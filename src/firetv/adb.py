"""ADB client for the Fire TV, wrapping androidtv's FireTVSync.

Design rule: nothing in here may raise out to HomeKit. Every public method
catches ADB/socket errors, reconnects once, retries once, then drops the
command with a warning (a lost keypress, not an outage).
"""
from __future__ import annotations

import logging
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

    def __init__(self, host: str, port: int, key_path: Path, tv=None):
        self._host = host
        self._port = port
        self._key_path = Path(key_path)
        self._tv = tv  # injected in tests; built lazily otherwise
        self._lock = threading.Lock()
        self._next_connect_at = 0.0
        self._backoff = RECONNECT_MIN_S

    # -- connection ---------------------------------------------------------

    def _build_tv(self):
        from adb_shell.auth.keygen import keygen
        from androidtv.firetv.firetv_sync import FireTVSync

        self._key_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._key_path.exists():
            log.info("generating new ADB key at %s (TV will prompt once)", self._key_path)
            keygen(str(self._key_path))
        return FireTVSync(self._host, self._port, adbkey=str(self._key_path))

    def _ensure_connected(self) -> None:
        if self._tv is None:
            self._tv = self._build_tv()
        if getattr(self._tv, "available", False):
            return
        now = time.monotonic()
        if now < self._next_connect_at:
            raise ConnectionError("backing off before reconnect")
        try:
            self._tv.adb_connect(auth_timeout_s=10.0)
            self._backoff = RECONNECT_MIN_S
        except Exception:
            self._next_connect_at = now + self._backoff
            self._backoff = min(self._backoff * 2, RECONNECT_MAX_S)
            raise

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
        return None

    # -- commands ------------------------------------------------------------

    def _keyevent(self, name: str) -> None:
        code = KEYCODES[name]
        self._run(f"keyevent {name}", lambda: self._tv.adb_shell(f"input keyevent {code}"))

    def send_key(self, name: str) -> None:
        if name not in KEYCODES:
            log.warning("unknown key %r dropped", name)
            return
        self._keyevent(name)

    def power(self, on: bool) -> None:
        self._keyevent("WAKEUP" if on else "SLEEP")

    def set_input(self, command: str) -> None:
        self.send_key(command)

    def status(self) -> TVStatus | None:
        def _update():
            state, current_app, _running = self._tv.update()
            power = state not in OFF_STATES
            app = current_app or ""
            hdmi = power and any(m in app for m in HDMI_APP_MARKERS)
            return TVStatus(power=power, hdmi=hdmi)

        return self._run("status", _update)
