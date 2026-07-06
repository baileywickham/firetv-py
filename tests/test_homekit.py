from firetv.adb import KEYCODES, TVStatus
from firetv.homekit import REMOTE_KEY_MAP, FireTVAccessory, active_input_id

INPUTS = [
    ("Fire TV", "HOME"),
    ("HDMI 1", "HDMI1"),
    ("HDMI 2", "HDMI2"),
]


class FakeChar:
    def __init__(self, value):
        self.value = value

    def set_value(self, value):
        self.value = value


class FakeClient:
    def __init__(self, status):
        self._status = status

    def status(self):
        return self._status


class FakeAccessory:
    """Duck-typed stand-in for FireTVAccessory exposing only the attributes
    _poll_once touches, so the poll logic can be tested without spinning up
    a real pyhap AccessoryDriver."""

    def __init__(self, status, current_input_id=1):
        self.client = FakeClient(status)
        self.char_active = FakeChar(0)
        self.char_input = FakeChar(current_input_id)
        self.inputs = INPUTS


def test_poll_once_unreachable_sets_inactive():
    acc = FakeAccessory(status=None)
    FireTVAccessory._poll_once(acc)
    assert acc.char_active.value == 0


def test_poll_once_power_on_sets_active_and_input():
    acc = FakeAccessory(status=TVStatus(power=True, hdmi=False), current_input_id=2)
    FireTVAccessory._poll_once(acc)
    assert acc.char_active.value == 1
    assert acc.char_input.value == 1  # non-HDMI input wins in Fire TV mode


def test_poll_once_power_off_leaves_input_untouched():
    acc = FakeAccessory(status=TVStatus(power=False, hdmi=False), current_input_id=2)
    FireTVAccessory._poll_once(acc)
    assert acc.char_active.value == 0
    assert acc.char_input.value == 2  # untouched: no need to update input while off


def test_remote_key_map_targets_real_keys():
    # HAP RemoteKey values: 4-7 arrows, 8 select, 9 back, 11 play/pause, 15 info
    assert set(REMOTE_KEY_MAP) == {4, 5, 6, 7, 8, 9, 11, 15}
    assert all(v in KEYCODES for v in REMOTE_KEY_MAP.values())
    assert REMOTE_KEY_MAP[8] == "CENTER"
    assert REMOTE_KEY_MAP[15] == "MENU"


def test_active_input_fire_tv_mode():
    # not on HDMI -> the (sole) non-HDMI input wins
    s = TVStatus(power=True, hdmi=False)
    assert active_input_id(s, current_id=2, inputs=INPUTS) == 1  # ids are 1-based


def test_active_input_hdmi_keeps_last_hdmi_selection():
    # ADB can't tell which HDMI port; keep the current one if it's HDMI
    s = TVStatus(power=True, hdmi=True)
    assert active_input_id(s, current_id=3, inputs=INPUTS) == 3


def test_active_input_hdmi_from_fire_tv_picks_first_hdmi():
    s = TVStatus(power=True, hdmi=True)
    assert active_input_id(s, current_id=1, inputs=INPUTS) == 2
