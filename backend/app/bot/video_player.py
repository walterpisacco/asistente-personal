"""Ventana única de TORI: fotos en espera y el video de YouTube en el mismo lugar.

Usa Google Chrome/Chromium real (no el WebView de Qt): Google bloquea el login
en navegadores embebidos. El perfil queda en data/youtube-chrome/.

Arranca con el bot. `ver_video` abre el video; `detener_video` vuelve a las fotos.
El proceso es aparte para no bloquear el loop de audio.
"""

from __future__ import annotations

import argparse
import atexit
import json
import logging
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger("tori.video")

_BACKEND = Path(__file__).resolve().parents[2]
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,32}$")
_player_proc: subprocess.Popen | None = None
_showing_video = False
_lock = threading.Lock()
# Qt 6.5+ xcb (solo fallback si no hay Chrome).
_XCB_CURSOR_VENDOR = _BACKEND / "vendor" / "xcb-cursor"
# Perfil de Chrome real: cookies / login de YouTube.
_CHROME_PROFILE = _BACKEND / "data" / "youtube-chrome"
_IDLE_HTML_PATH = _CHROME_PROFILE / "tori-idle.html"

_CHROME_CANDIDATES = (
    "google-chrome-stable",
    "google-chrome",
    "chromium-browser",
    "chromium",
    "brave-browser",
)


def _qt_env(base: dict[str, str] | None = None) -> dict[str, str]:
    env = (base or os.environ).copy()
    env["PYTHONPATH"] = str(_BACKEND) + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault(
        "QTWEBENGINE_CHROMIUM_FLAGS",
        "--autoplay-policy=no-user-gesture-required",
    )
    if _XCB_CURSOR_VENDOR.is_dir():
        env["LD_LIBRARY_PATH"] = (
            str(_XCB_CURSOR_VENDOR) + os.pathsep + env.get("LD_LIBRARY_PATH", "")
        )
    return env


def _find_chrome() -> str | None:
    for name in _CHROME_CANDIDATES:
        path = shutil.which(name)
        if path:
            return path
    return None


def idle_html() -> str:
    seeds = random.sample(range(1, 900), 6)
    images = "\n".join(
        f'<img class="{"on" if i == 0 else ""}" src="https://picsum.photos/1280/800?random={seed}" alt="">'
        for i, seed in enumerate(seeds)
    )
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    html, body {{
      margin: 0; height: 100%; overflow: hidden;
      background: #12141a; color: #f4f1ea;
      font-family: "Iowan Old Style", Palatino, Georgia, serif;
    }}
    .aurora {{
      position: absolute; inset: -25%;
      background:
        radial-gradient(circle at 20% 30%, rgba(196, 148, 106, .45), transparent 42%),
        radial-gradient(circle at 80% 20%, rgba(90, 122, 158, .4), transparent 40%),
        radial-gradient(circle at 60% 80%, rgba(92, 64, 84, .45), transparent 46%);
      animation: drift 22s ease-in-out infinite alternate;
    }}
    img {{
      position: absolute; inset: 0; width: 100%; height: 100%;
      object-fit: cover; opacity: 0;
      transition: opacity 1.8s ease;
    }}
    img.on {{ opacity: 1; }}
    .veil {{
      position: absolute; inset: 0;
      background: linear-gradient(to top, rgba(10,10,14,.78), rgba(10,10,14,.12) 46%, rgba(10,10,14,.4));
    }}
    .clock {{
      position: absolute; top: 42px; right: 48px; z-index: 2;
      text-align: right;
    }}
    .clock .time {{
      margin: 0; font-size: 72px; font-weight: 500;
      letter-spacing: .06em; line-height: 1;
      font-variant-numeric: tabular-nums;
    }}
    .clock .date {{
      margin: 10px 0 0; font-family: system-ui, sans-serif;
      font-size: 18px; letter-spacing: .06em; opacity: .82;
      text-transform: capitalize;
    }}
    .caption {{
      position: absolute; left: 48px; bottom: 42px; z-index: 2;
    }}
    h1 {{
      margin: 0; font-size: 68px; font-weight: 500; letter-spacing: .22em;
    }}
    p {{
      margin: 10px 0 0; font-family: system-ui, sans-serif;
      font-size: 18px; letter-spacing: .04em; opacity: .86;
    }}
    a.login {{
      display: inline-block; margin-top: 18px;
      font-family: system-ui, sans-serif; font-size: 15px;
      color: #f4f1ea; opacity: .75; text-decoration: underline;
      text-underline-offset: 4px;
    }}
    a.login:hover {{ opacity: 1; }}
    @keyframes drift {{
      from {{ transform: translate3d(0,0,0) scale(1); }}
      to {{ transform: translate3d(-4%, 3%, 0) scale(1.08); }}
    }}
  </style>
