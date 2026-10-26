#!/usr/bin/env python3
"""Deeper LAN scan: port map + service fingerprinting.

Complements discover.py (which is metadata-only) with a light active scan
of the local /24:

  * optional ICMP sweep to populate ARP
  * TCP connect scan over a curated set of common ports
  * HTTP/HTTPS title + Server header, SSH banner, TLS subject, RTSP check
  * targeted SSDP probes (upnp:rootdevice, IGD, ssdp:all)
  * identity hints (router / camera / printer / NAS / phone / computer)

Owner-authorized use only. No exploitation, no credential attempts.

Usage:
    python3 deep_scan.py
    python3 deep_scan.py --sweep --workers 192 --timeout 0.4
    python3 deep_scan.py --ports 22,80,443,554,8080
"""

from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from .discover import host_info, parse_arp, ping_sweep, run_cmd
from .paths import DATA_DIR
from .registry import vendor_of

DEFAULT_OUT = DATA_DIR / "deep_scan.json"

PORTS = [
    21,
    22,
    23,
    25,
    53,
    80,
    81,
    88,
    110,
    139,
    143,
    443,
    445,
    465,
    515,
    548,
    554,
    587,
    631,
    873,
    902,
    993,
    995,
    1080,
    1883,
    2049,
    2375,
    2376,
    3000,
    3306,
    3389,
    5000,
    5060,
    5432,
    5555,
    5900,
    5985,
    5986,
    6379,
    7000,
    8000,
    8001,
    8008,
    8080,
    8081,
    8088,
    8090,
    8123,
    8443,
    8554,
    8888,
    9000,
    9100,
    9200,
    9443,
    9999,
    10000,
    32400,
    37777,
    49152,
    49153,
    49154,
    50000,
]
WEB_PORTS = {
    80,
    81,
    88,
    3000,
    5000,
    8000,
    8001,
    8008,
    8080,
    8081,
    8088,
    8090,
    8123,
    8888,
    9000,
    32400,
    49152,
    49153,
    49154,
    50000,
}
TLS_PORTS = {443, 8443, 9443, 993, 995, 5986}
MAX_WEB_PROBES = 4

SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 1900
SSDP_TARGETS = [
    "upnp:rootdevice",
    "urn:schemas-upnp-org:device:InternetGatewayDevice:1",
    "ssdp:all",
]


def port_open(ip: str, port: int, timeout: float) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        return sock.connect_ex((ip, port)) == 0
    except OSError:
        return False
    finally:
        sock.close()


def scan_ports(
    cidr: str,
    ports: list[int],
    timeout: float,
    workers: int,
    hosts: list[str] | None = None,
) -> dict[str, list[int]]:
    if hosts:
        host_list = sorted(set(hosts))
    else:
        try:
            network = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            return {}
        host_list = [str(ip) for ip in network.hosts()]
        if len(host_list) > 2046:
            host_list = host_list[:2046]
    targets = [(ip, port) for ip in host_list for port in ports]
    open_map: dict[str, list[int]] = defaultdict(list)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(port_open, ip, port, timeout): (ip, port)
            for ip, port in targets
        }
        for future in as_completed(futures):
            ip, port = futures[future]
            try:
                if future.result():
                    open_map[ip].append(port)
            except Exception:
                pass
    return {ip: sorted(values) for ip, values in open_map.items()}


def scan_host_ports(
    ip: str, ports: list[int], timeout: float, workers: int
) -> list[int]:
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda port: port_open(ip, port, timeout), ports))
    return sorted(port for port, is_open in zip(ports, results) if is_open)


def http_probe(ip: str, port: int, tls: bool = False) -> dict:
    context = ssl._create_unverified_context() if tls else None
    conn_cls = http.client.HTTPSConnection if tls else http.client.HTTPConnection
    conn = (
        conn_cls(ip, port, timeout=2.5, context=context)
        if tls
        else conn_cls(ip, port, timeout=2.5)
    )
    try:
        conn.request(
            "GET",
            "/",
            headers={
                "User-Agent": "southbound/0.4",
                "Accept": "text/html,*/*",
            },
        )
        resp = conn.getresponse()
        body = resp.read(65536)
        headers = {k.lower(): v for k, v in resp.getheaders()}
        text = body.decode("utf-8", "ignore")
        title_match = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
        title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
        model_match = re.search(
            r"\b((?:HG|EG|HN|HS|ONT|ONU)[0-9]{3,5}[A-Za-z0-9-]*)\b", text, re.I
        )
        return {
            "port": port,
            "scheme": "https" if tls else "http",
            "status": resp.status,
            "server": headers.get("server", ""),
            "title": title[:120],
            "location": headers.get("location", ""),
            "model": model_match.group(1).upper() if model_match else "",
            "body_hint": _body_hint(text),
        }
    except Exception as exc:
        return {
            "port": port,
            "scheme": "https" if tls else "http",
            "error": type(exc).__name__,
        }
    finally:
        conn.close()


