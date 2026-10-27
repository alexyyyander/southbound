# Survey & Harness Deployment

This document is the staged path for homes without a full server:

1. Hostless survey: use an existing laptop or phone, no permanent host.
2. Minimal harness: Raspberry Pi / repurposed Pi, one protocol, one lamp, one sensor.
3. Expand only after the single-protocol loop works.
4. Camera and drone come later, with their own safety and bandwidth budgets.

## 1. Hostless survey

From the repository root on a laptop or a Pi:

```bash
python3 -m southbound survey --out data/survey.json --report data/SURVEY.local.md
```

The default survey is passive for discovery (ARP/mDNS/SSDP) and fingerprints
only hosts already found on the authorized LAN. `--sweep` opts into an ICMP
sweep. `--no-ports` skips service fingerprinting. `--ble` is an optional
advertisement scan and requires `pip install -e ".[survey]"`.

The JSON and Markdown report are written under `data/`, which is gitignored.
The report includes:

- IP devices and tiers;
- Wi-Fi bands/current channel/security, without storing visible SSIDs;
- radio evidence (Zigbee/Thread/Z-Wave/Sub-GHz/Bluetooth);
- host services and open ports;
- adapter readiness (MQTT, Home Assistant, camera, Zigbee, Thread, Matter, BLE, drone);
- recommended harness profile and next actions.

Run survey on the **target home network**, not on a random network. Current
repo data from another network is historical and must not be treated as
reachable.

## 2. Harness profiles

### Survey-only (no permanent host)

- Existing Mac/PC or phone.
- Use `southbound survey`, `discover`, `scan`.
- Good for a first visit, landlord/rental constraints, or deciding what to buy.

### Pi minimal (`pi_minimal`)

Best for the smallest always-on footprint:

- Raspberry Pi 4/5; a repurposed Pi 3 is acceptable for MQTT/Zigbee bridge only.
- Raspberry Pi OS Lite 64-bit, USB SSD if possible, 5V UPS.
- Mosquitto + Zigbee2MQTT + Southbound runtime.
- One USB Zigbee coordinator (SLZB-06M / ZBDongle-E).
- No Home Assistant, no Frigate, no LLM on the Pi.

A Pi Zero 2 W is fine as a serial/Zigbee bridge or survey node, but do not try
to run Home Assistant, Zigbee2MQTT, mosquitto, and an agent runtime together
on it. A used Pi 3 is best treated as a sensor/bridge node.

### Pi micro-hub (`pi_microhub`)

- Pi 5 (preferred) or Pi 4 with 4GB+ RAM and SSD.
- Home Assistant + Mosquitto + Zigbee2MQTT + Southbound.
- UPS and a small fanless case.
- Keep Frigate/camera recording and LLM inference on a separate N100/NAS/Jetson.

### Camera edge

- N100 mini PC, NAS, or Jetson Orin Nano.
- PoE switch + Reolink/Axis/UniFi/Amcrest camera.
- Frigate; local RTSP/ONVIF; structured observations only.

### Drone lab

- N100/Jetson companion + PX4/MAVLink or Tello EDU.
- VIO/UWB/AprilTag indoor positioning, prop guards, geofence, manual kill switch.
- Do not run autonomous flight around people or pets.

## 3. Pi minimal setup

Use Raspberry Pi OS Lite 64-bit and a wired Ethernet connection first; enable
Wi-Fi after the survey.

```bash
sudo apt update
sudo apt install -y git python3-venv python3-pip mosquitto mosquitto-clients avahi-utils bluez
git clone https://github.com/alexyyyander/southbound.git
cd southbound
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
```

Optional BLE survey:

```bash
pip install -e ".[survey]"
```

Run the first survey:

```bash
southbound survey --out data/survey.json --report data/SURVEY.local.md
```

Start with one Zigbee coordinator and one lamp, then verify:

- join only one device;
- `southbound tools` shows the expected capability;
- `southbound call ...` goes through policy/audit;
- the event/SSE log shows `command → state`;
- restart the Pi and confirm restoration.

## 4. Suggested hardware

| Part | Pi minimal | Pi micro-hub | Notes |
|---|---|---|---|
| Compute | Pi 4/5 or spare Pi 3 | Pi 5 / Pi 4 4GB+ | SSD, UPS, fanless case |
| Coordinator | SLZB-06M / ZBDongle-E | same | Do not use the Pi's Wi-Fi as a Zigbee radio |
| Radio | Zigbee first | Zigbee + Thread later | Add one protocol at a time |
| Power | 5V UPS | 5V UPS / PoE | Avoid SD-card corruption |
| Network | Ethernet first | Ethernet + IoT SSID/VLAN | Keep control plane off WAN |
| Storage | 16–32GB SSD | 64GB+ SSD | Logs and assets grow |
| Camera | none | none | Put Frigate on N100/NAS |
| Drone | none | none | Separate safety lab |

## 5. Service supervision

Example systemd unit (adjust user, path, and port):

```ini
[Unit]
Description=Southbound local runtime
After=network-online.target

[Service]
User=pi
WorkingDirectory=/home/pi/southbound
ExecStart=/home/pi/southbound/.venv/bin/southbound serve --port 8765
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Install with:

```bash
sudo cp deploy/systemd/southbound.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now southbound
```

The service binds `127.0.0.1` by default. Do not expose it to the WAN; use a
local reverse proxy only if you add authentication and TLS.

## 6. Acceptance checklist

- [ ] Survey report generated on the target network.
- [ ] Only authorized devices were probed.
- [ ] One protocol works end to end.
- [ ] One lamp/sensor action is verified by read-back.
- [ ] SSE/event log shows the command/state sequence.
- [ ] Pi reboot restores the runtime and device state.
- [ ] High-risk devices remain policy-gated and human-confirmed.
- [ ] No cloud dependency is required for the control path.

## 7. What not to do

- Do not run a broad port scan on a network you do not own.
- Do not use default credentials, deauth, packet capture, or exploit tools.
- Do not run HA, Frigate, LLM inference, and a drone companion on the same Pi.
- Do not connect a camera or drone without a privacy/safety review.
- Do not treat the old `192.168.0.0/23` inventory as the current network.
