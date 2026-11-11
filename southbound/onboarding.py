"""Shareable setup links carry only a short-lived device pairing grant."""
from urllib.parse import urlencode, urlsplit


def setup_link(base_url: str, code: str) -> str:
    url = urlsplit(base_url)
    if (url.scheme not in ("http", "https") or not url.hostname or url.username or url.password
            or url.path not in ("", "/") or url.query or url.fragment):
        raise ValueError("use a phone-reachable http(s) runtime root URL")
    return base_url.rstrip("/") + "/connect#" + urlencode({"code": code})
