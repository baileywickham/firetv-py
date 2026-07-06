"""Environment-variable configuration.

All deployment-specific values (TV address, input list, state dir) come from
the environment so the repo itself stays generic.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_INPUTS = (
    "Fire TV=HOME,HDMI 1=HDMI1,HDMI 2=HDMI2,HDMI 3=HDMI3,HDMI 4=HDMI4"
)

VALID_KEY_MODES = {"auto", "sendevent", "keyevent"}


def parse_inputs(s: str) -> list[tuple[str, str]]:
    """Parse ``label=command`` pairs from a comma-separated string."""
    inputs: list[tuple[str, str]] = []
    for part in s.split(","):
        label, sep, command = part.partition("=")
        if not sep or not label.strip() or not command.strip():
            raise ValueError(f"bad FIRETV_INPUTS entry: {part!r} (want label=command)")
        inputs.append((label.strip(), command.strip()))
    return inputs


@dataclass
class Config:
    host: str
    port: int
    name: str
    inputs: list[tuple[str, str]]
    state_dir: Path
    hap_port: int
    poll_seconds: int
    key_mode: str

    @classmethod
    def from_env(cls) -> "Config":
        host = os.environ.get("FIRETV_HOST")
        if not host:
            raise SystemExit("error: FIRETV_HOST is required (the TV's IP address)")
        key_mode = os.environ.get("FIRETV_KEY_MODE", "auto")
        if key_mode not in VALID_KEY_MODES:
            raise SystemExit(
                f"error: FIRETV_KEY_MODE={key_mode!r} invalid, "
                f"must be one of {sorted(VALID_KEY_MODES)}"
            )
        return cls(
            host=host,
            port=int(os.environ.get("FIRETV_PORT", "5555")),
            name=os.environ.get("FIRETV_NAME", "Fire TV"),
            inputs=parse_inputs(os.environ.get("FIRETV_INPUTS", DEFAULT_INPUTS)),
            state_dir=Path(
                os.environ.get("FIRETV_STATE_DIR", "~/.config/firetv")
            ).expanduser(),
            hap_port=int(os.environ.get("FIRETV_HAP_PORT", "51828")),
            poll_seconds=int(os.environ.get("FIRETV_POLL_SECONDS", "15")),
            key_mode=key_mode,
        )
