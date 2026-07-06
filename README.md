# firetv-py

Expose an Amazon Fire TV smart TV as a **native HomeKit Television
accessory** — power, volume, HDMI/Fire TV input switching, and full d-pad
navigation from the Apple TV Remote in iOS Control Center. Talks to the TV
over network ADB; no Amazon cloud, no Home Assistant.

Tested against a Fire TV Omni QLED 43" (Fire OS 8). Any Fire TV with
network ADB should work for power/nav/volume; the HDMI input keycodes
(243-246) are TV-panel specific.

## TV setup (one-time)

1. Connect the TV to your network.
2. Unhide developer options: Settings -> My Fire TV -> About -> highlight the
   TV name and press **Select 7 times**.
3. My Fire TV -> Developer Options -> **ADB Debugging: ON**.
4. Note the TV's IP (Settings -> Network).

## Run

```bash
uv sync
FIRETV_HOST=<tv-ip> uv run firetv-homekit
```

First run generates an ADB key and the TV shows an "Allow USB debugging?"
prompt — accept with "always allow". A HomeKit QR/setup code prints to
stdout; add it in the iOS Home app. TV accessories are published standalone
(unbridged) because iOS only offers the Control Center remote for those.

## Configuration (env vars)

| Var | Default | Meaning |
| --- | --- | --- |
| `FIRETV_HOST` | (required) | TV IP address |
| `FIRETV_PORT` | `5555` | ADB port |
| `FIRETV_NAME` | `Fire TV` | HomeKit display name |
| `FIRETV_INPUTS` | `Fire TV=HOME,HDMI 1=HDMI1,...,HDMI 4=HDMI4` | comma-separated `label=command` |
| `FIRETV_STATE_DIR` | `~/.config/firetv` | ADB key + HomeKit pairing state |
| `FIRETV_HAP_PORT` | `51828` | HAP listen port |
| `FIRETV_POLL_SECONDS` | `15` | state poll interval |
| `FIRETV_LOG_LEVEL` | `INFO` | logging level |
| `FIRETV_KEY_MODE` | `auto` | key injection: sendevent fast path w/ fallback (auto), sendevent, or keyevent |

## Container

```bash
docker build -t firetv-py .
docker run -e FIRETV_HOST=<tv-ip> -v firetv-state:/data --network host firetv-py
```

Host networking is required for HomeKit mDNS. For Kubernetes, run it as a
single-replica Deployment with `hostNetwork: true` (HomeKit mDNS needs the
host network), a small PVC mounted at `/data`, and `strategy: Recreate`
(one HomeKit identity at a time).

## Known limitations

- ADB reports HDMI-vs-FireTV mode but not *which* HDMI port, so the input
  tile can show the wrong HDMI number after physical-remote input changes.
- The TV doesn't report absolute volume over ADB — volume is up/down/mute
  only (which is all the Control Center remote needs).
- Text entry from the iOS keyboard is not part of HomeKit's TV profile.
- **Wake-from-standby needs the TV's network to stay up while asleep.** Fire
  TV panels drop into a deep Low Power Mode ~15 minutes after sleeping, which
  kills adbd and makes HomeKit power-on impossible until the TV is woken
  another way. Amazon provides no direct toggle, but enabling
  **Settings → Display & Sound → Power Controls → "Voice Commands When TV
  Screen is Off"** (a.k.a. Alexa Anytime, Omni panels) keeps the network
  stack alive in standby — verified reachable for 35+ minutes of sleep on an
  Omni QLED with the setting on, vs. ~15 minutes without. Costs a few watts
  and a hot microphone; the bridge otherwise reconnects automatically
  whenever the TV comes back.
- Each `input keyevent` press costs ~1s on the TV side (Fire OS spawns a
  fresh `input` process per injected keyevent). The `sendevent` fast path
  (`FIRETV_KEY_MODE=auto`, the default) writes raw Linux input events
  directly to the TV's input device instead, bringing nav/volume presses to
  ~0.2s on supported panels, with automatic fallback to `input keyevent` if
  no suitable input device can be found. Power and input switching
  (HOME/HDMI/WAKEUP/SLEEP) always go through `input keyevent` since they're
  latency-insensitive and must be maximally reliable.
