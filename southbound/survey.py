"""Hostless local-network survey and harness readiness.

This is the "survey first" layer: it inventories the authorized LAN, detects
Wi-Fi/BLE/radio capabilities when available, fingerprints only discovered
hosts, and turns the result into an integration-readiness report. It does not
perform credential attempts, authentication bypass, exploit attempts, or
scanning of networks the operator does not own.

The default path is passive: ARP, mDNS, SSDP, local radio capability checks.
`--sweep` and port fingerprinting are explicit opt-ins; when enabled, ports are
scanned only on hosts already discovered on the authorized LAN.
"""

from __future__ import annotations

import asyncio
import json
import platform
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from . import deep_scan, discover
from .paths import DATA_DIR

DEFAULT_OUT = DATA_DIR / "survey.json"
DEFAULT_REPORT = DATA_DIR / "SURVEY.local.md"

RADIO_TOKENS = {
    "zigbee": (
        "zigbee",
        "slzb",
        "zbdongle",
        "skyconnect",
        "zbt-1",
        "conbee",
        "cc2652",
        "coordinator",
    ),
    "thread": ("thread", "openthread", "meshcop", "zbt-1"),
    "zwave": ("z-wave", "zwave", "aeotec", "z-stick", "zooz"),
    "subghz": ("rtl-sdr", "rtlsdr", "hackrf", "airspy", "sdr"),
}

PROFILES = {
    "survey_only": {
        "title": "Survey-only",
        "host": "现有 Mac/PC；无需常驻主机",
        "hardware": "笔记本 + 手机 + 可选 USB radio",
        "scope": "发现、清单、接入评估",
    },
    "micro_hub": {
        "title": "Micro-hub",
        "host": "Home Assistant Green / N100 迷你主机 / Raspberry Pi 5",
        "hardware": "HA + Mosquitto + Zigbee coordinator",
        "scope": "常驻自动化、MQTT、Zigbee、单协议接入",
    },
    "pi_minimal": {
        "title": "Pi minimal",
        "host": "Raspberry Pi 4/5；闲置/重构的 Pi 3 也可做 MQTT/Zigbee bridge",
        "hardware": "Pi OS Lite + Mosquitto + Zigbee2MQTT + USB coordinator",
        "scope": "最小常驻桥：MQTT/Zigbee、Southbound runtime；不跑 HA、不跑 Frigate",
    },
    "pi_microhub": {
        "title": "Pi micro-hub",
        "host": "Raspberry Pi 5 / Pi 4（4GB+，SSD）",
        "hardware": "HA + Mosquitto + Zigbee coordinator + UPS",
        "scope": "常驻自动化；摄像头/Frigate 建议放到 N100/NAS",
    },
    "camera_edge": {
        "title": "Camera edge",
        "host": "N100 / NAS / Jetson Orin Nano",
        "hardware": "PoE 交换机 + RTSP/ONVIF 摄像头 + Frigate",
        "scope": "本地视觉、快照/片段、结构化 observation",
    },
    "drone_lab": {
        "title": "Drone lab",
        "host": "N100 / Jetson + 独立飞控链路",
        "hardware": "Tello EDU 或 PX4/MAVLink + VIO/UWB + 护罩",
        "scope": "受控飞行任务；人工急停与电子围栏",
    },
}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _run(cmd: list[str], timeout: float = 8.0) -> str:
    result = discover.run_cmd(cmd, timeout)
    return (result.stdout or "") + (result.stderr or "")


def _serial_ports() -> list[str]:
    path = Path("/dev")
    if not path.exists():
        return []
    if platform.system() == "Linux":
        patterns = ("ttyUSB*", "ttyACM*", "ttyAMA*")
        ports = []
        for pattern in patterns:
            ports.extend(str(item) for item in path.glob(pattern))
        return sorted(set(ports))
    return sorted(
        str(item)
        for item in path.iterdir()
        if item.name.startswith("tty.") and item.name != "tty.debug-console"
    )


def _bluetooth_state() -> str:
    if platform.system() == "Linux":
        text = _run(["bluetoothctl", "show"], 4)
        match = re.search(r"Powered:\s*(yes|no)", text, re.I)
        if match:
            return "On" if match.group(1).lower() == "yes" else "Off"
        return "unknown"
    text = _run(["system_profiler", "SPBluetoothDataType"], 8)
    match = re.search(r"State:\s*(\w+)", text)
    return match.group(1) if match else "unknown"


