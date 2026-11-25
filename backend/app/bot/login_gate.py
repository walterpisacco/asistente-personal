"""Pantalla de login del bot (usuario/contraseña → sesión local)."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from fastapi import HTTPException

from app.bot.session import (
    BotSession,
    cached_credentials,
    load_session,
    save_session,
)
from app.core.auth import decode_access_token
from app.core.config import Settings, get_settings
from app.core.database import SessionLocal
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.schemas.user import LoginRequest
from app.services.auth_service import AuthService

logger = logging.getLogger("tori.login")

_BACKEND = Path(__file__).resolve().parents[2]
_CHROME_PROFILE = _BACKEND / "data" / "youtube-chrome"
_CHROME_CANDIDATES = (
    "google-chrome-stable",
    "google-chrome",
    "chromium-browser",
    "chromium",
    "brave-browser",
)


def _find_chrome() -> str | None:
    for name in _CHROME_CANDIDATES:
        path = shutil.which(name)
        if path:
            return path
    return None


def _login_html(username: str = "", remember: bool = True, error: str = "") -> str:
    user_val = (
        username.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
    )
    err_block = (
        f'<p class="error" id="error">{error}</p>'
        if error
        else '<p class="error" id="error" hidden></p>'
    )
    remember_attr = "checked" if remember else ""
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TORI · Iniciar sesión</title>
  <style>
    :root {{
      --bg: #0b1220;
      --panel: rgba(16, 24, 40, 0.92);
      --line: rgba(148, 163, 184, 0.22);
      --text: #e8eef7;
      --muted: #93a4b8;
      --accent: #3dd6c6;
      --accent-2: #2bb3d6;
      --danger: #ff7b7b;
      --field: rgba(255,255,255,0.04);
    }}
    * {{ box-sizing: border-box; }}
    html, body {{
      margin: 0; min-height: 100%;
      font-family: "Segoe UI", "Helvetica Neue", sans-serif;
      color: var(--text);
      background:
        radial-gradient(1200px 600px at 10% -10%, rgba(61,214,198,0.18), transparent 55%),
        radial-gradient(900px 500px at 100% 0%, rgba(43,179,214,0.16), transparent 50%),
        linear-gradient(160deg, #071018 0%, var(--bg) 45%, #101828 100%);
    }}
    body {{
      display: grid;
      place-items: center;
      padding: 32px 16px;
    }}
    .panel {{
      width: min(420px, 100%);
      padding: 36px 32px 28px;
      border: 1px solid var(--line);
      border-radius: 24px;
      background: var(--panel);
      backdrop-filter: blur(16px);
      box-shadow: 0 30px 80px rgba(0,0,0,0.35);
      animation: rise 0.45s ease-out both;
    }}
    @keyframes rise {{
      from {{ opacity: 0; transform: translateY(12px); }}
      to {{ opacity: 1; transform: none; }}
    }}
    .brand {{
      margin: 0 0 6px;
      font-size: 13px;
      letter-spacing: 0.22em;
      text-transform: uppercase;
      color: var(--accent);
      font-weight: 700;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 28px;
      line-height: 1.15;
      font-weight: 650;
    }}
    .lead {{
      margin: 0 0 22px;
      color: var(--muted);
      font-size: 15px;
      line-height: 1.45;
    }}
    label {{
      display: block;
      margin: 0 0 14px;
      font-size: 13px;
      color: var(--muted);
    }}
    input[type="text"], input[type="password"], input[type="email"] {{
      width: 100%;
      margin-top: 6px;
      padding: 14px 14px;
      border-radius: 14px;
      border: 1px solid var(--line);
      background: var(--field);
      color: var(--text);
      font-size: 16px;
      outline: none;
    }}
    input:focus {{
      border-color: rgba(61,214,198,0.65);
      box-shadow: 0 0 0 3px rgba(61,214,198,0.15);
    }}
    .password-row {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 8px;
      align-items: center;
      margin-top: 6px;
    }}
    .password-row input {{ margin-top: 0; }}
    .toggle {{
      border: 1px solid var(--line);
      background: transparent;
      color: var(--muted);
      border-radius: 12px;
      padding: 12px 12px;
      cursor: pointer;
      font-size: 13px;
    }}
    .remember {{
      display: flex;
      align-items: center;
      gap: 10px;
      margin: 4px 0 18px;
      color: var(--muted);
      font-size: 14px;
      user-select: none;
    }}
    .remember input {{ width: 16px; height: 16px; accent-color: var(--accent); }}
    .error {{
      margin: 0 0 14px;
      padding: 10px 12px;
      border-radius: 12px;
      background: rgba(255,123,123,0.12);
      border: 1px solid rgba(255,123,123,0.35);
      color: #ffd0d0;
      font-size: 14px;
    }}
    button.submit {{
      width: 100%;
      border: 0;
      border-radius: 14px;
      padding: 14px 16px;
      font-size: 16px;
      font-weight: 650;
      color: #042028;
      cursor: pointer;
      background: linear-gradient(135deg, var(--accent), var(--accent-2));
    }}
    button.submit:disabled {{
      opacity: 0.65;
      cursor: wait;
    }}
    .foot {{
      margin: 18px 0 0;
      text-align: center;
      color: var(--muted);
      font-size: 12px;
    }}
  </style>
</head>
<body>
  <form class="panel" id="login-form" method="post" action="/api/login" autocomplete="on">
    <p class="brand">TORI</p>
    <h1>Iniciar sesión</h1>
    <p class="lead">Usá las mismas credenciales de la app para hablar con tu asistente.</p>
    {err_block}
    <label>
      Usuario o email
      <input name="username" id="username" type="text" autocomplete="username"
             required value="{user_val}" autofocus>
    </label>
    <label>
      Contraseña
      <span class="password-row">
        <input name="password" id="password" type="password" autocomplete="current-password" required>
        <button class="toggle" type="button" id="toggle-pass" aria-label="Mostrar contraseña">Ver</button>
      </span>
    </label>
    <label class="remember">
      <input type="checkbox" name="remember" id="remember" {remember_attr}>
      Recordarme en este equipo
    </label>
    <button class="submit" id="submit" type="submit">Entrar</button>
    <p class="foot">Después vas a poder activarme diciendo «hola».</p>
  </form>
  <script>
    const form = document.getElementById("login-form");
    const errorEl = document.getElementById("error");
    const submit = document.getElementById("submit");
    const toggle = document.getElementById("toggle-pass");
    const password = document.getElementById("password");
    toggle.addEventListener("click", () => {{
      const show = password.type === "password";
      password.type = show ? "text" : "password";
      toggle.textContent = show ? "Ocultar" : "Ver";
    }});
    form.addEventListener("submit", async (ev) => {{
      ev.preventDefault();
      errorEl.hidden = true;
      errorEl.textContent = "";
      submit.disabled = true;
      submit.textContent = "Entrando…";
      const body = new URLSearchParams(new FormData(form));
      try {{
        const res = await fetch("/api/login", {{
          method: "POST",
          headers: {{ "Content-Type": "application/x-www-form-urlencoded" }},
          body,
        }});
        const data = await res.json().catch(() => ({{}}));
        if (!res.ok) {{
          errorEl.hidden = false;
          errorEl.textContent = data.detail || "Usuario o contraseña incorrectos.";
          submit.disabled = false;
          submit.textContent = "Entrar";
          return;
        }}
        document.body.innerHTML = "<div class='panel'><p class='brand'>TORI</p><h1>Listo</h1><p class='lead'>Sesión iniciada. Ya podés hablarme.</p></div>";
      }} catch (err) {{
        errorEl.hidden = false;
        errorEl.textContent = "No se pudo iniciar sesión. Revisá la conexión.";
        submit.disabled = false;
        submit.textContent = "Entrar";
      }}
    }});
  </script>
</body>
</html>
"""


