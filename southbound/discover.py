#!/usr/bin/env python3
"""LAN inventory collector for the Southbound.

Collects, using only local-network metadata:

  * ARP table (optionally after a light ICMP sweep)
  * SSDP / UPnP device descriptions
  * mDNS / Bonjour service instances (macOS `dns-sd`)

Then classifies every device into an agent capability tier (T0-T3) and
lists the data/control paths available to a LAN orchestrator.

Owner-authorized use only: run it on a network you control.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import socket
import subprocess
import time
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from .paths import DATA_DIR
from .registry import classify, vendor_of

DEFAULT_OUT = DATA_DIR / "inventory.json"

SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 1900
SSDP_MSEARCH = (
    "M-SEARCH * HTTP/1.1\r\n"
    f"HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n"
    'MAN: "ssdp:discover"\r\n'
    "MX: 2\r\n"
    "ST: ssdp:all\r\n\r\n"
)

MDNS_TYPES = [
    "_airplay._tcp",
    "_raop._tcp",
    "_hap._tcp",
    "_matter._tcp",
    "_googlecast._tcp",
    "_ipp._tcp",
    "_printer._tcp",
    "_pdl-datastream._tcp",
    "_sonos._tcp",
    "_home-assistant._tcp",
    "_esphomelib._tcp",
    "_mqtt._tcp",
    "_http._tcp",
    "_workstation._tcp",
    "_companion-link._tcp",
    "_sleep-proxy._udp",
]

_ARP_LINE = re.compile(
    r"\((?P<ip>\d{1,3}(?:\.\d{1,3}){3})\)\s+at\s+(?P<mac>[0-9a-fA-F:]+)"
)
# Columns: Timestamp, A/R, Flags, if, Domain, Service Type, Instance Name
_MDNS_LINE = re.compile(
    r"^\s*(\S+)\s+(Add|Rmv)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(.*?)\s*$"
)


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "ignore")
    return value


def run_cmd(cmd: list[str], timeout: float) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(cmd, 0, _text(exc.stdout), _text(exc.stderr))
    except FileNotFoundError:
        return subprocess.CompletedProcess(cmd, 127, "", "")


def host_info() -> dict:
    route = run_cmd(["route", "-n", "get", "default"], 3).stdout
    iface_match = re.search(r"interface:\s*(\S+)", route)
    gateway_match = re.search(r"gateway:\s*(\S+)", route)
    iface = iface_match.group(1) if iface_match else "en0"
    gateway = gateway_match.group(1) if gateway_match else ""

    local_ip = run_cmd(["ipconfig", "getifaddr", iface], 2).stdout.strip()
    if not local_ip:
        try:
            local_ip = socket.gethostbyname(socket.gethostname())
        except OSError:
            local_ip = ""

    prefix = gateway.rsplit(".", 1)[0] + "." if gateway.count(".") == 3 else ""
    hostname = run_cmd(["scutil", "--get", "ComputerName"], 2).stdout.strip()
    if not hostname:
        hostname = socket.gethostname()
    cpu = run_cmd(["sysctl", "-n", "machdep.cpu.brand_string"], 2).stdout.strip()
    iface_info = run_cmd(["ifconfig", iface], 2).stdout
    mac_match = re.search(r"ether\s+([0-9a-fA-F:]{17})", iface_info)
    mac = mac_match.group(1).lower() if mac_match else None

    netmask = ""
    cidr = ""
    inet_match = re.search(
        r"inet\s+(\d+\.\d+\.\d+\.\d+)\s+netmask\s+0x([0-9a-fA-F]+)", iface_info
    )
    if local_ip and inet_match:
        netmask = str(ipaddress.IPv4Address(int(inet_match.group(2), 16)))
        try:
            cidr = str(ipaddress.ip_network(f"{local_ip}/{netmask}", strict=False))
        except ValueError:
            cidr = ""
    if not cidr and prefix:
        cidr = f"{prefix}0/24"
        netmask = "255.255.255.0"

    return {
        "interface": iface,
        "local_ip": local_ip,
        "gateway": gateway,
        "prefix": prefix,
        "netmask": netmask,
        "cidr": cidr,
        "subnet": cidr,
        "hostname": hostname,
        "cpu": cpu,
        "mac": mac,
    }


def parse_arp() -> dict[str, str]:
    entries: dict[str, str] = {}
    out = run_cmd(["arp", "-a"], 5).stdout
    for line in out.splitlines():
        match = _ARP_LINE.search(line)
        if not match:
            continue
        ip = match.group("ip")
        mac = match.group("mac").lower()
        if mac in ("ff:ff:ff:ff:ff:ff", "(incomplete)"):
            continue
        if mac.startswith(("1:0:5e", "01:00:5e", "33:33", "01:80:c2")):
            continue
        entries[ip] = mac
    return entries


def ping_sweep(cidr: str, skip_ip: str = "", workers: int = 48) -> None:
    if not cidr:
        return
    try:
        network = ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return
    targets = [str(ip) for ip in network.hosts() if str(ip) != skip_ip]
    if len(targets) > 2046:
        # Very large subnet: fall back to the /24 around this host.
        base = skip_ip.rsplit(".", 1)[0]
        targets = [f"{base}.{i}" for i in range(1, 255) if f"{base}.{i}" != skip_ip]

    def ping(ip: str) -> None:
        subprocess.run(
            ["ping", "-c", "1", "-W", "400", "-q", ip],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(ping, targets))
    time.sleep(0.4)


def _parse_ssdp(data: bytes) -> dict[str, str]:
    text = data.decode("utf-8", "ignore")
    headers: dict[str, str] = {}
    for line in text.split("\r\n")[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().upper()] = value.strip()
    return headers


def _fetch_upnp(location: str, timeout: float = 1.8) -> dict:
    try:
        req = urllib.request.Request(location, headers={"User-Agent": "southbound/0.5"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(256 * 1024)
        root = ET.fromstring(raw)

        def find(tag: str) -> str | None:
            node = root.find(f".//{{*}}{tag}")
            return node.text.strip() if node is not None and node.text else None

        return {
            "friendly_name": find("friendlyName"),
            "manufacturer": find("manufacturer"),
            "model": find("modelName"),
            "device_type": find("deviceType"),
        }
    except Exception:
        return {}


def ssdp_discover(duration: float = 3.0) -> list[dict]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(0.4)
    try:
        sock.sendto(SSDP_MSEARCH.encode("ascii"), (SSDP_ADDR, SSDP_PORT))
    except OSError:
        sock.close()
        return []

    deadline = time.monotonic() + duration
    seen: dict[tuple, dict] = {}
    while time.monotonic() < deadline:
        try:
            data, addr = sock.recvfrom(65507)
        except socket.timeout:
            continue
        except OSError:
            break
        headers = _parse_ssdp(data)
        location = headers.get("LOCATION", "")
        usn = headers.get("USN", "")
        st = headers.get("ST", "")
        key = (location or st, usn, addr[0])
        if key in seen:
            continue
        info = {
            "ip": addr[0],
            "location": location,
            "server": headers.get("SERVER", ""),
            "st": st,
            "usn": usn,
        }
        if location:
            info.update(_fetch_upnp(location))
        seen[key] = info
    sock.close()
    return list(seen.values())


def _browse_mdns(service_type: str, duration: float) -> list[dict]:
    try:
        proc = subprocess.run(
            ["dns-sd", "-B", service_type, "local."],
            capture_output=True,
            text=True,
            timeout=duration,
        )
        out = _text(proc.stdout) + _text(proc.stderr)
    except subprocess.TimeoutExpired as exc:
        out = _text(exc.stdout) + _text(exc.stderr)
    except FileNotFoundError:
        return []

    found: list[dict] = []
    for line in out.splitlines():
        match = _MDNS_LINE.match(line)
        if not match or match.group(2) != "Add":
            continue
        service = match.group(6).rstrip(".")
        instance = match.group(7).strip()
        if not instance or instance.startswith("_services"):
            continue
        display = re.sub(r"\._[^.]+\._(?:tcp|udp)\.local\.?$", "", instance).strip()
        found.append(
            {
                "service_type": service,
                "instance": instance,
                "display_name": display or instance,
            }
        )
    return found


def _resolve_mdns(services: list[dict], duration: float = 3.0) -> list[dict]:
    """Resolve each Bonjour instance to a hostname and IPv4 address."""

    def resolve(svc: dict) -> dict:
        out = run_cmd(
            ["dns-sd", "-L", svc["instance"], svc["service_type"], "local."],
            duration,
        ).stdout
        match = re.search(r"can be reached at (\S+?):(\d+)", out)
        if not match:
            return svc
        host = match.group(1).rstrip(".")
        svc["hostname"] = host
        svc["port"] = int(match.group(2))
        model = re.search(r"\smodel=([^\s]+)", out)
        if model:
            svc["model"] = model.group(1)
        lookup = run_cmd(["dns-sd", "-G", "v4", host], min(duration, 2.5)).stdout
        ip_match = re.search(r"\s(\d{1,3}(?:\.\d{1,3}){3})\s", lookup)
        if ip_match and not ip_match.group(1).startswith("127."):
            svc["ip"] = ip_match.group(1)
        return svc

    if not services:
        return services
    with ThreadPoolExecutor(max_workers=min(8, len(services))) as pool:
        return list(pool.map(resolve, services))


def mdns_discover(duration: float = 3.5) -> list[dict]:
    with ThreadPoolExecutor(max_workers=len(MDNS_TYPES)) as pool:
        batches = list(pool.map(lambda svc: _browse_mdns(svc, duration), MDNS_TYPES))
    deduped: dict[tuple[str, str], dict] = {}
    for batch in batches:
        for item in batch:
            deduped[(item["service_type"], item["instance"])] = item
    return _resolve_mdns(list(deduped.values()))


def _name_key(value: str) -> str:
    """Normalize a Bonjour/host name for fuzzy matching."""
    return re.sub(r"[^0-9a-z一-鿿]+", "", (value or "").lower())


def _attach_mdns(devices: dict[str, dict], services: list[dict]) -> None:
    for svc in services:
        target = None
        ip = svc.get("ip")
        if ip:
            target = devices.setdefault(
                f"ip:{ip}",
                {
                    "ip": ip,
                    "mac": None,
                    "sources": [],
                    "services": [],
                    "ssdp_types": [],
                },
            )
            target.setdefault("friendly_name", svc["display_name"])
        if target is None:
            name = _name_key(svc["display_name"].split("@")[-1])
            for dev in devices.values():
                label = _name_key(
                    " ".join(
                        str(dev.get(key) or "")
                        for key in ("friendly_name", "model", "hostname")
                    )
                )
                if name and label and (name in label or label in name):
                    target = dev
                    break
        if target is None:
            key = f"mdns:{svc['service_type']}:{svc['instance']}"
            target = devices.setdefault(
                key,
                {
                    "ip": None,
                    "mac": None,
                    "sources": [],
                    "services": [],
                    "ssdp_types": [],
                },
            )
            target["friendly_name"] = svc["display_name"]
        if svc.get("hostname"):
            target.setdefault("hostname", svc["hostname"])
        if svc.get("model"):
            target.setdefault("model", svc["model"])
        target["sources"] = list(dict.fromkeys(target.get("sources", []) + ["mdns"]))
        target["services"] = list(
            dict.fromkeys(target.get("services", []) + [svc["service_type"]])
        )


def _merge_ssdp(devices: dict[str, dict], responses: list[dict]) -> None:
    for resp in responses:
        ip = resp.get("ip")
        if ip:
            key = f"ip:{ip}"
        else:
            key = f"ssdp:{resp.get('usn') or resp.get('location')}"
        dev = devices.setdefault(
            key,
            {
                "ip": ip,
                "mac": None,
                "sources": [],
                "services": [],
                "ssdp_types": [],
            },
        )
        dev["sources"] = list(dict.fromkeys(dev.get("sources", []) + ["ssdp"]))
        for field in (
            "friendly_name",
            "manufacturer",
            "model",
            "device_type",
            "server",
            "location",
        ):
            value = resp.get(field)
            if value and not dev.get(field):
                dev[field] = value
        for value in (resp.get("st"), resp.get("device_type")):
            if value:
                dev["ssdp_types"] = list(
                    dict.fromkeys(dev.get("ssdp_types", []) + [value])
                )
        if not dev.get("vendor"):
            dev["vendor"] = vendor_of(dev.get("mac")) or resp.get("manufacturer")


def discover(
    sweep: bool = False,
    use_mdns: bool = True,
    use_ssdp: bool = True,
    mdns_seconds: float = 3.5,
    ssdp_seconds: float = 3.0,
) -> dict:
    host = host_info()
    arp = parse_arp()
    if sweep:
        ping_sweep(host["cidr"], host["local_ip"])
        arp = parse_arp()

    devices: dict[str, dict] = {}
    if host["local_ip"]:
        devices[f"ip:{host['local_ip']}"] = {
            "ip": host["local_ip"],
            "mac": host["mac"],
            "vendor": vendor_of(host["mac"])
            or ("Apple" if "apple" in (host.get("cpu") or "").lower() else None),
            "sources": ["self"],
            "services": [],
            "ssdp_types": [],
            "friendly_name": host["hostname"],
            "is_self": True,
        }
    for ip, mac in sorted(arp.items()):
        key = f"ip:{ip}"
        if key in devices:
            devices[key].setdefault("mac", mac)
            devices[key]["sources"] = list(
                dict.fromkeys(devices[key]["sources"] + ["arp"])
            )
        else:
            devices[key] = {
                "ip": ip,
                "mac": mac,
                "vendor": vendor_of(mac),
                "sources": ["arp"],
                "services": [],
                "ssdp_types": [],
            }

    if use_ssdp:
        _merge_ssdp(devices, ssdp_discover(ssdp_seconds))

    mdns_services = mdns_discover(mdns_seconds) if use_mdns else []
    _attach_mdns(devices, mdns_services)

    for dev in devices.values():
        if not dev.get("vendor") and dev.get("mac"):
            dev["vendor"] = vendor_of(dev["mac"])
        dev["analysis"] = classify(dev, mdns_types=dev.get("services", []))
        if dev.get("is_self"):
            cpu = (host.get("cpu") or "").lower()
            dev["analysis"].update(
                {
                    "kind": "本机（编排主机）",
                    "agent_tier": "T3" if "apple m" in cpu else "T2",
                    "chip_hint": host.get("cpu") or "未知 SoC",
                    "control_paths": ["本机进程/API", "已装的 MCP 工具"],
                    "data_paths": ["本机可达的全部协议", "MQTT/HTTP/SSH"],
                    "confidence": "high",
                    "notes": "agent 的编排与推理可跑在这里。",
                }
            )
        if dev.get("ip") and dev["ip"] == host.get("gateway"):
            dev["analysis"].update(
                {
                    "kind": "路由器/网关（默认网关）",
                    "agent_tier": "T0",
                    "chip_hint": "网络 SoC",
                    "control_paths": ["管理页面 / UPnP"],
                    "data_paths": ["DHCP 租约", "DNS", "SNMP/syslog"],
                    "confidence": "high",
                    "notes": "默认网关；通常是主路由或光猫。",
                }
            )

    ordered = sorted(
        devices.values(), key=lambda d: (d.get("ip") is None, d.get("ip") or "")
    )
    tiers: dict[str, int] = {}
    for dev in ordered:
        tier = dev["analysis"]["agent_tier"]
        tiers[tier] = tiers.get(tier, 0) + 1

    inventory = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "host": host,
        "devices": ordered,
        "mdns_services": mdns_services,
        "summary": {
            "device_count": len(ordered),
            "mdns_service_count": len(mdns_services),
            "by_tier": tiers,
        },
    }
    return inventory


def _short(value: str | None, width: int) -> str:
    if not value:
        return "-" * 1
    value = str(value)
    return value if len(value) <= width else value[: width - 1] + "…"


def print_inventory(inv: dict) -> None:
    host = inv["host"]
    print(
        f"\nLAN {host.get('subnet') or '?'} via {host.get('interface')} "
        f"(local {host.get('local_ip') or '?'}, gateway {host.get('gateway') or '?'})"
    )
    print(
        f"{inv['summary']['device_count']} devices, "
        f"{inv['summary']['mdns_service_count']} mDNS services\n"
    )
    header = f"{'IP':<16} {'MAC':<18} {'VENDOR':<22} {'KIND':<24} {'TIER':<5}"
    print(header)
    print("-" * len(header))
    for dev in inv["devices"]:
        analysis = dev.get("analysis", {})
        print(
            f"{_short(dev.get('ip'), 16):<16} "
            f"{_short(dev.get('mac'), 18):<18} "
            f"{_short(dev.get('manufacturer') or dev.get('vendor'), 22):<22} "
            f"{_short(analysis.get('kind'), 24):<24} "
            f"{analysis.get('agent_tier', '?'):<5}"
        )
    if inv["mdns_services"]:
        print("\nmDNS service instances:")
        for svc in inv["mdns_services"]:
            print(f"  {svc['service_type']:<32} {svc['display_name']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect a LAN device inventory")
    parser.add_argument(
        "--sweep", action="store_true", help="light ICMP sweep before reading ARP"
    )
    parser.add_argument("--no-mdns", action="store_true", help="skip mDNS discovery")
    parser.add_argument("--no-ssdp", action="store_true", help="skip SSDP discovery")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="output JSON path")
    parser.add_argument("--mdns-seconds", type=float, default=3.5)
    parser.add_argument("--ssdp-seconds", type=float, default=3.0)
    args = parser.parse_args()

    inventory = discover(
        sweep=args.sweep,
        use_mdns=not args.no_mdns,
        use_ssdp=not args.no_ssdp,
        mdns_seconds=args.mdns_seconds,
        ssdp_seconds=args.ssdp_seconds,
    )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print_inventory(inventory)
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()