</head>
<body>
  <div class="aurora"></div>
  {images}
  <div class="veil"></div>
  <div class="clock">
    <p class="time" id="clock-time">--:--</p>
    <p class="date" id="clock-date"></p>
  </div>
  <div class="caption">
    <h1>TORI</h1>
    <p>Te estoy escuchando..</p>
    <a class="login" href="https://www.youtube.com/">Iniciar sesión en YouTube</a>
  </div>
  <script>
    const frames = Array.from(document.querySelectorAll("img"));
    let index = 0;
    if (frames.length) {{
      setInterval(() => {{
        frames[index].classList.remove("on");
        index = (index + 1) % frames.length;
        frames[index].classList.add("on");
      }}, 8000);
    }}
    const timeEl = document.getElementById("clock-time");
    const dateEl = document.getElementById("clock-date");
    const dateFmt = new Intl.DateTimeFormat("es-AR", {{
      weekday: "long", day: "numeric", month: "long", year: "numeric"
    }});
    function tick() {{
      const now = new Date();
      const hh = String(now.getHours()).padStart(2, "0");
      const mm = String(now.getMinutes()).padStart(2, "0");
      timeEl.textContent = hh + ":" + mm;
      dateEl.textContent = dateFmt.format(now);
    }}
    tick();
    setInterval(tick, 1000);
  </script>
