"""Ventana única de TORI: fotos en espera y el video de YouTube en el mismo lugar.

Usa Google Chrome/Chromium real (no el WebView de Qt): Google bloquea el login
en navegadores embebidos. El perfil queda en data/youtube-chrome/.

Arranca con el bot. `ver_video` abre el video; `detener_video` vuelve a las fotos.
El proceso es aparte para no bloquear el loop de audio.
"""

from __future__ import annotations

import argparse
import atexit
import html
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

# Frases hablables para el panel idle (clave → plantilla; {valor} opcional).
_SUGGESTION_PHRASES: dict[str, str] = {
    "ver_video": "Poné música de {valor}",
    "detener_video": "Pará la música",
    "agregar_recordatorio": "Recordame ir al dentista mañana",
    "dame_recordatorios": "¿Qué recordatorios tengo?",
}
_SUGGESTION_ORDER = (
    "ver_video",
    "agregar_recordatorio",
    "dame_recordatorios",
    "detener_video",
)
_SUGGESTION_FALLBACK = [
    {"clave": "ver_video", "phrase": "Poné música de Madonna"},
    {"clave": "agregar_recordatorio", "phrase": "Recordame ir al dentista mañana"},
    {"clave": "dame_recordatorios", "phrase": "¿Qué recordatorios tengo?"},
    {"clave": "detener_video", "phrase": "Pará la música"},
]

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


def _phrase_for_action(clave: str, valor: str = "") -> str | None:
    template = _SUGGESTION_PHRASES.get(clave)
    if not template:
        return None
    if "{valor}" in template:
        sample = (valor or "").strip() or "Madonna"
        return template.replace("{valor}", sample)
    return template


def _load_action_suggestions() -> list[dict[str, str]]:
    """Sugerencias hablables desde la tabla actions; fallback estático si falla DB."""
    try:
        from app.core.database import SessionLocal
        from app.repositories.action_repository import ActionRepository

        db = SessionLocal()
        try:
            rows = ActionRepository(db).list_all()
        finally:
            db.close()
        by_clave = {r.clave.strip().lower(): r for r in rows}
        out: list[dict[str, str]] = []
        for clave in _SUGGESTION_ORDER:
            row = by_clave.get(clave)
            if row is None:
                continue
            phrase = _phrase_for_action(clave, row.valor or "")
            if phrase:
                out.append({"clave": clave, "phrase": phrase})
        if out:
            return out
    except Exception as exc:
        logger.warning("No pude cargar actions para idle: %s", exc)
    return list(_SUGGESTION_FALLBACK)


