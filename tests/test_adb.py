import logging
import time
from pathlib import Path

import pytest

from firetv.adb import KEYCODES, LINUX_KEYCODES, FireTVClient, TVStatus, parse_input_devices

# Realistic `getevent -pl` fixture built from live probe facts: event0 is a
# real device on the panel (MStar IR receiver) that lacks KEY_ENTER, so
# selection must skip it; event1 (MTK keypad) has the full d-pad + ENTER and
# must be the one picked.
GETEVENT_NO_MATCH = """add device 1: /dev/input/event0
  bus:      0003
  vendor    0000
  product   0000
  version   0000
  name:     "MStar Smart TV IR Receiver"
  events:
    KEY (0001): KEY_UP           KEY_DOWN         KEY_LEFT         KEY_RIGHT        KEY_BACK
  input props:
    <none>
"""

GETEVENT_TWO_DEVICES = (
    GETEVENT_NO_MATCH
    + """add device 2: /dev/input/event1
  bus:      0003
  vendor    0001
  product   0001
  version   0100
  name:     "MTK TV KEYPAD"
  events:
    KEY (0001): KEY_UP           KEY_DOWN         KEY_LEFT         KEY_RIGHT        KEY_ENTER        KEY_BACK         KEY_HOME         KEY_MENU
  input props:
    <none>
"""
)


class FakeTV:
    """Mimics the slice of FireTVSync that FireTVClient touches."""

    def __init__(self, getevent_output: str = GETEVENT_TWO_DEVICES):
        self.available = True
        self.shell_calls: list[str] = []
        self.connect_calls = 0
        self.fail_next_shell = 0  # raise on this many upcoming adb_shell calls
        self.update_result = ("playing", "com.amazon.tv.launcher", [], None)
        self.getevent_output = getevent_output
        self.sendevent_result: str | None = None  # returned by any sendevent call

    def adb_connect(self, auth_timeout_s=10.0):
        self.connect_calls += 1
        self.available = True
        return True

    def adb_close(self):
        self.available = False

    def adb_shell(self, cmd):
        if self.fail_next_shell > 0:
            self.fail_next_shell -= 1
            self.available = False
            raise ConnectionResetError("adb died")
        self.shell_calls.append(cmd)
        if cmd.startswith("getevent"):
            return self.getevent_output
        if cmd.startswith("sendevent"):
            return self.sendevent_result
        return None

    def update(self):
        return self.update_result


@pytest.fixture
def client(tmp_path: Path):
    """Default test client: explicit key_mode="keyevent" so existing
    shell_calls assertions (written before the sendevent fast path existed)
    aren't affected by fast-path discovery."""
    fake = FakeTV()
    c = FireTVClient("192.0.2.1", 5555, tmp_path / "adbkey", tv=fake, key_mode="keyevent")
    return c, fake


def test_send_key_sends_keyevent(client):
    c, fake = client
    c.send_key("CENTER")
    assert fake.shell_calls == [f"input keyevent {KEYCODES['CENTER']}"]


def test_send_key_unknown_name_is_dropped(client):
    c, fake = client
    c.send_key("NOT_A_KEY")
    assert fake.shell_calls == []


def test_power_on_off(client):
    c, fake = client
    c.power(True)
    c.power(False)
    assert fake.shell_calls == [
        f"input keyevent {KEYCODES['WAKEUP']}",
        f"input keyevent {KEYCODES['SLEEP']}",
    ]


def test_set_input_hdmi_and_home(client):
    c, fake = client
    c.set_input("HDMI1")
    c.set_input("HOME")
    assert fake.shell_calls == [
        f"input keyevent {KEYCODES['HDMI1']}",
        f"input keyevent {KEYCODES['HOME']}",
    ]


def test_status_maps_state_and_app(client):
    c, fake = client
    fake.update_result = ("idle", "com.amazon.tv.launcher", [], None)
    assert c.status() == TVStatus(power=True, hdmi=False)
    fake.update_result = ("playing", "com.amazon.tv.inputpreference.service", [], "HW4")
    assert c.status() == TVStatus(power=True, hdmi=True)
    fake.update_result = ("off", "", [], None)
    assert c.status() == TVStatus(power=False, hdmi=False)