def try_restore_user(settings: Settings | None = None) -> Optional[User]:
    """Si hay sesión válida, devuelve el User activo de la DB."""
    settings = settings or get_settings()
    session = load_session()
    if session is None or not session.access_token or session.user_id <= 0:
        return None
    try:
        payload = decode_access_token(session.access_token, settings)
        uid = int(payload.get("sub"))
    except Exception:
        logger.info("Sesión guardada inválida o vencida; pido login")
        return None
    db = SessionLocal()
    try:
        user = UserRepository(db).get_by_id(uid)
        if user is None or not user.is_active:
            return None
        # Mantener datos frescos en el archivo de sesión.
        save_session(
            BotSession(
                access_token=session.access_token,
                user_id=int(user.id),
                username=user.username,
                alias=user.alias or "",
                full_name=user.full_name or "",
                remembered_username=session.remembered_username,
                remembered_password=session.remembered_password,
            )
        )
        return user
    finally:
        db.close()


def authenticate(username: str, password: str, *, remember: bool = True) -> BotSession:
    db = SessionLocal()
    try:
        token = AuthService(db).login(
            LoginRequest(username=username.strip(), password=password)
        )
        user = token.user
        session = BotSession(
            access_token=token.access_token,
            user_id=int(user.id),
            username=user.username,
            alias=user.alias or "",
            full_name=user.full_name or "",
            remembered_username=username.strip() if remember else "",
            remembered_password=password if remember else "",
        )
        save_session(session)
        return session
    finally:
        db.close()


