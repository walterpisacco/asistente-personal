"""Keyword spotting local (sherpa-onnx) para la wake word.

El modelo es un Zipformer transducer en español (~3 M, int8). Corre en CPU
y solo se alimenta cuando el VAD ya detectó voz. El STT en la nube no
participa en la activación.
"""

from __future__ import annotations

import io
import logging
import wave
from pathlib import Path

import numpy as np

from app.bot.wake import _normalize

logger = logging.getLogger("tori.kws")

_MODEL_FILES: dict[str, int] = {
    "tokens.txt": 1_000,
    "bpe.model": 10_000,
    "encoder-epoch-20-avg-2-chunk-16-left-64.int8.onnx": 1_000_000,
    "decoder-epoch-20-avg-2-chunk-16-left-64.int8.onnx": 50_000,
    "joiner-epoch-20-avg-2-chunk-16-left-64.int8.onnx": 50_000,
}


def _model_file(name: str) -> str:
    if name == "encoder":
        return "encoder-epoch-20-avg-2-chunk-16-left-64.int8.onnx"
    if name == "decoder":
        return "decoder-epoch-20-avg-2-chunk-16-left-64.int8.onnx"
    if name == "joiner":
        return "joiner-epoch-20-avg-2-chunk-16-left-64.int8.onnx"
    raise KeyError(name)


def ensure_kws_model(model_dir: Path, repo: str) -> None:
    """Descarga encoder/decoder/joiner/tokens/bpe si faltan."""
    model_dir.mkdir(parents=True, exist_ok=True)
    missing = [
        name
        for name, min_bytes in _MODEL_FILES.items()
        if not (model_dir / name).is_file() or (model_dir / name).stat().st_size < min_bytes
    ]
    if not missing:
        return

    import httpx

    base = f"https://huggingface.co/{repo.strip('/')}/resolve/main"
    logger.info("Descargando keyword spotter (%s) → %s", repo, model_dir)
    with httpx.Client(follow_redirects=True, timeout=180.0) as client:
        for name in missing:
            url = f"{base}/{name}"
            dest = model_dir / name
            tmp = model_dir / f"{name}.part"
            logger.info("Descargando %s", name)
            try:
                with client.stream("GET", url) as response:
                    response.raise_for_status()
                    with tmp.open("wb") as fh:
                        for chunk in response.iter_bytes(64 * 1024):
                            fh.write(chunk)
                if tmp.stat().st_size < _MODEL_FILES[name]:
                    raise RuntimeError(f"{name} quedó incompleto ({tmp.stat().st_size} bytes)")
                tmp.replace(dest)
            except Exception:
                tmp.unlink(missing_ok=True)
                raise
    logger.info("Modelo de wake word listo")


def _token_symbols(tokens_path: Path) -> set[str]:
    symbols: set[str] = set()
    for line in tokens_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        sym, sep, idx = line.rpartition(" ")
        if sep and idx.strip().isdigit():
            symbols.add(sym)
    return symbols


def activation_phrases(username: str) -> list[str]:
    """Frases de wake para un usuario: «hola soy walter» y «soy walter»."""
    name = _normalize(username)
    if not name:
        raise ValueError(f"username inválido: {username!r}")
    return [f"hola soy {name}", f"soy {name}"]


def activation_phrase(username: str) -> str:
    """Frase canónica (enrolamiento / tips): «hola soy {name}»."""
    return activation_phrases(username)[0]


# Frases locales (KWS) → comando. Sin STT; útiles mientras hay video.
VIDEO_COMMAND_PHRASES: dict[str, str] = {
    "detener video": "detener_video",
    "para el video": "detener_video",
    "parar video": "detener_video",
}


def encode_keyword_line(
    phrase: str,
    *,
    bpe_path: Path,
    tokens_path: Path,
    score: float,
    threshold: float,
) -> str:
    """Convierte «hola tori» a la línea BPE que espera sherpa-onnx."""
    import sentencepiece as spm

    normalized = _normalize(phrase)
    if not normalized:
        raise ValueError("Frase de activación vacía")

    sp = spm.SentencePieceProcessor()
    sp.load(str(bpe_path))
    pieces = sp.encode(normalized, out_type=str)
    symbols = _token_symbols(tokens_path)
    missing = [piece for piece in pieces if piece not in symbols]
    if missing:
        raise ValueError(
            f"La wake word {normalized!r} usa tokens fuera del modelo: {missing}"
        )
    tag = normalized.replace(" ", "_")
    return f"{' '.join(pieces)} :{score:g} #{threshold:g} @{tag}"


def wav_bytes_to_pcm16(data: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(data), "rb") as wf:
        if wf.getsampwidth() != 2:
            raise ValueError("El WAV de wake tiene que ser PCM16")
        channels = wf.getnchannels()
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).copy()
    if channels > 1:
        pcm = pcm.reshape(-1, channels)[:, 0]
    return pcm


