import uuid
from pathlib import Path

import aiofiles

from app.core.config import Settings


class AudioService:
    def __init__(self, settings: Settings) -> None:
        self.audio_dir = Path(settings.audio_dir)
        if not self.audio_dir.is_absolute():
            from app.core.config import _BACKEND_ROOT

            self.audio_dir = _BACKEND_ROOT / self.audio_dir
        self.audio_dir.mkdir(parents=True, exist_ok=True)

    async def save_audio(self, content: bytes, extension: str = "mp3") -> str:
        filename = f"response_{uuid.uuid4().hex[:12]}.{extension}"
        path = self.audio_dir / filename
        async with aiofiles.open(path, "wb") as f:
            await f.write(content)
        return str(path)