def _body_hint(text: str) -> str:
    lower = text.lower()
    hints = []
    for key in (
        "huawei",
        "synology",
        "plex",
        "camera",
        "printer",
        "login",
        "router",
        "ont",
        "hg8245",
        "eg8145",
        "tplink",
        "xiaomi",
        "tuya",
        "hikvision",
        "dahua",
        "unifi",
        "openwrt",
    ):
        if key in lower:
            hints.append(key)
    return ",".join(hints)


def ssh_banner(ip: str) -> str:
    try:
        with socket.create_connection((ip, 22), timeout=2.0) as sock:
            sock.settimeout(2.0)
            raw = sock.recv(256)
            return raw.split(b"\n", 1)[0].decode("ascii", "ignore").strip()
    except Exception:
        return ""


def smb_netbios(ip: str) -> str:
    """Read the NetBIOS name from an SMB host (guest-level, read-only)."""
    result = run_cmd(["smbutil", "status", "-a", ip], 6)
    text = (result.stdout or "") + (result.stderr or "")
    match = re.search(r"^([A-Za-z0-9_-]{1,15})\s+0x", text, re.M)
    return match.group(1) if match else ""


def is_random_mac(mac: str | None) -> bool:
    if not mac:
        return False
    try:
        first = int(mac.split(":")[0], 16)
    except (ValueError, IndexError):
        return False
    return bool(first & 0x02)


def exposures(host: dict) -> list[str]:
    ports = set(host.get("open_ports", []))
    notes = []
    if 23 in ports:
        notes.append("Telnet 开放")
    if 139 in ports or 445 in ports:
        notes.append("SMB 开放")
    if 49152 in ports or 49153 in ports:
        notes.append("UPnP 端口开放")
    if 53 in ports:
        notes.append("DNS 服务")
    if 3389 in ports:
        notes.append("RDP 开放")
    if 5900 in ports:
        notes.append("VNC 开放")
    return notes


def rtsp_probe(ip: str) -> dict:
    request = f"OPTIONS rtsp://{ip}/ RTSP/1.0\r\nCSeq: 1\r\nUser-Agent: southbound/0.4\r\n\r\n"
    try:
        with socket.create_connection((ip, 554), timeout=2.0) as sock:
            sock.settimeout(2.0)
            sock.sendall(request.encode("ascii"))
            data = sock.recv(512).decode("utf-8", "ignore")
        status = data.split("\r\n", 1)[0]
        server = ""
        for line in data.split("\r\n"):
            if line.lower().startswith("server:"):
                server = line.split(":", 1)[1].strip()
        return {"status": status, "server": server}
    except Exception:
        return {}


def tls_subject(ip: str, port: int) -> dict:
    result = run_cmd(
        ["openssl", "s_client", "-connect", f"{ip}:{port}", "-servername", ip],
        5,
    )
    # openssl writes the session info to stderr when stdin is not a TTY.
    text = (result.stdout or "") + (result.stderr or "")
    out = {}
    for key in ("subject", "issuer"):
        match = re.search(rf"^\s*{key}=(.*)$", text, re.M | re.I)
        if match:
            out[key] = match.group(1).strip()
    return out


def _port_service(port: int) -> str:
    return {
        21: "ftp",
        22: "ssh",
        23: "telnet",
        25: "smtp",
        53: "dns",
        80: "http",
        81: "http",
        88: "http",
        110: "pop3",
        139: "smb",
        143: "imap",
        443: "https",
        445: "smb",
        465: "smtps",
        515: "lpd",
        548: "afp",
        554: "rtsp",
        587: "smtp",
        631: "ipp",
        873: "rsync",
        902: "vmware",
        993: "imaps",
        995: "pop3s",
        1080: "socks",
        1883: "mqtt",
        2049: "nfs",
        2375: "docker",
        2376: "docker-tls",
        3000: "http",
        3306: "mysql",
        3389: "rdp",
        5000: "http",
        5060: "sip",
        5432: "postgres",
        5555: "adb",
        5900: "vnc",
        5985: "winrm",
        5986: "winrm-tls",
        6379: "redis",
        7000: "http",
        8000: "http",
        8001: "http",
        8008: "http",
        8080: "http",
        8081: "http",
        8088: "http",
        8090: "http",
        8123: "http",
        8443: "https",
        8554: "rtsp",
        8888: "http",
        9000: "http",
        9100: "raw-print",
        9200: "elastic",
        9443: "https",
        9999: "http",
        10000: "http",
        32400: "plex",
        37777: "dahua",
        49152: "upnp",
        49153: "upnp",
        49154: "upnp",
        50000: "http",
    }.get(port, "tcp")


