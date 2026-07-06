from pathlib import Path

import pytest

from firetv.adb import KEYCODES, FireTVClient, TVStatus


class FakeTV:
    """Mimics the slice of FireTVSync that FireTVClient touches."""

    def __init__(self):
        self.available = True
        self.shell_calls: list[str] = []
        self.connect_calls = 0
        self.fail_next_shell = 0  # raise on this many upcoming adb_shell calls
        self.update_result = ("playing", "com.amazon.tv.launcher", [], None)

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

    def update(self):
        return self.update_result


@pytest.fixture
def client(tmp_path: Path):
    fake = FakeTV()
    c = FireTVClient("192.0.2.1", 5555, tmp_path / "adbkey", tv=fake)
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