def load_user_from_session() -> Optional[User]:
    session = load_session()
    if session is None or session.user_id <= 0:
        return None
    db = SessionLocal()
    try:
        return UserRepository(db).get_by_id(session.user_id)
    finally:
        db.close()


def wait_for_login(settings: Settings | None = None) -> User:
    """Muestra la UI de login si hace falta y bloquea hasta autenticar."""
    settings = settings or get_settings()
    restored = try_restore_user(settings)
    if restored is not None:
        logger.info(
            "Sesión restaurada user_id=%s username=%s",
            restored.id,
            restored.username,
        )
        return restored

    host = (settings.bot_ui_host or "tori.local").strip() or "tori.local"
    port = int(settings.bot_ui_port or 8765)
    cached_user, _cached_pass = cached_credentials()
    done = threading.Event()
    result: dict[str, object] = {"user_id": None, "error": None}
    html = _login_html(username=cached_user, remember=True)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:  # noqa: A003
            return

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path in ("/", "/index.html", "/login"):
                self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
                return
            if path == "/api/session":
                payload = {
                    "authenticated": done.is_set() and result.get("user_id") is not None,
                    "user_id": result.get("user_id"),
                }
                self._send(
                    200,
                    json.dumps(payload).encode("utf-8"),
                    "application/json; charset=utf-8",
                )
                return
            self.send_error(404)

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path != "/api/login":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            form = parse_qs(raw, keep_blank_values=True)
            username = (form.get("username") or [""])[0].strip()
            password = (form.get("password") or [""])[0]
            remember = (form.get("remember") or [""])[0] in ("on", "true", "1", "yes")
            try:
                session = authenticate(username, password, remember=remember)
                result["user_id"] = session.user_id
                done.set()
                self._send(
                    200,
                    json.dumps(
                        {
                            "ok": True,
                            "user_id": session.user_id,
                            "username": session.username,
                            "alias": session.alias,
                        }
                    ).encode("utf-8"),
                    "application/json; charset=utf-8",
                )
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, str) else "Login inválido"
                if exc.status_code == 401:
                    detail = "Usuario o contraseña incorrectos."
                self._send(
                    int(exc.status_code),
                    json.dumps({"detail": detail}).encode("utf-8"),
                    "application/json; charset=utf-8",
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Error en login")
                self._send(
                    500,
                    json.dumps({"detail": f"Error interno: {exc}"}).encode("utf-8"),
                    "application/json; charset=utf-8",
                )

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    app_url = f"http://{host}:{port}/"
    logger.info("Login UI en %s", app_url)

    chrome = _find_chrome()
    chrome_proc: subprocess.Popen | None = None
    chrome_args: list[str] | None = None
    if chrome:
        _CHROME_PROFILE.mkdir(parents=True, exist_ok=True)
        width = max(0, int(settings.bot_ui_width or 0))
        height = max(0, int(settings.bot_ui_height or 0))
        chrome_args = [
            chrome,
            f"--user-data-dir={_CHROME_PROFILE}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=TranslateUI",
        ]
        if width > 0 and height > 0:
            chrome_args.append(f"--window-size={width},{height}")
            chrome_args.append("--window-position=80,60")
        else:
            chrome_args.append("--start-maximized")
        chrome_args.append(f"--app={app_url}")
        chrome_proc = subprocess.Popen(
            chrome_args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        logger.warning("No encontré Chrome; abrí manualmente %s", app_url)

    try:
        while not done.wait(timeout=0.5):
            if (
                chrome_args is not None
                and chrome_proc is not None
                and chrome_proc.poll() is not None
            ):
                # Si cerraron la ventana sin login, reabrir.
                logger.info("Ventana de login cerrada; reabriendo…")
                chrome_proc = subprocess.Popen(
                    chrome_args,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        time.sleep(0.4)
    finally:
        try:
            httpd.shutdown()
        except Exception:
            pass
        if chrome_proc is not None and chrome_proc.poll() is None:
            chrome_proc.terminate()
            try:
                chrome_proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                chrome_proc.kill()

    user_id = result.get("user_id")
    db = SessionLocal()
    try:
        user = UserRepository(db).get_by_id(int(user_id))  # type: ignore[arg-type]
        if user is None:
            raise RuntimeError("Login OK pero el usuario no está en la base")
        logger.info("Login OK user_id=%s username=%s", user.id, user.username)
        return user
    finally:
        db.close()
