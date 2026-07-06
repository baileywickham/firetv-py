from firetv.adb import KEYCODES, TVStatus
from firetv.homekit import REMOTE_KEY_MAP, active_input_id

INPUTS = [
    ("Fire TV", "HOME"),
    ("HDMI 1", "HDMI1"),
    ("HDMI 2", "HDMI2"),
]


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