def test_shell_failure_reconnects_and_retries(client):
    c, fake = client
    fake.fail_next_shell = 1
    c.send_key("UP")  # first attempt fails, reconnect, second succeeds
    assert fake.connect_calls >= 1
    assert fake.shell_calls == [f"input keyevent {KEYCODES['UP']}"]


def test_shell_failure_twice_is_dropped(client):
    c, fake = client
    fake.fail_next_shell = 2
    c.send_key("UP")  # both attempts fail -> dropped, no raise
    assert fake.shell_calls == []


class DownTV(FakeTV):
    """FakeTV whose adb_connect reports failure (returns False, stays unavailable),
    matching androidtv's ADBPythonSync.connect(), which never raises."""

    def __init__(self):
        super().__init__()
        self.available = False

    def adb_connect(self, auth_timeout_s=10.0):
        self.connect_calls += 1
        return False


def test_connect_false_engages_backoff(tmp_path: Path, caplog):
    fake = DownTV()
    c = FireTVClient("192.0.2.1", 5555, tmp_path / "adbkey", tv=fake, key_mode="keyevent")
    with caplog.at_level(logging.WARNING, logger="firetv.adb"):
        c.send_key("UP")  # must not raise
    assert fake.shell_calls == []  # command dropped, never reached the shell
    assert any("keyevent UP failed" in r.getMessage() for r in caplog.records)
    assert c._backoff == 10  # doubled from RECONNECT_MIN_S after one failure
    assert c._next_connect_at > time.monotonic()


def test_backoff_gates_reconnect_attempts(tmp_path: Path):
    fake = DownTV()
    c = FireTVClient("192.0.2.1", 5555, tmp_path / "adbkey", tv=fake, key_mode="keyevent")
    c.send_key("UP")
    # First call: attempt 1 tries adb_connect (fails, opens backoff window);
    # attempt 2 lands inside the window and is gated before connecting.
    calls_after_first = fake.connect_calls
    assert calls_after_first == 1
    c.send_key("UP")  # entirely within the backoff window
    assert fake.connect_calls == calls_after_first  # no new adb_connect
    assert fake.shell_calls == []


# -- sendevent fast path -----------------------------------------------------


def test_parse_input_devices_basic():
    devices = parse_input_devices(GETEVENT_TWO_DEVICES)
    assert [path for path, _caps in devices] == ["/dev/input/event0", "/dev/input/event1"]
    path0, caps0 = devices[0]
    assert "KEY_UP" in caps0
    assert "KEY_ENTER" not in caps0
    path1, caps1 = devices[1]
    assert all(k in caps1 for k in ("KEY_UP", "KEY_DOWN", "KEY_LEFT", "KEY_RIGHT", "KEY_ENTER"))


def test_parse_input_devices_no_match():
    devices = parse_input_devices(GETEVENT_NO_MATCH)
    assert len(devices) == 1
    path, caps = devices[0]
    assert path == "/dev/input/event0"
    assert "KEY_ENTER" not in caps


def test_parse_input_devices_empty_output():
    assert parse_input_devices("") == []
    assert parse_input_devices(None) == []  # type: ignore[arg-type]


def _sendevent_chain(device: str, code: int) -> str:
    return "; ".join(
        [
            f"sendevent {device} 1 {code} 1",
            f"sendevent {device} 0 0 0",
            f"sendevent {device} 1 {code} 0",
            f"sendevent {device} 0 0 0",
        ]
    )


def test_auto_mode_sends_sendevent_chain_for_up(tmp_path: Path):
    fake = FakeTV()
    c = FireTVClient("192.0.2.1", 5555, tmp_path / "adbkey", tv=fake, key_mode="auto")
    c.send_key("UP")
    assert fake.shell_calls == [
        "getevent -pl",
        _sendevent_chain("/dev/input/event1", LINUX_KEYCODES["UP"]),
    ]
    assert c._fast_device == "/dev/input/event1"
    assert c._fast_dead is False

    # second press reuses the discovered device without re-running getevent
    c.send_key("DOWN")
    assert fake.shell_calls[-1] == _sendevent_chain("/dev/input/event1", LINUX_KEYCODES["DOWN"])
    assert fake.shell_calls.count("getevent -pl") == 1


