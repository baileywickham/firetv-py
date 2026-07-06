"""ADB client for the Fire TV, wrapping androidtv's FireTVSync.

Design rule: nothing in here may raise out to HomeKit. Every public method
catches ADB/socket errors, reconnects once, retries once, then drops the
command with a warning (a lost keypress, not an outage).
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("firetv.adb")

# Android keycodes, sent as `input keyevent <n>`. HDMI* are the
# KEYCODE_TV_INPUT_HDMI_* codes the Omni's MediaTek firmware honors
# (verified against a Fire TV Omni QLED 43", Fire OS 8).
KEYCODES = {
    "UP": 19,
    "DOWN": 20,
    "LEFT": 21,
    "RIGHT": 22,
    "CENTER": 23,
    "BACK": 4,
    "HOME": 3,
    "MENU": 82,
    "PLAY_PAUSE": 85,
    "VOLUME_UP": 24,
    "VOLUME_DOWN": 25,
    "MUTE": 164,
    "WAKEUP": 224,
    "SLEEP": 223,
    "HDMI1": 243,
    "HDMI2": 244,
    "HDMI3": 245,
    "HDMI4": 246,
}

# Linux input-event keycodes for the `sendevent` fast path, used only for
# nav/volume/media keys that are latency-sensitive. Power and input switching
# (HOME, HDMI1-4, WAKEUP, SLEEP) deliberately stay on the Android keyevent
# path above since they must be maximally reliable, not fast.
LINUX_KEYCODES = {
    "UP": 103,
    "DOWN": 108,
    "LEFT": 105,
    "RIGHT": 106,
    "CENTER": 28,  # KEY_ENTER
    "BACK": 158,
    "MENU": 139,
    "PLAY_PAUSE": 164,
    "VOLUME_UP": 115,
    "VOLUME_DOWN": 114,
    "MUTE": 113,
}

# Capability strings a device must advertise to be usable as the d-pad
# sendevent target.
_REQUIRED_CAPS = ("KEY_UP", "KEY_DOWN", "KEY_LEFT", "KEY_RIGHT", "KEY_ENTER")

_ADD_DEVICE_RE = re.compile(r"^add device \d+:\s*(/dev/input/\S+)\s*$")

# Output from a failed `sendevent` invocation contains one of these
# substrings (case-insensitive); a clean run prints nothing.
_SENDEVENT_FAILURE_RE = re.compile(r"error|could not", re.IGNORECASE)


def parse_input_devices(getevent_output: str) -> list[tuple[str, str]]:
    """Parse ``getevent -pl`` output into ``(device_path, capabilities_text)`` pairs.

    Each "add device N: /dev/input/eventX" line starts a new device; every
    following line (until the next "add device" line) is treated as part of
    that device's indented capability block and concatenated for substring
    matching (e.g. "KEY_UP" in the block).
    """
    devices: list[tuple[str, list[str]]] = []
    for line in (getevent_output or "").splitlines():
        m = _ADD_DEVICE_RE.match(line)
        if m:
            devices.append((m.group(1), []))
        elif devices:
            devices[-1][1].append(line)
    return [(path, "\n".join(lines)) for path, lines in devices]


# Foreground-app substrings that mean the panel is on an HDMI input.
HDMI_APP_MARKERS = ("inputpreference", "tvinput")

# androidtv states that mean the screen is off.
OFF_STATES = ("off", "standby", None)

RECONNECT_MIN_S = 5
RECONNECT_MAX_S = 60


@dataclass(frozen=True)
class TVStatus:
    power: bool
    hdmi: bool


class FireTVClient:
    """Serialized, self-healing ADB access to one Fire TV."""

    def __init__(self, host: str, port: int, key_path: Path, tv=None, key_mode: str = "auto"):
        self._host = host
        self._port = port
        self._key_path = Path(key_path)
        self._tv = tv  # injected in tests; built lazily otherwise
        self._lock = threading.Lock()
        self._next_connect_at = 0.0
        self._backoff = RECONNECT_MIN_S
        self._key_mode = key_mode
        # sendevent fast-path state: discovered device path (None until the
        # first successful discovery) and a "dead" flag that, once set in
        # auto mode, permanently routes future presses to keyevent until the
        # connection is dropped and reconnected.
        self._fast_device: str | None = None
        self._fast_dead = False

    # -- connection ---------------------------------------------------------

    def _build_tv(self):
        from adb_shell.auth.keygen import keygen
        from androidtv.firetv.firetv_sync import FireTVSync

        self._key_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._key_path.exists():
            log.info("generating new ADB key at %s (TV will prompt once)", self._key_path)
            keygen(str(self._key_path))
        return FireTVSync(self._host, self._port, adbkey=str(self._key_path))

    def _note_connect_failure(self, now: float) -> None:
        self._next_connect_at = now + self._backoff
        self._backoff = min(self._backoff * 2, RECONNECT_MAX_S)

    def _ensure_connected(self) -> None:
        if self._tv is None:
            self._tv = self._build_tv()
        if getattr(self._tv, "available", False):
            return
        now = time.monotonic()
        if now < self._next_connect_at:
            raise ConnectionError("backing off before reconnect")
        try:
            # androidtv's ADBPythonSync.connect() catches exceptions internally
            # and returns a bool, so a False return is a failure too.
            ok = self._tv.adb_connect(auth_timeout_s=10.0)
        except Exception:
            self._note_connect_failure(now)
            raise
        if not ok or not getattr(self._tv, "available", False):
            self._note_connect_failure(now)
            raise ConnectionError("adb connect failed")
        self._backoff = RECONNECT_MIN_S

    def _run(self, label: str, fn):
        """connect -> fn(); on failure reconnect once and retry; else drop."""
        with self._lock:
            for attempt in (1, 2):
                try:
                    self._ensure_connected()
                    return fn()
                except Exception as e:  # noqa: BLE001 - boundary: must not raise
                    log.warning("%s failed (attempt %d): %s", label, attempt, e)
                    try:
                        self._tv.adb_close()
                    except Exception:  # noqa: BLE001
                        pass
                    self._fast_device = None
                    self._fast_dead = False
        return None

    # -- commands ------------------------------------------------------------

    def _keyevent(self, name: str) -> None:
        code = KEYCODES[name]
        self._run(f"keyevent {name}", lambda: self._tv.adb_shell(f"input keyevent {code}"))

    def _discover_input_device(self) -> str | None:
        """Find the /dev/input/eventX node that advertises d-pad keys."""
        output = self._tv.adb_shell("getevent -pl")
        if not output:
            return None
        for path, caps in parse_input_devices(output):
            if all(cap in caps for cap in _REQUIRED_CAPS):
                return path
        return None

    def _send_fast(self, name: str) -> None:
        """sendevent fast path for `name`; falls back to keyevent on failure."""

        def _do():
            if self._fast_device is None:
                device = self._discover_input_device()
                if device is None:
                    if self._key_mode == "auto":
                        log.warning(
                            "no sendevent-capable input device found; "
                            "falling back to keyevent for future presses"
                        )
                        self._fast_dead = True
                        code = KEYCODES[name]
                        self._tv.adb_shell(f"input keyevent {code}")
                    else:  # explicit "sendevent" mode: never silently degrade
                        log.warning(
                            "no sendevent-capable input device found; "
                            "dropping key %r (key_mode=sendevent)",
                            name,
                        )
                    return
                self._fast_device = device

            device = self._fast_device
            code = LINUX_KEYCODES[name]
            cmd = "; ".join(
                [
                    f"sendevent {device} 1 {code} 1",
                    f"sendevent {device} 0 0 0",
                    f"sendevent {device} 1 {code} 0",
                    f"sendevent {device} 0 0 0",
                ]
            )
            result = self._tv.adb_shell(cmd)
            if isinstance(result, str) and result.strip() and _SENDEVENT_FAILURE_RE.search(result):
                log.warning("sendevent failed for %r: %s", name, result.strip())
                if self._key_mode == "auto":
                    self._fast_dead = True
                    keycode = KEYCODES[name]
                    self._tv.adb_shell(f"input keyevent {keycode}")

        self._run(f"sendevent {name}", _do)

    def send_key(self, name: str) -> None:
        if name not in KEYCODES:
            log.warning("unknown key %r dropped", name)
            return
        if self._key_mode == "keyevent" or name not in LINUX_KEYCODES or self._fast_dead:
            self._keyevent(name)
        else:
            self._send_fast(name)

    def power(self, on: bool) -> None:
        self._keyevent("WAKEUP" if on else "SLEEP")

    def set_input(self, command: str) -> None:
        self.send_key(command)

    def status(self) -> TVStatus | None:
        def _update():
            state, current_app, *_rest = self._tv.update()
            power = state not in OFF_STATES
            app = current_app or ""
            hdmi = power and any(m in app for m in HDMI_APP_MARKERS)
            return TVStatus(power=power, hdmi=hdmi)

        return self._run("status", _update)