class WakeWordSpotter:
    """Spotter streaming. `accept` devuelve True al oír la wake word."""

    def __init__(
        self,
        *,
        model_dir: Path,
        usernames: list[str],
        sample_rate: int = 16000,
        score: float = 1.0,
        threshold: float = 0.25,
    ) -> None:
        if sample_rate != 16000:
            raise RuntimeError("El keyword spotter local requiere sample rate 16000")
        if not usernames:
            raise ValueError("No hay usuarios activos para la frase de activación")

        import sherpa_onnx

        self.sample_rate = sample_rate
        self.last_keyword = ""
        self._username_by_phrase: dict[str, str] = {}
        self._command_by_phrase: dict[str, str] = {}
        lines: list[str] = []
        for username in usernames:
            for phrase in activation_phrases(username):
                if phrase in self._username_by_phrase:
                    other = self._username_by_phrase[phrase]
                    raise ValueError(
                        f"La frase {phrase!r} corresponde a {other!r} y a {username!r}"
                    )
                self._username_by_phrase[phrase] = username
                lines.append(
                    encode_keyword_line(
                        phrase,
                        bpe_path=model_dir / "bpe.model",
                        tokens_path=model_dir / "tokens.txt",
                        score=score,
                        threshold=threshold,
                    )
                )
        for phrase, command in VIDEO_COMMAND_PHRASES.items():
            if phrase in self._username_by_phrase or phrase in self._command_by_phrase:
                raise ValueError(f"Frase de comando duplicada: {phrase!r}")
            self._command_by_phrase[phrase] = command
            lines.append(
                encode_keyword_line(
                    phrase,
                    bpe_path=model_dir / "bpe.model",
                    tokens_path=model_dir / "tokens.txt",
                    score=score,
                    threshold=threshold,
                )
            )
        keywords_path = model_dir / "keywords.wake.txt"
        keywords_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        logger.info(
            "Frases de activación: %s | comandos: %s",
            ", ".join(self._username_by_phrase),
            ", ".join(self._command_by_phrase),
        )

        self._kws = sherpa_onnx.KeywordSpotter(
            tokens=str(model_dir / "tokens.txt"),
            encoder=str(model_dir / _model_file("encoder")),
            decoder=str(model_dir / _model_file("decoder")),
            joiner=str(model_dir / _model_file("joiner")),
            keywords_file=str(keywords_path),
            keywords_score=float(score),
            keywords_threshold=float(threshold),
            num_trailing_blanks=1,
            num_threads=2,
            sample_rate=sample_rate,
            provider="cpu",
        )
        self._stream = self._kws.create_stream()

    def _new_stream(self) -> None:
        # reset_stream deja estado y la frase siguiente puede no disparar.
        self._stream = self._kws.create_stream()

    def reset(self) -> None:
        self._new_stream()
        self.last_keyword = ""

    def accept(self, pcm: np.ndarray) -> bool:
        """Alimenta un bloque PCM16 mono. True si detectó la wake word."""
        if pcm.size == 0:
            return False
        samples = np.ascontiguousarray(pcm.astype(np.float32) / 32768.0)
        self._stream.accept_waveform(self.sample_rate, samples)
        while self._kws.is_ready(self._stream):
            self._kws.decode_stream(self._stream)
        result = (self._kws.get_result(self._stream) or "").strip()
        if not result:
            return False
        self.last_keyword = result.replace("_", " ")
        logger.info(
            "KWS hit raw=%r → %r usuario=%s cmd=%s",
            result,
            self.last_keyword,
            self.matched_username() or "-",
            self.matched_command() or "-",
        )
        self._new_stream()
        return True

    def matched_username(self) -> str:
        """Username de la última frase detectada. Vacío si no hubo match."""
        return self._username_by_phrase.get(self.last_keyword, "")

    def matched_command(self) -> str:
        """Comando de la última frase (p.ej. detener_video). Vacío si no aplica."""
        return self._command_by_phrase.get(self.last_keyword, "")
    def matches_wav(self, wav_bytes: bytes) -> bool:
        """Pasa un WAV ya grabado por el spotter (enrolamiento)."""
        pcm = wav_bytes_to_pcm16(wav_bytes)
        self.reset()
        block = max(1, int(0.03 * self.sample_rate))
        tail = np.zeros(int(0.6 * self.sample_rate), dtype=np.int16)
        audio = np.concatenate([pcm, tail])
        hit = False
        for start in range(0, len(audio), block):
            if self.accept(audio[start : start + block]):
                hit = True
                break
        self.reset()
        return hit


def create_wake_spotter(settings, usernames: list[str]) -> WakeWordSpotter:
    if int(settings.bot_sample_rate) != 16000:
        raise RuntimeError("El keyword spotter local requiere BOT_SAMPLE_RATE=16000")
    model_dir = settings._resolve_path(settings.bot_kws_model_dir)
    ensure_kws_model(model_dir, settings.bot_kws_repo)
    spotter = WakeWordSpotter(
        model_dir=model_dir,
        usernames=usernames,
        sample_rate=settings.bot_sample_rate,
        score=settings.bot_kws_score,
        threshold=settings.bot_kws_threshold,
    )
    logger.info(
        "Wake local listo (%s usuarios, threshold=%s). STT solo después de activar.",
        len(usernames),
        settings.bot_kws_threshold,
    )
    return spotter
