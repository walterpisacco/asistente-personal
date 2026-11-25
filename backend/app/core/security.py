"""Security helpers: tokens, IP extraction, private ranges, hashing."""

from __future__ import annotations

import ipaddress
import secrets
from typing import Optional

from starlette.requests import Request


APP_TOKEN_HEADER = "X-App-Token"


def is_dev_environment(app_env: str) -> bool:
    return app_env.lower() in {"development", "dev", "local", "test"}


def secure_compare(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return secrets.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def is_private_or_loopback(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return bool(
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
    )


def extract_client_ip(
    request: Request,
    *,
    trust_proxy: bool = True,
) -> str:
    """IP real del cliente. Con proxy de confianza usa X-Forwarded-For."""
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # Primer hop = cliente original
            first = forwarded.split(",")[0].strip()
            if first:
                return first
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()

    if request.client and request.client.host:
        return request.client.host
    return "0.0.0.0"


def extract_country_hint(request: Request) -> Optional[str]:
    """País si el edge (Cloudflare/nginx) ya lo resolvió."""
    for header in ("cf-ipcountry", "x-country-code", "x-geo-country"):
        value = request.headers.get(header)
        if value:
            code = value.strip().upper()
            if code and code not in {"XX", "T1"}:  # XX unknown, T1 tor
                return code
    return None