def detect_radios() -> dict[str, Any]:
    """Detect radio/coordinator evidence without joining any radio network."""
    serial = _serial_ports()
    if platform.system() == "Linux":
        usb = _run(["lsusb"], 8)
    else:
        usb = _run(["system_profiler", "SPUSBDataType"], 10)
    haystack = "\n".join([usb, *serial]).lower()
    matches = {
        key: [token for token in tokens if token in haystack]
        for key, tokens in RADIO_TOKENS.items()
    }
    bluetooth = _bluetooth_state()
    return {
        "bluetooth": {
            "state": bluetooth,
            "available": bluetooth.lower() == "on",
        },
        "serial_ports": serial,
        "zigbee": {
            "status": "detected" if matches["zigbee"] else "needs_radio",
            "evidence": matches["zigbee"],
        },
        "thread": {
            "status": "detected" if matches["thread"] else "needs_radio",
            "evidence": matches["thread"],
        },
        "zwave": {
            "status": "detected" if matches["zwave"] else "needs_radio",
            "evidence": matches["zwave"],
        },
        "subghz": {
            "status": "detected" if matches["subghz"] else "needs_radio",
            "evidence": matches["subghz"],
        },
    }


def parse_wifi_output(text: str) -> dict[str, Any]:
    """Parse macOS `system_profiler SPAirPortDataType` without leaking SSIDs."""
    current: dict[str, Any] = {}
    others: list[dict[str, Any]] = []
    section: str | None = None
    active: dict[str, Any] | None = None

    def band_for(channel: str) -> str:
        if "(6GHz" in channel:
            return "6GHz"
        if "(5GHz" in channel:
            return "5GHz"
        if "(2GHz" in channel:
            return "2.4GHz"
        return "unknown"

    for raw in text.splitlines():
        indent = len(raw) - len(raw.lstrip())
        line = raw.strip()
        if line == "Current Network Information:":
            section = "current"
            continue
        if line == "Other Local Wi-Fi Networks:":
            section = "other"
            active = None
            continue
        if section is None:
            continue
        if indent == 12 and line.endswith(":"):
            name = line[:-1].strip()
            active = {"ssid_visible": not name.startswith("<")}
            if section == "current":
                current = active
            else:
                others.append(active)
            continue
        if active is None:
            continue
        if line.startswith("Channel:"):
            active["channel"] = line.split(":", 1)[1].strip()
            active["band"] = band_for(active["channel"])
        elif line.startswith("Security:"):
            active["security"] = line.split(":", 1)[1].strip()
        elif line.startswith("Signal / Noise:"):
            active["signal_noise"] = line.split(":", 1)[1].strip()
        elif line.startswith("PHY Mode:"):
            active["phy"] = line.split(":", 1)[1].strip()

    bands: dict[str, int] = {}
    for item in [current, *others]:
        band = item.get("band")
        if band:
            bands[band] = bands.get(band, 0) + 1
    return {
        "available": True,
        "current": current,
        "other_count": len(others),
        "other_networks": [
            {key: item.get(key) for key in ("band", "channel", "security", "phy")}
            for item in others
        ],
        "bands": bands,
        "ssid_visibility": (
            "redacted-by-os" if not current.get("ssid_visible", False) else "visible"
        ),
    }


def _split_nmcli(line: str) -> list[str]:
    fields = re.split(r"(?<!\\):", line)
    return [field.replace("\\:", ":").replace("\\\\", "\\") for field in fields]


def _band_from_freq(freq: str) -> str:
    try:
        mhz = int(freq)
    except ValueError:
        return "unknown"
    if mhz < 3000:
        return "2.4GHz"
    if mhz < 5925:
        return "5GHz"
    return "6GHz"