def test_hdmi1_and_wakeup_stay_on_keyevent_in_auto_mode(tmp_path: Path):
    fake = FakeTV()
    c = FireTVClient("192.0.2.1", 5555, tmp_path / "adbkey", tv=fake, key_mode="auto")
    c.set_input("HDMI1")
    c.power(True)  # WAKEUP
    assert fake.shell_calls == [
        f"input keyevent {KEYCODES['HDMI1']}",
        f"input keyevent {KEYCODES['WAKEUP']}",
    ]
    assert "getevent -pl" not in fake.shell_calls


def test_key_mode_keyevent_never_runs_getevent(tmp_path: Path):
    fake = FakeTV()
    c = FireTVClient("192.0.2.1", 5555, tmp_path / "adbkey", tv=fake, key_mode="keyevent")
    c.send_key("UP")
    c.send_key("VOLUME_UP")
    assert fake.shell_calls == [
        f"input keyevent {KEYCODES['UP']}",
        f"input keyevent {KEYCODES['VOLUME_UP']}",
    ]
    assert "getevent -pl" not in fake.shell_calls


def test_auto_mode_discovery_failure_falls_back_and_does_not_retry(tmp_path: Path):
    fake = FakeTV(getevent_output=GETEVENT_NO_MATCH)  # no device has KEY_ENTER
    c = FireTVClient("192.0.2.1", 5555, tmp_path / "adbkey", tv=fake, key_mode="auto")
    c.send_key("UP")
    assert fake.shell_calls == ["getevent -pl", f"input keyevent {KEYCODES['UP']}"]
    assert c._fast_dead is True

    # next press must not re-run discovery
    c.send_key("DOWN")
    assert fake.shell_calls == [
        "getevent -pl",
        f"input keyevent {KEYCODES['UP']}",
        f"input keyevent {KEYCODES['DOWN']}",
    ]


def test_sendevent_mode_discovery_failure_drops_press(tmp_path: Path):
    fake = FakeTV(getevent_output=GETEVENT_NO_MATCH)
    c = FireTVClient("192.0.2.1", 5555, tmp_path / "adbkey", tv=fake, key_mode="sendevent")
    c.send_key("UP")
    # explicit sendevent mode never silently degrades to keyevent
    assert fake.shell_calls == ["getevent -pl"]
    assert c._fast_dead is False


def test_sendevent_failure_output_triggers_keyevent_fallback_in_auto_mode(tmp_path: Path):
    fake = FakeTV()
    fake.sendevent_result = "could not open /dev/input/event1"
    c = FireTVClient("192.0.2.1", 5555, tmp_path / "adbkey", tv=fake, key_mode="auto")
    c.send_key("UP")
    assert fake.shell_calls == [
        "getevent -pl",
        _sendevent_chain("/dev/input/event1", LINUX_KEYCODES["UP"]),
        f"input keyevent {KEYCODES['UP']}",
    ]
    assert c._fast_dead is True


def test_sendevent_failure_output_in_sendevent_mode_just_logs(tmp_path: Path):
    fake = FakeTV()
    fake.sendevent_result = "ERROR: could not deliver"
    c = FireTVClient("192.0.2.1", 5555, tmp_path / "adbkey", tv=fake, key_mode="sendevent")
    c.send_key("UP")
    assert fake.shell_calls == [
        "getevent -pl",
        _sendevent_chain("/dev/input/event1", LINUX_KEYCODES["UP"]),
    ]
    assert c._fast_dead is False


def test_fast_path_state_resets_on_reconnect(tmp_path: Path):
    fake = FakeTV()
    c = FireTVClient("192.0.2.1", 5555, tmp_path / "adbkey", tv=fake, key_mode="auto")
    c.send_key("UP")
    assert c._fast_device == "/dev/input/event1"

    fake.fail_next_shell = 1  # force the next command to fail -> reconnect
    c.send_key("DOWN")
    # after a failure+reconnect cycle, discovery must run again
    assert fake.shell_calls.count("getevent -pl") == 2
