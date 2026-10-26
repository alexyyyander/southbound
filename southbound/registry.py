#!/usr/bin/env python3
"""Device classification heuristics for the Southbound.

The goal is not precise fingerprinting: it is to answer two practical
questions for every device seen on the LAN:

  1. Can this class of hardware host an on-device agent, and at what level?
  2. If not, what data/control paths are available to a LAN orchestrator?

Vendor lookup uses the IEEE OUI registry (cached in data/oui.csv) with a
small offline fallback. Everything else is a transparent rule set, and
unknown devices are reported as unknown rather than guessed.
"""

from __future__ import annotations

import csv
import re
import urllib.request
from pathlib import Path

from .paths import DATA_DIR

OUI_CACHE_PATH = DATA_DIR / "oui.csv"
OUI_URL = "https://standards-oui.ieee.org/oui/oui.csv"

# Small curated fallback so the demo still works offline. Not exhaustive.
FALLBACK_OUI = {
    "240AC4": "Espressif Inc.",
    "2462AB": "Espressif Inc.",
    "246F28": "Espressif Inc.",
    "30AEA4": "Espressif Inc.",
    "3C6105": "Espressif Inc.",
    "5CCF7F": "Espressif Inc.",
    "84F3EB": "Espressif Inc.",
    "A020A6": "Espressif Inc.",
    "AC67B2": "Espressif Inc.",
    "C44F33": "Espressif Inc.",
    "D8A01D": "Espressif Inc.",
    "ECFA3B": "Espressif Inc.",
    "B827EB": "Raspberry Pi Foundation",
    "DCA632": "Raspberry Pi Trading Ltd.",
    "E45F01": "Raspberry Pi Trading Ltd.",
    "D83ADD": "Raspberry Pi Trading Ltd.",
    "001B63": "Apple, Inc.",
    "001EC2": "Apple, Inc.",
    "0023DF": "Apple, Inc.",
    "002608": "Apple, Inc.",
    "040CCE": "Apple, Inc.",
    "041552": "Apple, Inc.",
    "0C4DE9": "Apple, Inc.",
    "109ADD": "Apple, Inc.",
    "3C22FB": "Apple, Inc.",
    "5C97F3": "Apple, Inc.",
    "F0DBF8": "Apple, Inc.",
    "001788": "Philips Lighting BV",
    "001132": "Demo Storage",
    "000E58": "Sonos, Inc.",
    "18B430": "Nest Labs Inc.",
    "001A11": "Google, Inc.",
    "00E0FC": "Huawei Technologies Co.,Ltd",
    "50C7BF": "TP-LINK TECHNOLOGIES CO.,LTD.",
    "001E75": "LG Electronics",
    "0012FB": "Samsung Electronics Co.,Ltd",
    "0013A9": "Sony Corporation",
    "44D9E7": "Ubiquiti Inc.",
    "204E7F": "NETGEAR",
    "001FC6": "ASUSTek COMPUTER INC.",
}

TIER_INFO = {
    "T0": ("T0 端点", "只上报/执行，不跑 agent"),
    "T1": ("T1 微控 + 微型 NN", "唤醒词/意图分类/异常检测（KB 到几 MB 模型）"),
    "T2": ("T2 Linux SoC", "0.5B–3B 量化 LLM + 完整工具调用循环"),
    "T3": ("T3 边缘 GPU/NPU", "3B–8B+ 多模态 agent"),
    "?": ("? 未确定", "需要主动指纹或人工确认"),
}

_OUI_DB: dict[str, str] | None = None


def normalize_mac(mac: str) -> str:
    """Normalize a MAC to 12 uppercase hex characters (no separators)."""
    parts = re.split(r"[:\-.]", mac.strip())
    return "".join(p.zfill(2) for p in parts).upper()


def _parse_oui_csv(path: Path) -> dict[str, str]:
    db: dict[str, str] = {}
    with path.open(newline="", encoding="utf-8", errors="ignore") as fh:
        for row in csv.DictReader(fh):
            assignment = (row.get("Assignment") or "").strip().upper()
            org = (row.get("Organization Name") or "").strip()
            if len(assignment) == 6 and org:
                db[assignment] = org
    return db


