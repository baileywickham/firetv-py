"""HomeKit Television accessory backed by FireTVClient.

Published standalone (not behind a Bridge): iOS only offers the Control
Center remote for unbridged Television accessories.
"""
from __future__ import annotations

import logging

from pyhap.accessory import Accessory
from pyhap.const import CATEGORY_TELEVISION

from .adb import FireTVClient, TVStatus

log = logging.getLogger("firetv.homekit")

# HAP requires a non-empty x.y.z FirmwareRevision on every accessory; iOS
# hubs that read an empty one drop the connection right after subscribing.
FIRMWARE_REVISION = "1.0.0"

# HAP RemoteKey characteristic value -> KEYCODES name.
REMOTE_KEY_MAP = {
    4: "UP",
    5: "DOWN",
    6: "LEFT",
    7: "RIGHT",
    8: "CENTER",
    9: "BACK",
    11: "PLAY_PAUSE",
    15: "MENU",  # "information" button
}

# HAP InputSourceType values.
SOURCE_TYPE_HOME_SCREEN = 1
SOURCE_TYPE_HDMI = 3


def _is_hdmi(command: str) -> bool:
    return command.upper().startswith("HDMI")


def active_input_id(status: TVStatus, current_id: int, inputs) -> int:
    """Which 1-based input Identifier should be shown for this TV status.

    ADB reveals HDMI-vs-FireTV mode but not the HDMI port number, so when in
    HDMI mode we keep the currently selected input if it is already an HDMI
    one, else fall back to the first HDMI input.
    """
    ids_hdmi = [i for i, (_l, cmd) in enumerate(inputs, start=1) if _is_hdmi(cmd)]
    ids_other = [i for i, (_l, cmd) in enumerate(inputs, start=1) if not _is_hdmi(cmd)]
    if status.hdmi:
        if current_id in ids_hdmi:
            return current_id
        return ids_hdmi[0] if ids_hdmi else current_id
    return ids_other[0] if ids_other else current_id


class FireTVAccessory(Accessory):
    category = CATEGORY_TELEVISION

    def __init__(self, driver, name: str, client: FireTVClient, inputs, poll_seconds: int):
        super().__init__(driver, name)
        self.client = client
        self.inputs = list(inputs)
        self._poll_seconds = poll_seconds

        self.set_info_service(
            firmware_revision=FIRMWARE_REVISION,
            manufacturer="baileywickham",
            model="firetv-py",
            serial_number="firetv-1",
        )

        tv = self.add_preload_service(
            "Television",
            chars=["Active", "ActiveIdentifier", "ConfiguredName", "SleepDiscoveryMode", "RemoteKey"],
        )
        self.char_active = tv.configure_char("Active", setter_callback=self._set_active, value=0)
        self.char_input = tv.configure_char(
            "ActiveIdentifier", setter_callback=self._set_input, value=1
        )
        tv.configure_char("ConfiguredName", value=name)
        tv.configure_char("SleepDiscoveryMode", value=1)  # always discoverable
        tv.configure_char("RemoteKey", setter_callback=self._remote_key)

        for i, (label, command) in enumerate(self.inputs, start=1):
            source = self.add_preload_service(
                "InputSource",
                chars=[
                    "Identifier",
                    "ConfiguredName",
                    "InputSourceType",
                    "IsConfigured",
                    "CurrentVisibilityState",
                ],
            )
            source.configure_char("Identifier", value=i)
            source.configure_char("ConfiguredName", value=label)
            source.configure_char(
                "InputSourceType",
                value=SOURCE_TYPE_HDMI if _is_hdmi(command) else SOURCE_TYPE_HOME_SCREEN,
            )
            source.configure_char("IsConfigured", value=1)
            source.configure_char("CurrentVisibilityState", value=0)  # shown
            tv.add_linked_service(source)

        speaker = self.add_preload_service(
            "TelevisionSpeaker", chars=["Active", "Mute", "VolumeControlType", "VolumeSelector"]
        )
        speaker.configure_char("Active", value=1)
        speaker.configure_char("VolumeControlType", value=1)  # relative
        speaker.configure_char("Mute", setter_callback=self._set_mute, value=False)
        speaker.configure_char("VolumeSelector", setter_callback=self._volume)
        tv.add_linked_service(speaker)

    # -- HomeKit -> TV (HAP thread) ------------------------------------------

    def _set_active(self, value: int) -> None:
        self.client.power(bool(value))

    def _set_input(self, identifier: int) -> None:
        if identifier < 1:
            log.warning("input identifier %s out of range", identifier)
            return
        try:
            _label, command = self.inputs[identifier - 1]
        except IndexError:
            log.warning("input identifier %s out of range", identifier)
            return
        self.client.set_input(command)

    def _remote_key(self, value: int) -> None:
        name = REMOTE_KEY_MAP.get(value)
        if name is None:
            log.info("unmapped RemoteKey %s dropped", value)
            return
        self.client.send_key(name)

    def _volume(self, value: int) -> None:  # 0 = up, 1 = down
        self.client.send_key("VOLUME_UP" if value == 0 else "VOLUME_DOWN")

    def _set_mute(self, value: bool) -> None:
        self.client.send_key("MUTE")

    # -- TV -> HomeKit (poll loop) ---------------------------------------------

    async def run(self) -> None:
        """Poll the TV every poll_seconds and mirror state into HomeKit.

        Never raises: an escaping exception would kill the task and freeze
        HomeKit at stale state.
        """
        from pyhap.util import event_wait

        while not await event_wait(self.driver.aio_stop_event, self._poll_seconds):
            self._poll_once()

    def _poll_once(self) -> None:
        try:
            status = self.client.status()
            if status is None:
                self.char_active.set_value(0)  # unreachable -> off (adbd stops when TV sleeps)
                return
            self.char_active.set_value(1 if status.power else 0)
            if status.power:
                self.char_input.set_value(
                    active_input_id(status, self.char_input.value, self.inputs)
                )
        except Exception as e:  # noqa: BLE001
            log.warning("poll failed: %s", e)