def infer_identity(host: dict, self_ip: str = "") -> str:
    ports = set(host.get("open_ports", []))
    http = host.get("http") or []
    titles = " ".join(
        f"{item.get('title','')} {item.get('server','')} {item.get('body_hint','')} {item.get('model','')}"
        for item in http
    ).lower()
    ssh = (host.get("ssh_banner") or "").lower()
    vendor = (host.get("vendor") or "").lower()
    netbios = host.get("netbios") or ""

    if self_ip and host.get("ip") == self_ip:
        return "本机（扫描主机）"
    if netbios and "wrt" in netbios.lower():
        return "OpenWrt 路由器"
    if "espressif" in vendor:
        return "ESP32 设备（JSON API/FTPS）"
    if "airtunes" in titles:
        return "Apple AirPlay 主机"
    if 37777 in ports or 554 in ports:
        return "摄像头/NVR"
    if 9100 in ports or 631 in ports or 515 in ports:
        return "打印机"
    if 32400 in ports:
        return "Plex 媒体服务器"
    if "synology" in titles:
        return "Demo NAS"
    if 1883 in ports:
        return "MQTT broker"
    if 22 in ports and "openssh" in ssh:
        return "Linux/Unix 主机"
    if 139 in ports or 445 in ports:
        return f"Windows 主机（{netbios}）" if netbios else "Windows/SMB 主机"
    if 548 in ports:
        return "Mac/AFP 文件共享"
    if 3389 in ports:
        return "Windows 远程桌面"
    if "hg8245" in titles:
        return "华为 HG8245H 光猫"
    if "huawei" in titles or "ont" in titles or "eg8145" in titles:
        return "华为光猫/路由器"
    if "router" in titles or "login" in titles:
        return "Web 管理设备"
    if 80 in ports or 443 in ports or 8080 in ports:
        return "Web 服务设备"
    if "intel" in vendor:
        return "电脑（Intel 网卡）"
    if ports:
        return "未知（有开放端口）"
    if host.get("mac_random"):
        return "手机/便携设备（随机 MAC）"
    return "仅存活（无开放端口）"


def ssdp_probe(duration: float = 3.0) -> list[dict]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(0.4)
    for target in SSDP_TARGETS:
        message = (
            "M-SEARCH * HTTP/1.1\r\n"
            f"HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n"
            'MAN: "ssdp:discover"\r\n'
            "MX: 2\r\n"
            f"ST: {target}\r\n\r\n"
        )
        try:
            sock.sendto(message.encode("ascii"), (SSDP_ADDR, SSDP_PORT))
        except OSError:
            break

    deadline = time.monotonic() + duration
    responses: dict[tuple, dict] = {}
    while time.monotonic() < deadline:
        try:
            data, addr = sock.recvfrom(65507)
        except socket.timeout:
            continue
        except OSError:
            break
        headers = {}
        for line in data.decode("utf-8", "ignore").split("\r\n")[1:]:
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip().upper()] = value.strip()
        responses[(addr[0], headers.get("USN", ""), headers.get("ST", ""))] = {
            "ip": addr[0],
            "st": headers.get("ST", ""),
            "usn": headers.get("USN", ""),
            "server": headers.get("SERVER", ""),
            "location": headers.get("LOCATION", ""),
        }
    sock.close()
    return list(responses.values())