def _download_oui(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    req = urllib.request.Request(OUI_URL, headers={"User-Agent": "southbound/0.6"})
    with urllib.request.urlopen(req, timeout=12) as resp, tmp.open("wb") as out:
        while True:
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            out.write(chunk)
    tmp.replace(path)


def _load_oui_db() -> dict[str, str]:
    global _OUI_DB
    if _OUI_DB is not None:
        return _OUI_DB

    db: dict[str, str] = {}
    if OUI_CACHE_PATH.exists():
        try:
            db.update(_parse_oui_csv(OUI_CACHE_PATH))
        except Exception:
            pass
    else:
        try:
            _download_oui(OUI_CACHE_PATH)
            db.update(_parse_oui_csv(OUI_CACHE_PATH))
        except Exception:
            pass
    db.update(FALLBACK_OUI)
    _OUI_DB = db
    return db


def vendor_of(mac: str | None) -> str | None:
    if not mac:
        return None
    hexmac = normalize_mac(mac)
    if len(hexmac) < 6:
        return None
    return _load_oui_db().get(hexmac[:6])


def _vendor_profile(vendor: str) -> dict | None:
    v = vendor.lower()

    if "espressif" in v:
        return dict(
            kind="ESP32 系列 MCU",
            agent_tier="T1",
            chip_hint="ESP32 / ESP8266（视型号）",
            control_paths=["ESPHome / MQTT / 自定义固件"],
            data_paths=["MQTT 遥测", "ESPHome 原生 API"],
            notes="可跑唤醒词/意图/异常检测等微型模型；跑不了通用 LLM。",
        )
    if "raspberry pi" in v:
        return dict(
            kind="Linux 单板机",
            agent_tier="T2",
            chip_hint="BCM2711 / BCM2712",
            control_paths=["SSH / 本地 API / Home Assistant"],
            data_paths=["MQTT", "文件日志", "GPIO"],
            notes="可作局域网编排主机；Pi 5 可跑 0.5B–1.5B 量化模型。",
        )
    if "apple" in v:
        return dict(
            kind="Apple 设备/配件",
            agent_tier="T0",
            chip_hint="Apple 自研 SoC（封闭）",
            control_paths=["HomeKit / AirPlay（需配对）"],
            data_paths=["HomeKit 事件", "AirPlay 元数据"],
            notes="生态封闭，本地接口有限；Mac 类设备可另判为 T2/T3。",
        )
    if any(
        k in v
        for k in (
            "tp-link",
            "netgear",
            "asus",
            "ubiquiti",
            "huawei",
            "zte",
            "tenda",
            "d-link",
            "linksys",
            "mercury",
        )
    ):
        return dict(
            kind="路由器/网关",
            agent_tier="T0",
            chip_hint="网络 SoC",
            control_paths=["UPnP IGD / 管理页面"],
            data_paths=["UPnP 状态", "SNMP", "syslog"],
            notes="通常只做转发；能提供网络拓扑与部分状态。",
        )
    if "xiaomi" in v:
        return dict(
            kind="小米生态设备/网关",
            agent_tier="T0",
            chip_hint="米家 SoC / ESP 模组",
            control_paths=["MiIO 本地协议（需 token）", "米家云 API"],
            data_paths=["MiIO 属性上报", "网关子设备事件"],
            notes="本地接入需要提取 token；部分型号可刷第三方固件。",
        )
    if any(k in v for k in ("synology", "qnap")):
        return dict(
            kind="NAS",
            agent_tier="T2",
            chip_hint="Intel/AMD/ARM SoC",
            control_paths=["DSM/QTS API", "SSH", "Container"],
            data_paths=["SNMP", "系统日志", "应用 API"],
            notes="内存足够时可作为常驻编排主机。",
        )
    if "sonos" in v:
        return dict(
            kind="智能音箱",
            agent_tier="T0",
            chip_hint="封闭音频 SoC",
            control_paths=["UPnP/SOAP（本地）"],
            data_paths=["播放状态/音量"],
            notes="本地控制可用，但不能跑 agent。",
        )
    if any(k in v for k in ("samsung", "sony", "lg electronics", "hisense", "tcl")):
        return dict(
            kind="电视/显示设备",
            agent_tier="T0",
            chip_hint="电视 SoC",
            control_paths=["SSDP/DIAL", "厂商本地 API"],
            data_paths=["开关机/输入源状态"],
            notes="封闭生态；数据以状态为主。",
        )
    if "google" in v:
        return dict(
            kind="Chromecast / Nest",
            agent_tier="T0",
            chip_hint="封闭 SoC",
            control_paths=["CASTV2", "Google Home API"],
            data_paths=["eureka_info / 媒体状态"],
            notes="本地 Cast 可用；自动化依赖云。",
        )
    if "amazon" in v:
        return dict(
            kind="Echo / Ring 等",
            agent_tier="T0",
            chip_hint="封闭 SoC",
            control_paths=["Alexa 云 API"],
            data_paths=["云事件（需账号）"],
            notes="基本无本地开放接口。",
        )
    if "signify" in v or "philips" in v:
        return dict(
            kind="Hue 桥 / 灯具",
            agent_tier="T0",
            chip_hint="Zigbee 网关 SoC",
            control_paths=["Hue Bridge 本地 API"],
            data_paths=["Zigbee 子设备事件（经 bridge）"],
            notes="桥本身可作为 Zigbee 数据入口。",
        )
    if "tuya" in v:
        return dict(
            kind="Tuya 生态设备",
            agent_tier="T0",
            chip_hint="Tuya 模组（常为 ESP/Realtek）",
            control_paths=["Tuya 本地协议（需 local key）", "云 API"],
            data_paths=["DP 状态上报"],
            notes="本地接入需提取 local key；云依赖明显。",
        )
    if any(
        k in v
        for k in (
            "brother",
            "canon",
            "hewlett",
            "hp inc",
            "epson",
            "xerox",
            "kyocera",
            "ricoh",
        )
    ):
        return dict(
            kind="打印机",
            agent_tier="T0",
            chip_hint="打印机 SoC",
            control_paths=["IPP"],
            data_paths=["IPP 状态", "SNMP 耗材/计数"],
            notes="数据信号以状态和耗材为主。",
        )
    if any(k in v for k in ("nintendo", "sony interactive")):
        return dict(
            kind="游戏机",
            agent_tier="T0",
            chip_hint="封闭 SoC",
            control_paths=["厂商协议"],
            data_paths=["在线状态"],
            notes="不适合作为自动化节点。",
        )
    if "quectel" in v:
        return dict(
            kind="蜂窝模块/CPE",
            agent_tier="T0",
            chip_hint="Quectel 蜂窝模组",
            control_paths=["厂商云 / AT 指令"],
            data_paths=["信号强度/流量（需模组权限）"],
            notes="通常是 4G/5G CPE 或模组，不是通用计算节点。",
        )
    if "fn-link" in v:
        return dict(
            kind="Wi-Fi 模组设备",
            agent_tier="?",
            chip_hint="FN-Link Wi-Fi 模组（常配 Realtek/ESP）",
            control_paths=["未知"],
            data_paths=["ARP/SSDP/mDNS 元数据"],
            notes="模组厂商，需确认整机类型。",
        )
    if "guangzhou shiyuan" in v or "cvte" in v:
        return dict(
            kind="智能屏/电视",
            agent_tier="T0",
            chip_hint="CVTE 显示 SoC",
            control_paths=["厂商 API / 遥控"],
            data_paths=["状态/输入源"],
            notes="封闭生态，数据有限。",
        )
    if "intel" in v:
        return dict(
            kind="通用计算机（Intel 网卡）",
            agent_tier="T2",
            chip_hint="x86/ARM SoC",
            control_paths=["SSH / 系统 API / SMB"],
            data_paths=["系统日志", "SNMP", "SMB 共享"],
            notes="若为 PC/服务器，可作编排节点；嵌入式设备需确认。",
        )
    if any(k in v for k in ("broadcom", "realtek", "azurewave", "murata")):
        return dict(
            kind="通用计算机/网卡",
            agent_tier="?",
            chip_hint="x86/ARM SoC 或 Wi-Fi 模组",
            control_paths=["SSH / 系统 API"],
            data_paths=["系统日志", "SNMP"],
            notes="需看具体是什么设备；可能具备 T2 能力。",
        )
    return None


def _service_rule(services: list[str]) -> dict | None:
    s = " ".join(services)

    if "esphomelib" in s:
        return dict(
            kind="ESPHome 节点",
            agent_tier="T1",
            chip_hint="ESP32 / ESP8266",
            control_paths=["ESPHome 原生 API", "MQTT"],
            data_paths=["MQTT 传感器流", "ESPHome 状态"],
            notes="原生可编程，是最容易纳入编排的一类。",
        )
    if "home-assistant" in s:
        return dict(
            kind="Home Assistant 主机",
            agent_tier="T2",
            chip_hint="取决于宿主（Pi / NUC / NAS）",
            control_paths=["HA REST / WebSocket"],
            data_paths=["HA 状态机与事件总线"],
            notes="可直接作为本项目的编排层。",
        )
    if "matter" in s:
        return dict(
            kind="Matter 设备",
            agent_tier="T1",
            chip_hint="ESP32-C6 / nRF52 / 专用 SoC",
            control_paths=["Matter controller（chip-tool / HA）"],
            data_paths=["Matter 属性订阅"],
            notes="标准协议，跨厂商接入成本最低。",
        )
    if "hap" in s:
        return dict(
            kind="HomeKit 配件",
            agent_tier="T0",
            chip_hint="配件 SoC",
            control_paths=["HAP 配对（需配对码）"],
            data_paths=["HAP 事件通知"],
            notes="可本地接入，但配对与权限由 Apple 生态约束。",
        )
    if any(k in s for k in ("companion-link", "workstation", "ssh")):
        return dict(
            kind="计算机",
            agent_tier="T2",
            chip_hint="x86/ARM SoC",
            control_paths=["SSH / 本地 API"],
            data_paths=["系统日志", "进程/文件"],
            notes="可作为编排主机或工具执行节点。",
        )
    if "airplay" in s or "raop" in s:
        return dict(
            kind="AirPlay 接收端",
            agent_tier="T0",
            chip_hint="封闭音频 SoC",
            control_paths=["AirPlay"],
            data_paths=["AirPlay 元数据（Now Playing）"],
            notes="可以读播放状态，控制能力有限。",
        )
    if "googlecast" in s:
        return dict(
            kind="Chromecast / Google 设备",
            agent_tier="T0",
            chip_hint="封闭 SoC",
            control_paths=["CASTV2"],
            data_paths=["eureka_info", "媒体状态"],
        )
    if any(k in s for k in ("ipp", "printer", "pdl-datastream")):
        return dict(
            kind="打印机",
            agent_tier="T0",
            chip_hint="打印机 SoC",
            control_paths=["IPP"],
            data_paths=["IPP 状态", "SNMP"],
        )
    if "sonos" in s:
        return dict(
            kind="Sonos 音箱",
            agent_tier="T0",
            chip_hint="封闭音频 SoC",
            control_paths=["UPnP/SOAP"],
            data_paths=["播放状态"],
        )
    if "mqtt" in s:
        return dict(
            kind="MQTT broker",
            agent_tier="T2",
            chip_hint="取决于宿主",
            control_paths=["MQTT"],
            data_paths=["全部已接入主题"],
            notes="数据总线本身；已有 broker 时优先接它。",
        )
    return None


def _ssdp_rule(ssdp_types: list[str]) -> dict | None:
    s = " ".join(ssdp_types)
    if "internetgatewaydevice" in s:
        return dict(
            kind="路由器/网关（UPnP IGD）",
            agent_tier="T0",
            chip_hint="网络 SoC",
            control_paths=["UPnP IGD"],
            data_paths=["外网 IP/端口映射状态", "SNMP"],
        )
    if "mediarenderer" in s:
        return dict(
            kind="媒体渲染设备",
            agent_tier="T0",
            chip_hint="电视/音箱 SoC",
            control_paths=["UPnP AVTransport"],
            data_paths=["播放状态"],
        )
    if "mediaserver" in s:
        return dict(
            kind="媒体服务器/NAS",
            agent_tier="T2",
            chip_hint="NAS SoC",
            control_paths=["UPnP ContentDirectory"],
            data_paths=["媒体库索引"],
        )
    return None


def _merge(result: dict, part: dict | None) -> None:
    if not part:
        return
    for key, value in part.items():
        if key in ("data_paths", "control_paths") and isinstance(value, list):
            merged = list(dict.fromkeys((result.get(key) or []) + value))
            result[key] = merged
        elif key == "notes" and value:
            result["notes"] = (result.get("notes") or "").strip()
            result["notes"] = f"{result['notes']} {value}".strip()
        elif value not in (None, "", []):
            result[key] = value


def _is_random_mac(mac: str | None) -> bool:
    if not mac:
        return False
    try:
        first = int(mac.split(":")[0], 16)
    except (ValueError, IndexError):
        return False
    return bool(first & 0x02)


def classify(device: dict, mdns_types: tuple[str, ...] | list[str] = ()) -> dict:
    vendor = (device.get("manufacturer") or device.get("vendor") or "").strip()
    services = [str(x).lower() for x in (device.get("services") or [])]
    services += [str(x).lower() for x in mdns_types]
    ssdp_types = [str(x).lower() for x in (device.get("ssdp_types") or [])]

    vendor_part = _vendor_profile(vendor) if vendor else None
    service_part = _service_rule(services)
    ssdp_part = _ssdp_rule(ssdp_types)

    result: dict = {
        "kind": "未知设备",
        "agent_tier": "?",
        "chip_hint": "未知",
        "control_paths": [],
        "data_paths": [],
        "confidence": "low",
        "notes": "",
    }
    # Priority: service hint > vendor > SSDP.
    _merge(result, ssdp_part)
    _merge(result, vendor_part)
    _merge(result, service_part)

    text_l = (
        " ".join(
            str(device.get(key) or "")
            for key in ("vendor", "manufacturer", "model", "friendly_name", "hostname")
        ).lower()
        + " "
        + " ".join(services)
    )

    model = str(device.get("model") or "").lower()
    apple_like = (
        "apple" in vendor.lower()
        or model.startswith("mac")
        or any(key in text_l for key in ("macbook", "mac mini", "mac studio", "imac"))
    )
    if apple_like:
        if (
            "apple tv" in text_l
            or "homepod" in text_l
            or model.startswith("audioaccessory")
        ):
            result.update(
                kind="Apple TV/HomePod",
                agent_tier="T0",
                chip_hint="Apple 封闭 SoC",
                control_paths=["AirPlay/HomeKit"],
                data_paths=["AirPlay 元数据"],
                notes="媒体端点，不是 agent 主机。",
            )
        elif model.startswith("mac") or any(
            key in text_l for key in ("macbook", "mac mini", "mac studio", "imac")
        ):
            result.update(
                kind="Mac 计算机",
                agent_tier="T3",
                chip_hint="Apple Silicon（视型号）",
                control_paths=["SSH/本机 API"],
                data_paths=["系统日志/屏幕/音频"],
                notes="可作编排/推理主机。",
            )

    if result["kind"] == "未知设备" and _is_random_mac(device.get("mac")):
        result.update(
            kind="手机/便携设备（随机 MAC）",
            agent_tier="T0",
            chip_hint="移动 SoC / 随机 MAC",
            control_paths=["通常无本地开放端口"],
            data_paths=["BLE / 厂商云 API（需配对）"],
            confidence="low",
            notes="随机 MAC，通常是手机/平板；需 DHCP 租约或端点确认。",
        )

    if service_part:
        result["confidence"] = "high"
    elif vendor_part or ssdp_part:
        result["confidence"] = "medium"

    generic = "ARP/SSDP/mDNS 元数据"
    if generic not in result["data_paths"]:
        result["data_paths"].append(generic)
    if not result["control_paths"]:
        result["control_paths"] = ["未知（需主动指纹确认）"]

    return result