def parse_nmcli_wifi(text: str) -> dict[str, Any]:
    """Parse `nmcli -t ... device wifi list` without retaining SSIDs."""
    current: dict[str, Any] = {}
    others: list[dict[str, Any]] = []
    for line in text.splitlines():
        fields = _split_nmcli(line)
        if len(fields) < 6:
            continue
        in_use, _ssid, signal, security, freq, channel = fields[:6]
        item = {
            "band": _band_from_freq(freq),
            "channel": channel,
            "security": security or "open",
            "signal": signal,
        }
        if in_use.strip() == "*":
            current = item
        else:
            others.append(item)
    bands: dict[str, int] = {}
    for item in [current, *others]:
        band = item.get("band")
        if band:
            bands[band] = bands.get(band, 0) + 1
    return {
        "available": bool(current or others),
        "current": current,
        "other_count": len(others),
        "other_networks": [
            {key: item.get(key) for key in ("band", "channel", "security", "signal")}
            for item in others
        ],
        "bands": bands,
        "ssid_visibility": "hidden-by-survey",
    }


def wifi_survey() -> dict[str, Any]:
    system = platform.system()
    if system == "Linux":
        text = _run(
            [
                "nmcli",
                "-t",
                "-f",
                "IN-USE,SSID,SIGNAL,SECURITY,FREQ,CHAN",
                "device",
                "wifi",
                "list",
            ],
            12,
        )
        parsed = parse_nmcli_wifi(text)
        if not parsed["available"]:
            return {
                "available": False,
                "reason": "nmcli unavailable or no scan result; install NetworkManager or use iw with authorization",
            }
        return parsed
    if system != "Darwin":
        return {
            "available": False,
            "reason": "Wi-Fi survey parser supports macOS and Linux; use phone/AP inventory on this host",
        }
    text = _run(["system_profiler", "SPAirPortDataType"], 12)
    parsed = parse_wifi_output(text)
    if not parsed["current"] and not parsed["other_count"]:
        return {
            "available": False,
            "reason": "macOS returned no Wi-Fi survey data (permission or radio off)",
        }
    return parsed


async def _ble_discover(seconds: float) -> dict[str, Any]:
    try:
        from bleak import BleakScanner
    except ImportError:
        return {
            "available": False,
            "reason": "bleak not installed; run pip install 'southbound[survey]'",
            "devices": [],
        }
    try:
        devices = await BleakScanner.discover(timeout=seconds)
    except Exception as exc:
        return {
            "available": False,
            "reason": f"{type(exc).__name__}: {exc}",
            "devices": [],
        }
    rows = []
    for item in devices:
        rows.append(
            {
                "address": getattr(item, "address", ""),
                "name": getattr(item, "name", "") or "",
                "rssi": getattr(item, "rssi", None),
            }
        )
    rows.sort(key=lambda row: (row["name"].lower(), row["address"]))
    return {
        "available": True,
        "device_count": len(rows),
        "devices": rows,
        "note": "advertisement/listening only; no pairing or connection",
    }


def ble_survey(seconds: float = 5.0) -> dict[str, Any]:
    """Optional passive BLE scan; requires the `survey` extra."""
    return asyncio.run(_ble_discover(seconds))


def _mdns_types(inventory: dict[str, Any]) -> set[str]:
    return {
        str(item.get("service_type") or "")
        for item in inventory.get("mdns_services") or []
    }


def _open_ports(deep: dict[str, Any] | None) -> dict[str, list[int]]:
    return {
        str(item.get("ip") or ""): list(item.get("open_ports") or [])
        for item in (deep or {}).get("hosts") or []
        if item.get("ip")
    }


def _identity_map(deep: dict[str, Any] | None) -> dict[str, str]:
    return {
        str(item.get("ip") or ""): str(item.get("identity") or "")
        for item in (deep or {}).get("hosts") or []
        if item.get("ip")
    }