def scan(
    sweep: bool,
    ports: list[int],
    timeout: float,
    workers: int,
    full: bool = False,
    hosts: list[str] | None = None,
) -> dict:
    host = host_info()
    if sweep and not hosts:
        ping_sweep(host["cidr"], host["local_ip"])
    arp = parse_arp()
    open_map = scan_ports(host["cidr"], ports, timeout, workers, hosts=hosts)

    alive = sorted(set(hosts) if hosts else (set(arp) | set(open_map)))
    if full:
        full_ports = sorted(set(range(1, 2049)) | {p for p in PORTS if p > 2048})
        full_targets = set(open_map)
        for ip in alive:
            mac = arp.get(ip)
            if vendor_of(mac) and not is_random_mac(mac):
                full_targets.add(ip)
        for ip in sorted(full_targets):
            merged = set(open_map.get(ip, [])) | set(
                scan_host_ports(ip, full_ports, min(timeout, 0.25), workers)
            )
            if merged:
                open_map[ip] = sorted(merged)

    ssdp = ssdp_probe()
    host_ips = sorted(set(hosts) if hosts else (set(arp) | set(open_map)))
    hosts: list[dict] = []
    for ip in host_ips:
        open_ports = open_map.get(ip, [])
        record: dict = {
            "ip": ip,
            "mac": arp.get(ip),
            "mac_random": is_random_mac(arp.get(ip)),
            "vendor": vendor_of(arp.get(ip)),
            "open_ports": open_ports,
            "services": [_port_service(p) for p in open_ports],
            "http": [],
            "ssh_banner": ssh_banner(ip) if 22 in open_ports else "",
            "rtsp": rtsp_probe(ip) if 554 in open_ports else {},
            "tls": [],
            "netbios": "",
            "model": "",
            "exposures": [],
        }
        web_ports = [p for p in open_ports if p in WEB_PORTS][:MAX_WEB_PROBES]
        for port in web_ports:
            record["http"].append(http_probe(ip, port, tls=False))
        for port in [p for p in open_ports if p in TLS_PORTS][:2]:
            record["tls"].append({"port": port, **tls_subject(ip, port)})
            if port in (443, 8443, 9443):
                record["http"].append(http_probe(ip, port, tls=True))
        if {139, 445} & set(open_ports):
            record["netbios"] = smb_netbios(ip)
        record["model"] = next(
            (item["model"] for item in record["http"] if item.get("model")), ""
        )
        record["exposures"] = exposures(record)
        record["identity"] = infer_identity(record, host.get("local_ip", ""))
        hosts.append(record)

    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "host": host,
        "scanned_ports": ports,
        "full_scan": full,
        "hosts": hosts,
        "ssdp": ssdp,
        "summary": {
            "host_count": len(hosts),
            "with_open_ports": sum(1 for h in hosts if h["open_ports"]),
        },
    }


def print_results(result: dict) -> None:
    host = result["host"]
    scope = f"({len(result['scanned_ports'])} curated ports/host"
    if result.get("full_scan"):
        scope += ", full 1-2048 on alive hosts"
    scope += ")"
    print(f"\nDeep scan {host['subnet']} via {host['interface']} {scope}\n")
    header = f"{'IP':<16} {'VENDOR':<22} {'OPEN PORTS':<28} {'IDENTITY':<24} DETAIL"
    print(header)
    print("-" * len(header))
    for entry in result["hosts"]:
        ports = ",".join(str(p) for p in entry["open_ports"]) or "-"
        details = []
        if entry.get("model"):
            details.append(f"model={entry['model']}")
        if entry.get("netbios"):
            details.append(f"name={entry['netbios']}")
        for item in entry.get("http", []):
            if item.get("title"):
                details.append(f"title={item['title']}")
            elif item.get("server"):
                details.append(f"server={item['server']}")
        if entry.get("ssh_banner"):
            details.append(entry["ssh_banner"][:40])
        if entry.get("rtsp", {}).get("server"):
            details.append(f"rtsp={entry['rtsp']['server']}")
        if entry.get("exposures"):
            details.append("exposure=" + ",".join(entry["exposures"]))
        print(
            f"{entry['ip']:<16} "
            f"{(entry.get('vendor') or 'unknown')[:21]:<22} "
            f"{ports[:27]:<28} "
            f"{entry['identity'][:23]:<24} "
            f"{'; '.join(details)[:90]}"
        )
    if result["ssdp"]:
        print("\nSSDP responses:")
        for item in result["ssdp"]:
            print(f"  {item['ip']:<16} {item['st'][:40]:<42} {item['server'][:40]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Deeper LAN port/service scan")
    parser.add_argument("--sweep", action="store_true", help="ICMP sweep first")
    parser.add_argument(
        "--full", action="store_true", help="scan ports 1-2048 on alive hosts"
    )
    parser.add_argument(
        "--hosts",
        default="",
        help="comma-separated IPs; scan only these (targeted mode)",
    )
    parser.add_argument("--workers", type=int, default=192)
    parser.add_argument("--timeout", type=float, default=0.4)
    parser.add_argument(
        "--ports", default="", help="comma-separated port list override"
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    ports = PORTS
    if args.ports:
        ports = sorted({int(p) for p in args.ports.split(",") if p.strip().isdigit()})

    target_hosts = [ip.strip() for ip in args.hosts.split(",") if ip.strip()] or None
    result = scan(
        sweep=args.sweep,
        ports=ports,
        timeout=args.timeout,
        workers=args.workers,
        full=args.full,
        hosts=target_hosts,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print_results(result)
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()
