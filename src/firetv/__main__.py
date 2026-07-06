"""Entrypoint: config -> ADB client -> HomeKit accessory -> HAP driver."""
from __future__ import annotations

import logging
import os
import signal

from pyhap.accessory_driver import AccessoryDriver

from .adb import FireTVClient
from .config import Config
from .homekit import FireTVAccessory

log = logging.getLogger("firetv")


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("FIRETV_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = Config.from_env()
    cfg.state_dir.mkdir(parents=True, exist_ok=True)

    client = FireTVClient(cfg.host, cfg.port, cfg.state_dir / "adbkey")
    first = client.status()
    log.info("initial TV status: %s (accessory publishes regardless)", first)

    driver = AccessoryDriver(
        port=cfg.hap_port, persist_file=str(cfg.state_dir / "homekit.state")
    )
    driver.add_accessory(
        FireTVAccessory(driver, cfg.name, client, cfg.inputs, cfg.poll_seconds)
    )
    signal.signal(signal.SIGTERM, driver.signal_handler)
    log.info("starting HAP driver on port %d", cfg.hap_port)
    driver.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