def idle_html(suggestions: list[dict[str, str]] | None = None) -> str:
    items = suggestions if suggestions is not None else _load_action_suggestions()
    seeds = random.sample(range(1, 900), 6)
    images = "\n".join(
        f'<img class="{"on" if i == 0 else ""}" '
        f'src="https://picsum.photos/1280/800?random={seed}" alt="">'
        for i, seed in enumerate(seeds)
    )
    chips = "\n".join(
        f'<li class="chip{" active" if i == 0 else ""}" data-i="{i}">'
        f'<span class="chip-label">Probá decir</span>'
        f'<span class="chip-phrase">{html.escape(item["phrase"])}</span>'
        f"</li>"
        for i, item in enumerate(items)
    )
    hero_phrase = html.escape(items[0]["phrase"]) if items else "Decime en qué te ayudo"
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root {{
      --bg0: #0b1c2c;
      --bg1: #123a4a;
      --bg2: #1a6b72;
      --text: #f2f7fa;
      --muted: rgba(242, 247, 250, .72);
      --chip: rgba(12, 28, 40, .42);
      --chip-active: rgba(90, 200, 210, .22);
      --accent: #7edce6;
      --card-radius: 22px;
    }}
    * {{ box-sizing: border-box; }}
    html, body {{
      margin: 0; height: 100%; overflow: hidden;
      color: var(--text);
      font-family: "Segoe UI", "Helvetica Neue", "Avenir Next", sans-serif;
      background: radial-gradient(ellipse at 40% 35%, var(--bg2) 0%, var(--bg1) 42%, var(--bg0) 100%);
    }}
    .shell {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: clamp(16px, 2.2vw, 28px);
      height: 100%;
      padding: clamp(18px, 2.4vw, 32px);
      position: relative;
      z-index: 1;
    }}
    .aurora {{
      position: absolute; inset: -20%;
      background:
        radial-gradient(circle at 18% 28%, rgba(80, 190, 200, .28), transparent 42%),
        radial-gradient(circle at 78% 18%, rgba(40, 110, 150, .35), transparent 40%),
        radial-gradient(circle at 55% 85%, rgba(20, 70, 90, .4), transparent 48%);
      animation: drift 24s ease-in-out infinite alternate;
      pointer-events: none;
      z-index: 0;
    }}
    .panel-left {{
      display: flex;
      flex-direction: column;
      min-width: 0;
      padding: clamp(8px, 1vw, 16px) 4px;
    }}
    .topbar {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: clamp(18px, 3vh, 36px);
    }}
    .brand {{
      margin: 0;
      font-size: clamp(42px, 7vw, 72px);
      font-weight: 650;
      letter-spacing: .14em;
      line-height: 1;
    }}
    .clock {{
      text-align: right;
      flex-shrink: 0;
    }}
    .clock .time {{
      margin: 0;
      font-size: clamp(28px, 4.2vw, 44px);
      font-weight: 500;
      letter-spacing: .04em;
      font-variant-numeric: tabular-nums;
      line-height: 1;
    }}
    .clock .date {{
      margin: 8px 0 0;
      font-size: clamp(12px, 1.4vw, 15px);
      color: var(--muted);
      text-transform: capitalize;
      max-width: 18ch;
    }}
    .hero {{
      flex: 1;
      display: flex;
      flex-direction: column;
      justify-content: center;
      min-height: 0;
    }}
    .hero-lead {{
      margin: 0 0 10px;
      font-size: clamp(15px, 1.8vw, 18px);
      color: var(--muted);
      letter-spacing: .02em;
    }}
    .hero-phrase {{
      margin: 0;
      font-size: clamp(26px, 3.4vw, 40px);
      font-weight: 560;
      line-height: 1.25;
      max-width: 18ch;
      transition: opacity .45s ease, transform .45s ease;
    }}
    .hero-phrase.swap {{
      opacity: 0;
      transform: translateY(8px);
    }}
    .suggestions {{
      list-style: none;
      margin: clamp(22px, 3.5vh, 40px) 0 0;
      padding: 0;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }}
    .chip {{
      display: flex;
      flex-direction: column;
      gap: 2px;
      padding: 12px 16px;
      border-radius: 16px;
      background: var(--chip);
      border: 1px solid transparent;
      opacity: .55;
      transition: opacity .4s ease, background .4s ease, border-color .4s ease,
        transform .4s ease;
    }}
    .chip.active {{
      opacity: 1;
      background: var(--chip-active);
      border-color: rgba(126, 220, 230, .45);
      transform: translateX(4px);
    }}
    .chip-label {{
      font-size: 11px;
      letter-spacing: .08em;
      text-transform: uppercase;
      color: var(--accent);
      opacity: .85;
    }}
    .chip-phrase {{
      font-size: clamp(14px, 1.6vw, 17px);
      font-weight: 500;
    }}
    .foot {{
      margin-top: auto;
      padding-top: 16px;
    }}
    .listen {{
      margin: 0 0 8px;
      font-size: 14px;
      color: var(--muted);
    }}
    a.login {{
      font-size: 13px;
      color: var(--muted);
      text-decoration: underline;
      text-underline-offset: 3px;
    }}
    a.login:hover {{ color: var(--text); }}
    .panel-right {{
      min-width: 0;
      display: flex;
      align-items: stretch;
    }}
    .photo-card {{
      position: relative;
      flex: 1;
      border-radius: var(--card-radius);
      overflow: hidden;
      box-shadow:
        0 18px 48px rgba(0, 0, 0, .38),
        0 2px 0 rgba(255, 255, 255, .06) inset;
      background: #0a1520;
    }}
    .photo-card img {{
      position: absolute; inset: 0;
      width: 100%; height: 100%;
      object-fit: cover;
      opacity: 0;
      transition: opacity 1.6s ease;
    }}
    .photo-card img.on {{ opacity: 1; }}
    .photo-veil {{
      position: absolute; inset: 0;
      background: linear-gradient(
        to top,
        rgba(8, 18, 28, .55),
        transparent 42%,
        rgba(8, 18, 28, .18)
      );
      pointer-events: none;
    }}
    .photo-caption {{
      position: absolute;
      left: 20px; bottom: 18px;
      font-size: 13px;
      letter-spacing: .04em;
      color: rgba(242, 247, 250, .88);
      text-shadow: 0 1px 8px rgba(0,0,0,.45);
    }}
    @keyframes drift {{
      from {{ transform: translate3d(0,0,0) scale(1); }}
      to {{ transform: translate3d(-3%, 2%, 0) scale(1.06); }}
    }}
    @media (max-width: 820px) {{
      .shell {{
        grid-template-columns: 1fr;
        grid-template-rows: 1fr 1.1fr;
      }}
      .hero-phrase {{ max-width: none; }}
    }}
  </style>
