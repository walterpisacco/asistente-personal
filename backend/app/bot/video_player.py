"""Ventana única de TORI: fotos en espera y el video de YouTube en el mismo lugar.

Arranca con el bot. `ver_video` cambia al reproductor; `detener_video` vuelve
a las fotos. El proceso es aparte para no bloquear el loop de audio.
"""

from __future__ import annotations

import argparse
import atexit
import json
import logging
import os
import random
import re
import subprocess
import sys
import threading
from pathlib import Path

logger = logging.getLogger("tori.video")

_BACKEND = Path(__file__).resolve().parents[2]
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,32}$")
_player_proc: subprocess.Popen | None = None
_showing_video = False
_lock = threading.Lock()

_CHROME_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
# YouTube exige un Referer https que no sea youtube.com. Con base youtube.com
# el embed responde error 153 o 152-4 y el video no arranca.
_EMBED_ORIGIN = "https://www.qt.io/"


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
  <div class="caption">
    <h1>TORI</h1>
    <p>Estoy escuchando</p>
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
  </script>
</body>
</html>
"""


def embed_html(video_id: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="referrer" content="strict-origin-when-cross-origin">
  <style>
    html, body {{
      margin: 0; height: 100%; background: #000; overflow: hidden;
    }}
    #player {{
      position: absolute; inset: 0; width: 100%; height: 100%;
    }}
  </style>
</head>
<body>
  <div id="player"></div>
  <script>
    function onYouTubeIframeAPIReady() {{
      var player = new YT.Player("player", {{
        width: window.innerWidth,
        height: window.innerHeight,
        videoId: "{video_id}",
        playerVars: {{
          autoplay: 1,
          rel: 0,
          modestbranding: 1,
          origin: "https://www.qt.io"
        }},
        events: {{
          onReady: function (event) {{ event.target.playVideo(); }},
          onError: function (event) {{
            var code = event.data;
            if (code === 101 || code === 150 || code === 152 || code === 153) {{
              location.href = "https://www.youtube.com/watch?v={video_id}&autoplay=1";
            }}
          }}
        }}
      }});
      window.addEventListener("resize", function () {{
        player.setSize(window.innerWidth, window.innerHeight);
      }});
    }}
    var tag = document.createElement("script");
    tag.src = "https://www.youtube.com/iframe_api";
    document.head.appendChild(tag);
  </script>
</body>
</html>
"""


def _has_display() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _alive() -> bool:
    return _player_proc is not None and _player_proc.poll() is None


def _spawn() -> None:
    global _player_proc
    if not _has_display():
        raise RuntimeError("No hay pantalla gráfica para abrir la ventana")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_BACKEND) + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault(
        "QTWEBENGINE_CHROMIUM_FLAGS",
        "--autoplay-policy=no-user-gesture-required",
    )
    _player_proc = subprocess.Popen(
        [sys.executable, "-m", "app.bot.video_player", "--listen"],
        cwd=str(_BACKEND),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    logger.info("Ventana TORI abierta")


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


def _configure_view(view) -> None:
    from PyQt6.QtWebEngineCore import QWebEngineSettings

    settings = view.settings()
    settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
    settings.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
    settings.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)
    settings.setAttribute(
        QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
    )
    view.page().profile().setHttpUserAgent(_CHROME_UA)


def _run_window() -> None:
    os.environ.setdefault(
        "QTWEBENGINE_CHROMIUM_FLAGS",
        "--autoplay-policy=no-user-gesture-required",
    )
    from PyQt6.QtCore import QObject, QUrl, pyqtSignal
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWidgets import QApplication, QMainWindow

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
        view.setHtml(embed_html(video_id), QUrl(_EMBED_ORIGIN))

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
    window.show()
    raise SystemExit(app.exec())


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
    view.setHtml(embed_html(args.id), QUrl(_EMBED_ORIGIN))
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
