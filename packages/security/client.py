"""Who is on the other end of a request: someone on this machine, or a client behind a proxy.

HOOD binds 127.0.0.1. In the container topology (docker-compose.yml) Caddy shares HOOD's network
namespace, so every proxied internet request ALSO arrives from 127.0.0.1. Treating "the peer is
loopback" as "the owner at the machine" therefore let any internet client:

- claim the Root Owner account before first-run setup (takeover), and
- share one rate-limit bucket with everyone else, so a stranger's failed logins locked the owner
  out of their own account.

``classify`` separates the cases:

- ``direct_local``: loopback peer and no forwarding header at all (a browser or tool on this host).
- forwarded through a proxy: never direct-local. The client address comes from ``X-Forwarded-For``
  only when the owner declared a trusted proxy in front of HOOD (``HOOD_TRUSTED_PROXY=1``, set by
  docker-compose.yml); otherwise HOOD can't tell clients apart and doesn't pretend to.

The rightmost ``X-Forwarded-For`` entry is used: it is the one appended by HOOD's own proxy
(Caddy replaces client-supplied ``X-Forwarded-*`` from untrusted peers), so a client can't pick
its own rate-limit bucket by sending a fake header.
"""
from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from typing import Any, List, Optional

# Any of these means a proxy (or a client imitating one) is involved: not a direct local request.
FORWARDING_HEADERS = ("Forwarded", "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto",
                      "X-Forwarded-Port", "X-Real-IP", "Via", "CF-Connecting-IP", "True-Client-IP",
                      "X-Client-IP", "X-Original-Forwarded-For")


@dataclass(frozen=True)
class ClientInfo:
    ip: str               # best-known client address (rate limiting, audit)
    direct_local: bool    # someone on this machine, not through any proxy
    forwarded: bool       # the request passed through a proxy
    secure: bool          # the client reached the (trusted) proxy over HTTPS


def trusted_proxy_enabled() -> bool:
    return os.environ.get("HOOD_TRUSTED_PROXY", "").strip().lower() in ("1", "true", "yes", "loopback")


def _ip(value: str) -> Optional[str]:
    text = (value or "").strip().strip('"')
    if text.startswith("[") and "]" in text:            # [2001:db8::1]:443
        text = text[1:text.index("]")]
    elif text.count(":") == 1:                           # 203.0.113.9:51234
        text = text.split(":", 1)[0]
    try:
        addr = ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return None
    mapped = getattr(addr, "ipv4_mapped", None)
    return str(mapped or addr)


def is_loopback(value: str) -> bool:
    ip = _ip(value)
    return bool(ip) and ipaddress.ip_address(ip).is_loopback


def _all(headers: Any, name: str) -> List[str]:
    getter = getattr(headers, "get_all", None)
    values = getter(name) if callable(getter) else None
    if values is None:
        one = headers.get(name) if headers is not None else None
        values = [one] if one else []
    return [v for v in values if v]


def classify(peer: str, headers: Any) -> ClientInfo:
    peer_ip = _ip(peer) or str(peer)
    forwarded = any(_all(headers, h) for h in FORWARDING_HEADERS)
    if not forwarded:
        return ClientInfo(ip=peer_ip, direct_local=is_loopback(peer_ip), forwarded=False, secure=False)
    if is_loopback(peer_ip) and trusted_proxy_enabled():
        hops = [h for v in _all(headers, "X-Forwarded-For") for h in v.split(",")]
        client = next((ip for ip in (_ip(h) for h in reversed(hops)) if ip), None)
        proto = ",".join(_all(headers, "X-Forwarded-Proto")).split(",")[-1].strip().lower()
        return ClientInfo(ip=client or "proxy-unknown", direct_local=False, forwarded=True,
                          secure=proto == "https")
    # A proxy HOOD wasn't told to trust (or a client faking one): never local, address unknown.
    return ClientInfo(ip=peer_ip, direct_local=False, forwarded=True, secure=False)


def host_matches(host_header: str, allowed: str) -> bool:
    """Host allowlist match that treats the default HTTPS/HTTP port as optional.

    Through the proxy the browser sends ``Host: hood.example.com``; an allowlist entry written as
    ``hood.example.com:443`` (the documented form) must still match, or every request is refused
    and the owner is locked out of HOOD."""
    def split(value: str):
        value = (value or "").strip().lower().rstrip(".")
        if value.startswith("["):
            end = value.find("]")
            return value[:end + 1], value[end + 2:] if value[end + 1:end + 2] == ":" else ""
        if value.count(":") == 1:
            name, port = value.split(":")
            return name, port
        return value, ""
    h_name, h_port = split(host_header)
    a_name, a_port = split(allowed)
    if not h_name or h_name != a_name:
        return False
    if h_port == a_port:
        return True
    defaults = {"", "443", "80"}
    return h_port in defaults and a_port in defaults