def _readiness(
    inventory: dict[str, Any],
    deep: dict[str, Any] | None,
    radios: dict[str, Any] | None,
    ble: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    mdns = _mdns_types(inventory)
    ports = _open_ports(deep)
    identities = _identity_map(deep)
    all_ports = {port for values in ports.values() for port in values}
    radio = radios or {}
    ble_data = ble or {}
    bluetooth = (radio.get("bluetooth") or {}).get("state", "unknown")

    def status(available: bool, evidence: str, adapter: str = "") -> dict[str, Any]:
        return {"available": available, "evidence": evidence, "adapter": adapter}

    mqtt = 1883 in all_ports or 8883 in all_ports or "_mqtt._tcp" in mdns
    ha = 8123 in all_ports or "_home-assistant._tcp" in mdns
    camera = (
        554 in all_ports
        or 37777 in all_ports
        or any("摄像头" in value or "NVR" in value for value in identities.values())
    )
    matter = "_matter._tcp" in mdns
    thread = (
        "_meshcop._udp" in mdns
        or (radio.get("thread") or {}).get("status") == "detected"
    )
    zigbee = (radio.get("zigbee") or {}).get("status") == "detected"
    ble_ok = ble_data.get("available") or bluetooth.lower() == "on"
    drone = False

    return {
        "mqtt": status(mqtt, "1883/8883 or _mqtt._tcp", "mqtt"),
        "home_assistant": status(ha, "8123 or _home-assistant._tcp", "home_assistant"),
        "camera": status(
            camera, "554/37777 or camera identity", "camera (new adapter needed)"
        ),
        "zigbee": status(zigbee, "coordinator evidence", "zigbee2mqtt"),
        "thread": status(thread, "_meshcop._udp or Thread radio", "matter/thread"),
        "matter": status(matter, "_matter._tcp", "matter via HA"),
        "ble": status(
            ble_ok, f"Bluetooth state={bluetooth}", "ble (GATT; explicit binding required)"
        ),
        "drone": status(
            drone, "no drone endpoint or adapter", "drone (new adapter needed)"
        ),
    }


def _recommended_profile(
    readiness: dict[str, dict[str, Any]], host: dict[str, Any]
) -> str:
    if readiness["camera"]["available"]:
        return "camera_edge"
    if readiness["drone"]["available"]:
        return "drone_lab"
    host_text = " ".join(
        str(host.get(key) or "") for key in ("model", "cpu", "hostname")
    ).lower()
    is_pi = "raspberry" in host_text or "bcm27" in host_text or "bcm28" in host_text
    if (
        readiness["mqtt"]["available"]
        or readiness["home_assistant"]["available"]
        or readiness["zigbee"]["available"]
    ):
        if is_pi:
            return (
                "pi_microhub"
                if readiness["home_assistant"]["available"]
                else "pi_minimal"
            )
        return "micro_hub"
    return "survey_only"


def _next_actions(readiness: dict[str, dict[str, Any]]) -> list[str]:
    actions = []
    if not readiness["mqtt"]["available"]:
        actions.append("部署一个本地 Mosquitto MQTT broker（可先跑在现有 Mac/PC）")
    if not readiness["home_assistant"]["available"]:
        actions.append("加 Home Assistant Green / N100，或先复用现有电脑做临时 host")
    if not readiness["zigbee"]["available"]:
        actions.append(
            "加 Zigbee coordinator（SLZB-06M / ZBDongle-E），再跑 Zigbee2MQTT"
        )
    if not readiness["camera"]["available"]:
        actions.append("摄像头接入前先加 RTSP/ONVIF adapter + PoE 网络")
    if not readiness["ble"]["available"]:
        actions.append("BLE 设备需要先打开 Bluetooth，并安装可选的 bleak survey extra")
    if not readiness["drone"]["available"]:
        actions.append("无人机先做模拟 mission；真机前加室内定位、护罩和人工急停")
    return actions


def build_survey(
    inventory: dict[str, Any],
    deep: dict[str, Any] | None = None,
    radios: dict[str, Any] | None = None,
    wifi: dict[str, Any] | None = None,
    ble: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pure survey assembly; collectors are injected for testability."""
    readiness = _readiness(inventory, deep, radios, ble)
    detected = [name for name, item in readiness.items() if item["available"]]
    missing = [name for name, item in readiness.items() if not item["available"]]
    host = inventory.get("host") or {}
    profile = _recommended_profile(readiness, host)
    services = inventory.get("mdns_services") or []
    return {
        "schema_version": 1,
        "generated_at": _now(),
        "scope": "authorized local network only; no credential or exploit attempts",
        "host": host,
        "inventory": {
            "device_count": len(inventory.get("devices") or []),
            "devices": inventory.get("devices") or [],
            "mdns_service_count": len(services),
            "mdns_service_types": sorted(
                {str(item.get("service_type") or "") for item in services}
            ),
        },
        "deep_scan": deep,
        "radios": radios or {},
        "wifi": wifi or {"available": False, "reason": "skipped"},
        "ble": ble or {"available": False, "reason": "skipped; pass --ble to scan"},
        "readiness": readiness,
        "summary": {
            "device_count": len(inventory.get("devices") or []),
            "host_count": len((deep or {}).get("hosts") or []),
            "mdns_service_count": len(services),
            "detected_protocols": detected,
            "missing_protocols": missing,
            "recommended_profile": profile,
        },
        "harness": {
            "profile": profile,
            "profile_detail": PROFILES[profile],
            "next_actions": _next_actions(readiness),
        },
    }


def _device_rows(survey: dict[str, Any]) -> list[str]:
    rows = []
    for dev in survey["inventory"].get("devices") or []:
        analysis = dev.get("analysis") or {}
        rows.append(
            f"| {dev.get('ip') or '—'} | {analysis.get('kind') or '未知'} | "
            f"{analysis.get('agent_tier') or '?'} | "
            f"{dev.get('vendor') or dev.get('manufacturer') or '—'} | "
            f"{', '.join(dev.get('sources') or [])} |"
        )
    if not rows:
        return ["| — | 未发现 | — | — | — |"]
    return rows


def render_report(survey: dict[str, Any]) -> str:
    host = survey.get("host") or {}
    summary = survey.get("summary") or {}
    readiness = survey.get("readiness") or {}
    radios = survey.get("radios") or {}
    wifi = survey.get("wifi") or {}
    ble = survey.get("ble") or {}
    deep = survey.get("deep_scan") or {}
    profile = survey.get("harness") or {}
    lines = [
        "# Local Network Survey",
        "",
        f"- Generated: {survey.get('generated_at')}",
        f"- Scope: {survey.get('scope')}",
        "",
        "## Summary",
        "",
        f"- Network: `{host.get('cidr') or host.get('subnet') or '?'}` via "
        f"`{host.get('interface') or '?'}`",
        f"- Local / gateway: `{host.get('local_ip') or '?'}` / "
        f"`{host.get('gateway') or '?'}`",
        f"- Devices: **{summary.get('device_count', 0)}**; "
        f"mDNS services: **{summary.get('mdns_service_count', 0)}**; "
        f"fingerprinted hosts: **{summary.get('host_count', 0)}**",
        f"- Detected protocols: {', '.join(summary.get('detected_protocols') or []) or 'none'}",
        f"- Missing protocols: {', '.join(summary.get('missing_protocols') or []) or 'none'}",
        f"- Recommended harness: **{profile.get('profile', 'survey_only')}**",
        "",
        "## IP devices",
        "",
        "| IP | Kind | Tier | Vendor | Sources |",
        "|---|---|---|---|---|",
        *_device_rows(survey),
        "",
        "## Wi-Fi",
        "",
    ]
    if wifi.get("available"):
        current = wifi.get("current") or {}
        lines.extend(
            [
                f"- Current: band **{current.get('band', 'unknown')}**, "
                f"channel {current.get('channel', '—')}, "
                f"security {current.get('security', '—')}",
                f"- Other visible networks: **{wifi.get('other_count', 0)}**",
                f"- Bands seen: {', '.join(f'{k}×{v}' for k, v in (wifi.get('bands') or {}).items()) or '—'}",
                f"- SSID visibility: {wifi.get('ssid_visibility', 'unknown')}",
            ]
        )
    else:
        lines.append(
            f"- Not available: {wifi.get('reason') or wifi.get('reason', 'unknown')}"
        )
    lines.extend(
        [
            "",
            "## Radios",
            "",
            "| Radio | Status | Evidence |",
            "|---|---|---|",
        ]
    )
    for key in ("zigbee", "thread", "zwave", "subghz"):
        item = radios.get(key) or {}
        lines.append(
            f"| {key} | {item.get('status', 'unknown')} | "
            f"{', '.join(item.get('evidence') or []) or '—'} |"
        )
    lines.extend(
        [
            f"| Bluetooth | {(radios.get('bluetooth') or {}).get('state', 'unknown')} | "
            f"serial ports: {len(radios.get('serial_ports') or [])} |",
            "",
            "## Host services",
            "",
            "| IP | Identity | Open ports |",
            "|---|---|---|",
        ]
    )
    hosts = deep.get("hosts") or []
    if hosts:
        for item in hosts:
            lines.append(
                f"| {item.get('ip') or '—'} | {item.get('identity') or '—'} | "
                f"{', '.join(str(p) for p in item.get('open_ports') or []) or '—'} |"
            )
    else:
        lines.append("| — | 未做端口指纹 | — |")
    lines.extend(
        [
            "",
            "## Adapter readiness",
            "",
            "| Domain | Available | Evidence | Adapter |",
            "|---|---|---|---|",
        ]
    )
    for name, item in readiness.items():
        lines.append(
            f"| {name} | {'yes' if item.get('available') else 'no'} | "
            f"{item.get('evidence') or '—'} | {item.get('adapter') or '—'} |"
        )
    lines.extend(
        [
            "",
            "## Harness profile",
            "",
            f"**{profile.get('profile', 'survey_only')}** — "
            f"{profile.get('profile_detail', {}).get('scope', '')}",
            "",
        ]
    )
    for action in profile.get("next_actions") or []:
        lines.append(f"- {action}")
    if ble.get("available"):
        lines.extend(
            [
                "",
                "## BLE advertisements (optional)",
                "",
                f"- Devices: **{ble.get('device_count', 0)}** (no pairing performed)",
            ]
        )
        for item in (ble.get("devices") or [])[:20]:
            lines.append(
                f"- {item.get('name') or '(unnamed)'} `{item.get('address')}` "
                f"RSSI {item.get('rssi', '—')}"
            )
    lines.extend(
        [
            "",
            "## Safety boundary",
            "",
            "- Only run on a network and devices you own or are explicitly authorized to assess.",
            "- No default-credential, authentication-bypass, exploit, deauth, or payload capture.",
            "- High-risk devices (locks, power, gas, drones) stay behind policy + human confirmation.",
            "",
        ]
    )
    return "\n".join(lines)


def print_summary(survey: dict[str, Any]) -> None:
    host = survey.get("host") or {}
    summary = survey.get("summary") or {}
    profile = (survey.get("harness") or {}).get("profile", "survey_only")
    print(
        f"\nSurvey {host.get('cidr') or '?'} via {host.get('interface') or '?'} "
        f"({summary.get('device_count', 0)} devices, "
        f"{summary.get('mdns_service_count', 0)} mDNS)"
    )
    print(
        f"Detected: {', '.join(summary.get('detected_protocols') or []) or 'none'}\n"
        f"Missing: {', '.join(summary.get('missing_protocols') or []) or 'none'}\n"
        f"Profile: {profile}"
    )
    for action in (survey.get("harness") or {}).get("next_actions") or []:
        print(f"  - {action}")


def survey(
    sweep: bool = False,
    use_mdns: bool = True,
    use_ssdp: bool = True,
    ports: bool = True,
    ble: bool = False,
    ble_seconds: float = 5.0,
    wifi: bool = True,
    timeout: float = 0.4,
    workers: int = 192,
) -> dict[str, Any]:
    inventory = discover.discover(
        sweep=sweep,
        use_mdns=use_mdns,
        use_ssdp=use_ssdp,
    )
    hosts = [str(dev["ip"]) for dev in inventory.get("devices") or [] if dev.get("ip")]
    gateway = (inventory.get("host") or {}).get("gateway")
    if gateway and gateway not in hosts:
        hosts.append(gateway)
    deep = None
    if ports and hosts:
        deep = deep_scan.scan(
            sweep=False,
            ports=deep_scan.PORTS,
            timeout=timeout,
            workers=workers,
            hosts=hosts,
        )
    return build_survey(
        inventory,
        deep=deep,
        radios=detect_radios(),
        wifi=wifi_survey() if wifi else {"available": False, "reason": "skipped"},
        ble=(
            ble_survey(ble_seconds)
            if ble
            else {"available": False, "reason": "skipped"}
        ),
    )


def save(survey_data: dict[str, Any], out: Path, report: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(survey_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report.write_text(render_report(survey_data), encoding="utf-8")
