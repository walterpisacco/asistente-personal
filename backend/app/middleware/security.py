"""Middleware de seguridad: token de app, rate limit y geo AR."""

from __future__ import annotations

import logging
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import Settings
from app.core.geoip import lookup_country_code
from app.core.rate_limit import rate_limiter
from app.core.security import (
    APP_TOKEN_HEADER,
    extract_client_ip,
    extract_country_hint,
    is_dev_environment,
    is_private_or_loopback,
    secure_compare,
)

logger = logging.getLogger(__name__)

# Paths públicos (sin token). Health debe quedar accesible para probes.
PUBLIC_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}

# Rutas caras: límite más estricto
EXPENSIVE_SUFFIXES = (
    "/audio",
    "/audio-base64",
)


class ApiSecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings) -> None:
        super().__init__(app)
        self.settings = settings

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        method = request.method.upper()

        if method == "OPTIONS":
            return await call_next(request)

        # Static TTS / avatares: <Audio> e <Image> no envían headers custom.
        if path.startswith("/static/") or path.startswith("/assets/"):
            return await call_next(request)

        client_ip = extract_client_ip(
            request, trust_proxy=self.settings.trust_proxy_headers
        )
        request.state.client_ip = client_ip

        # --- Geo restrict (Argentina) ---
        geo_error = self._check_geo(request, client_ip, path)
        if geo_error is not None:
            return geo_error

        # --- App token ---
        if (
            path not in PUBLIC_PATHS
            and not path.startswith("/static/")
            and not path.startswith("/assets/")
        ):
            auth_error = self._check_token(request, path)
            if auth_error is not None:
                return auth_error

        # --- Rate limit ---
        if path not in PUBLIC_PATHS:
            rl_error = self._check_rate_limit(client_ip, path)
            if rl_error is not None:
                return rl_error

        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    def _check_token(self, request: Request, path: str) -> Response | None:
        expected = (self.settings.app_api_token or "").strip()
        if not expected:
            # En producción exigir token configurado.
            if not is_dev_environment(self.settings.app_env):
                logger.error("APP_API_TOKEN vacío en producción — bloqueando %s", path)
                return JSONResponse(
                    status_code=503,
                    content={"detail": "API token not configured"},
                )
            return None

        # Solo X-App-Token: Authorization Bearer queda libre para el JWT de usuario.
        provided = (request.headers.get(APP_TOKEN_HEADER) or "").strip()
        if not secure_compare(provided, expected):
            logger.warning(
                "Unauthorized token ip=%s path=%s",
                getattr(request.state, "client_ip", "?"),
                path,
            )
            return JSONResponse(
                status_code=401,
                content={"detail": "Unauthorized"},
            )
        return None

    def _check_rate_limit(self, client_ip: str, path: str) -> Response | None:
        expensive = any(path.endswith(suffix) for suffix in EXPENSIVE_SUFFIXES)
        limit = (
            self.settings.rate_limit_expensive_per_minute
            if expensive
            else self.settings.rate_limit_per_minute
        )
        key = f"{'exp' if expensive else 'std'}:{client_ip}"
        if not rate_limiter.allow(key, limit, 60.0):
            logger.warning("Rate limit ip=%s path=%s", client_ip, path)
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Try again later."},
                headers={"Retry-After": "60"},
            )
        return None

    def _check_geo(
        self, request: Request, client_ip: str, path: str
    ) -> Response | None:
        if not self.settings.geo_restrict_enabled:
            return None
        if path in {"/health"}:
            return None

        allowed = {
            c.strip().upper()
            for c in self.settings.geo_restrict_countries.split(",")
            if c.strip()
        } or {"AR"}

        if is_private_or_loopback(client_ip):
            if self.settings.geo_allow_private or is_dev_environment(
                self.settings.app_env
            ):
                return None
            # En prod con geo activo, LAN del server solo si está permitido.
            return JSONResponse(
                status_code=403,
                content={"detail": "Access denied from this network"},
            )

        country = extract_country_hint(request)
        if not country:
            country = lookup_country_code(client_ip)

        if country == "LOCAL":
            if self.settings.geo_allow_private or is_dev_environment(
                self.settings.app_env
            ):
                return None

        if not country:
            # Fail-closed en prod, fail-open en dev.
            if is_dev_environment(self.settings.app_env):
                logger.warning("Geo unknown ip=%s — allowing (dev)", client_ip)
                return None
            logger.warning("Geo unknown ip=%s — denying", client_ip)
            return JSONResponse(
                status_code=403,
                content={"detail": "Access denied (geo)"},
            )

        if country not in allowed:
            logger.warning(
                "Geo blocked ip=%s country=%s path=%s", client_ip, country, path
            )
            return JSONResponse(
                status_code=403,
                content={"detail": "Access restricted to allowed regions"},
            )
        return None