</head>
<body>
  <div class="aurora" aria-hidden="true"></div>
  <div class="shell">
    <section class="panel-left">
      <div class="topbar">
        <h1 class="brand">TORI</h1>
        <div class="clock">
          <p class="time" id="clock-time">--:--</p>
          <p class="date" id="clock-date"></p>
        </div>
      </div>
      <div class="hero">
        <p class="hero-lead">Te estoy escuchando</p>
        <p class="hero-phrase" id="hero-phrase">{hero_phrase}</p>
        <ul class="suggestions" id="suggestions">
          {chips}
        </ul>
      </div>
      <div class="foot">
        <p class="listen">Decí “hola soy …” para activarme</p>
        <a class="login" href="https://www.youtube.com/">Iniciar sesión en YouTube</a>
      </div>
    </section>
    <section class="panel-right">
      <div class="photo-card">
        {images}
        <div class="photo-veil"></div>
        <div class="photo-caption">Momentos</div>
      </div>
    </section>
  </div>
  <script>
    const frames = Array.from(document.querySelectorAll(".photo-card img"));
    let frameIndex = 0;
    if (frames.length) {{
      setInterval(() => {{
        frames[frameIndex].classList.remove("on");
        frameIndex = (frameIndex + 1) % frames.length;
        frames[frameIndex].classList.add("on");
      }}, 8000);
    }}

    const chips = Array.from(document.querySelectorAll(".chip"));
    const hero = document.getElementById("hero-phrase");
    let chipIndex = 0;
    function setActiveChip(i) {{
      if (!chips.length || !hero) return;
      chips.forEach((c) => c.classList.remove("active"));
      const chip = chips[i];
      chip.classList.add("active");
      const phrase = chip.querySelector(".chip-phrase");
      if (!phrase) return;
      hero.classList.add("swap");
      setTimeout(() => {{
        hero.textContent = phrase.textContent || "";
        hero.classList.remove("swap");
      }}, 220);
    }}
    if (chips.length > 1) {{
      setInterval(() => {{
        chipIndex = (chipIndex + 1) % chips.length;
        setActiveChip(chipIndex);
      }}, 7000);
    }}

    const timeEl = document.getElementById("clock-time");
    const dateEl = document.getElementById("clock-date");
    const dateFmt = new Intl.DateTimeFormat("es-AR", {{
      weekday: "long", day: "numeric", month: "long"
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
    suggestions = _load_action_suggestions()
    _IDLE_HTML_PATH.write_text(idle_html(suggestions), encoding="utf-8")
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