</body>
</html>
"""


def watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}&autoplay=1"


def _has_display() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _alive() -> bool:
    return _player_proc is not None and _player_proc.poll() is None


def _spawn() -> None:
    global _player_proc
    if not _has_display():
        raise RuntimeError("No hay pantalla gráfica para abrir la ventana")
    env = _qt_env()
    _player_proc = subprocess.Popen(
        [sys.executable, "-m", "app.bot.video_player", "--listen"],
        cwd=str(_BACKEND),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    time.sleep(0.5)
    if _player_proc.poll() is not None:
        err = ""
        try:
            err = (_player_proc.stderr.read() or "").strip()
        except OSError:
            pass
        code = _player_proc.returncode
        _player_proc = None
        hint = ""
        if "xcb-cursor" in err or 'Could not load the Qt platform plugin "xcb"' in err:
            hint = " Instalá: sudo apt install libxcb-cursor0"
        raise RuntimeError(
            f"La ventana TORI salió al arrancar (código {code}).{hint}"
            + (f"\n{err}" if err else "")
        )
    logger.info("Ventana TORI abierta (pid=%s)", _player_proc.pid)


def _send(message: dict) -> None:
    global _player_proc
    with _lock:
        if not _alive():
            _spawn()
        assert _player_proc is not None and _player_proc.stdin is not None
        try:
            _player_proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            _player_proc.stdin.flush()
        except (BrokenPipeError, OSError):
            if _player_proc is not None and _player_proc.poll() is None:
                _player_proc.kill()
            _spawn()
            assert _player_proc is not None and _player_proc.stdin is not None
            _player_proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            _player_proc.stdin.flush()


def is_showing_video() -> bool:
    """True si la ventana está mostrando un video de YouTube."""
    return _showing_video and _alive()


def show_idle() -> None:
    """Muestra la pantalla de fotos. La crea si todavía no está abierta."""
    global _showing_video
    _send({"cmd": "idle"})
    _showing_video = False


def open_video(video_id: str, title: str = "") -> None:
    """Pone el video en la ventana que ya está abierta."""
    global _showing_video
    video_id = (video_id or "").strip()
    if not _VIDEO_ID_RE.fullmatch(video_id):
        raise ValueError(f"id de video inválido: {video_id!r}")
    _send({"cmd": "play", "id": video_id, "title": title or "YouTube"})
    _showing_video = True
    logger.info("Video en ventana id=%s", video_id)


def return_to_idle() -> bool:
    """Vuelve a las fotos si había un video. La ventana sigue abierta."""
    global _showing_video
    was_playing = _showing_video and _alive()
    if _alive() or _has_display():
        try:
            show_idle()
        except Exception:
            _showing_video = False
            raise
    _showing_video = False
    return was_playing


def close_video() -> bool:
    """Cierra el proceso de la ventana (al salir del bot)."""
    global _player_proc, _showing_video
    with _lock:
        proc = _player_proc
        _player_proc = None
        _showing_video = False
    if proc is None or proc.poll() is not None:
        return False
    if proc.stdin is not None:
        try:
            proc.stdin.close()
        except OSError:
            pass
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)
    logger.info("Ventana TORI cerrada")
    return True


atexit.register(close_video)


def _write_idle_html() -> Path:
    _CHROME_PROFILE.mkdir(parents=True, exist_ok=True)
    _IDLE_HTML_PATH.write_text(idle_html(), encoding="utf-8")
    return _IDLE_HTML_PATH


def _stop_proc(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)


def _run_window_chrome(chrome: str) -> None:
    """Ventana TORI con Chrome real (login de Google permitido)."""
    chrome_proc: subprocess.Popen | None = None

    def open_url(url: str) -> None:
        nonlocal chrome_proc
        _stop_proc(chrome_proc)
        # Misma carpeta de perfil → la sesión de YouTube se conserva.
        chrome_proc = subprocess.Popen(
            [
                chrome,
                f"--user-data-dir={_CHROME_PROFILE}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-features=TranslateUI",
                "--autoplay-policy=no-user-gesture-required",
                "--start-maximized",
                f"--app={url}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("Chrome TORI url=%s pid=%s", url[:80], chrome_proc.pid)

    def show_gallery() -> None:
        path = _write_idle_html()
        open_url(path.resolve().as_uri())

    def show_video(video_id: str, title: str) -> None:
        if not _VIDEO_ID_RE.fullmatch(video_id or ""):
            return
        open_url(watch_url(video_id))

    show_gallery()
    try:
        for raw in sys.stdin:
            raw = raw.strip()
            if not raw:
                continue
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            cmd = message.get("cmd")
            if cmd == "play":
                show_video(str(message.get("id") or ""), str(message.get("title") or ""))
            elif cmd == "idle":
                show_gallery()
    finally:
        _stop_proc(chrome_proc)


def _configure_view(view) -> None:
    from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings

    profile_dir = _BACKEND / "data" / "youtube-web"
    profile_dir.mkdir(parents=True, exist_ok=True)
    profile = QWebEngineProfile("tori-youtube", view)
    profile.setPersistentStoragePath(str(profile_dir))
    profile.setCachePath(str(profile_dir / "cache"))
    profile.setPersistentCookiesPolicy(
        QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
    )
    profile.setHttpUserAgent(
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    )

    class YoutubePage(QWebEnginePage):
        def createWindow(self, _type):  # noqa: N802
            popup = YoutubePage(self.profile(), self)

            def open_here(url) -> None:
                if url.isEmpty() or url.scheme() in ("", "about"):
                    return
                self.setUrl(url)
                popup.deleteLater()

            popup.urlChanged.connect(open_here)
            return popup

    view.setPage(YoutubePage(profile, view))
    settings = view.settings()
    settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
    settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, True)
    settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
    settings.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
    settings.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)
    settings.setAttribute(
        QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
    )


def _run_window_qt() -> None:
    """Fallback si no hay Chrome: WebView Qt (Google login suele fallar)."""
    os.environ.setdefault(
        "QTWEBENGINE_CHROMIUM_FLAGS",
        "--autoplay-policy=no-user-gesture-required",
    )
    from PyQt6.QtCore import QObject, QUrl, pyqtSignal
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWidgets import QApplication, QMainWindow

    logger.warning(
        "Sin Chrome/Chromium: uso Qt WebEngine. "
        "El login de Google suele estar bloqueado; instalá google-chrome."
    )

    app = QApplication(sys.argv)
    app.setApplicationName("TORI")

    window = QMainWindow()
    window.setWindowTitle("TORI")
    window.resize(1100, 640)
    view = QWebEngineView(window)
    _configure_view(view)
    window.setCentralWidget(view)

    def show_gallery() -> None:
        window.setWindowTitle("TORI")
        view.setHtml(idle_html(), QUrl("https://picsum.photos/"))

    def show_video(video_id: str, title: str) -> None:
        if not _VIDEO_ID_RE.fullmatch(video_id or ""):
            return
        window.setWindowTitle(title or "YouTube")
        view.setUrl(QUrl(watch_url(video_id)))

    class Bridge(QObject):
        play = pyqtSignal(str, str)
        idle = pyqtSignal()
        quit_app = pyqtSignal()

    bridge = Bridge()
    bridge.play.connect(show_video)
    bridge.idle.connect(show_gallery)
    bridge.quit_app.connect(app.quit)

    def read_commands() -> None:
        for raw in sys.stdin:
            raw = raw.strip()
            if not raw:
                continue
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            cmd = message.get("cmd")
            if cmd == "play":
                bridge.play.emit(str(message.get("id") or ""), str(message.get("title") or ""))
            elif cmd == "idle":
                bridge.idle.emit()
        bridge.quit_app.emit()

    threading.Thread(target=read_commands, daemon=True).start()
    show_gallery()
    window.showMaximized()
    raise SystemExit(app.exec())


def _run_window() -> None:
    chrome = _find_chrome()
    if chrome:
        logger.info("Ventana TORI con Chrome: %s", chrome)
        _run_window_chrome(chrome)
        return
    _run_window_qt()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Ventana TORI: fotos o video de YouTube")
    parser.add_argument("--listen", action="store_true", help="Espera comandos por stdin")
    parser.add_argument("--id", default="", help="Id de YouTube para abrir directo")
    parser.add_argument("--title", default="YouTube")
    args = parser.parse_args(argv)
    if args.listen:
        _run_window()
        return
    if not _VIDEO_ID_RE.fullmatch(args.id):
        raise SystemExit("Indicá --listen o un --id de video válido")
    chrome = _find_chrome()
    if chrome:
        _write_idle_html()
        raise SystemExit(
            subprocess.call(
                [
                    chrome,
                    f"--user-data-dir={_CHROME_PROFILE}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--autoplay-policy=no-user-gesture-required",
                    "--start-maximized",
                    f"--app={watch_url(args.id)}",
                ]
            )
        )
    os.environ.setdefault(
        "QTWEBENGINE_CHROMIUM_FLAGS",
        "--autoplay-policy=no-user-gesture-required",
    )
    from PyQt6.QtCore import QUrl
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWidgets import QApplication, QMainWindow

    app = QApplication(sys.argv)
    window = QMainWindow()
    window.setWindowTitle(args.title)
    window.resize(1100, 640)
    view = QWebEngineView(window)
    _configure_view(view)
    window.setCentralWidget(view)
    view.setUrl(QUrl(watch_url(args.id)))
    window.showMaximized()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
