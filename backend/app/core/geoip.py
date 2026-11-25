"""Resolución de país por IP (cache + lookup HTTP, opcional MaxMind)."""

from __future__ import annotations

import logging
import time
from typing import Optional

import httpx

from app.core.security import is_private_or_loopback

logger = logging.getLogger(__name__)

# Cache simple: ip -> (country_code, expires_at)
_cache: dict[str, tuple[str, float]] = {}
_CACHE_TTL_SECONDS = 6 * 60 * 60


def _cache_get(ip: str) -> Optional[str]:
    item = _cache.get(ip)
    if not item:
        return None
    code, expires = item
    if time.time() > expires:
        _cache.pop(ip, None)
        return None
    return code


def _cache_set(ip: str, code: str) -> None:
    _cache[ip] = (code.upper(), time.time() + _CACHE_TTL_SECONDS)


def lookup_country_code(ip: str) -> Optional[str]:
    """
    Devuelve ISO 3166-1 alpha-2 (ej. AR) o None si no se pudo resolver.
    IPs privadas → 'LOCAL'.
    """
    if is_private_or_loopback(ip):
        return "LOCAL"

    cached = _cache_get(ip)
    if cached:
        return cached

    # ipapi.co free tier (sin key): country_code
    try:
        with httpx.Client(timeout=2.5) as client:
            response = client.get(f"https://ipapi.co/{ip}/country_code/")
            if response.status_code == 200:
                code = response.text.strip().upper()
                if code and len(code) == 2 and code.isalpha():
                    _cache_set(ip, code)
                    return code
            logger.warning(
                "Geo lookup failed ip=%s status=%s body=%s",
                ip,
                response.status_code,
                response.text[:80],
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Geo lookup error ip=%s: %s", ip, exc)

    return None
