# Asistente personal TORI

Bot de voz headless (sin frontend): escucha continua, wake word **HOLA TORI**, identificación por embedding de voz, conversación STT → LLM → TTS.

## Requisitos

- Python 3.11+
- MySQL
- Micrófono y salida de audio (`sudo apt install libportaudio2 portaudio19-dev`)
- API keys en `backend/.env` (Deepgram / OpenAI / etc.)
- Base de datos dedicada `asistente_personal` (no reutilizar la de etiquetas-ia)

## Setup

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Crear DB y migrar
alembic upgrade head
python scripts/seed_database.py

# Enrolar voz de un usuario existente (no crea users)
python scripts/enroll_voice.py --list
python scripts/enroll_voice.py --id 1
# o: python scripts/enroll_voice.py --id 1 --wav /ruta/sample.wav

# Correr el bot
python scripts/run_bot.py
# o: python -m app.bot
```

## Flujo

1. Idle: VAD local y keyword spotting local («HOLA TORI»). No llama al STT hasta activar. El modelo (~5 MB) se descarga solo la primera vez a `backend/models/kws-es/`.
2. Identifica al hablante solo buscando en `user_embeddings` (si no matchea y no hay guest en DB, rechaza y vuelve a idle)
3. Inyecta CRM JSON al system prompt
4. Turnos por `BOT_SILENCE_MS`; cierra por `BOT_END_CALL_MS`
5. Acciones YouTube vía `config/bot_action_triggers.json`

## Frase de activación

Se configura en `backend/.env`:

```bash
BOT_WAKE_WORD=hola tori
```

Al arrancar, el bot la normaliza (minúsculas, sin acentos) y la escribe en `backend/models/kws-es/keywords.wake.txt`. Ese archivo se regenera en cada inicio: no editarlo a mano. `backend/models/` no va al repositorio.

Una sola frase, en español, cubierta por el vocabulario del modelo. Si un token no existe, el arranque falla. Hay que reiniciar el bot para que tome el cambio.

Sensibilidad (también en `.env`):

- `BOT_KWS_THRESHOLD=0.25` — más bajo, más fácil de activar
- `BOT_KWS_SCORE=1.0` — más alto, más sesgo hacia la frase

## Estructura

- `app/core` — config y DB
- `app/providers` — STT / TTS / LLM (Deepgram, Google, ElevenLabs, OpenAI, Ollama, mock)
- `app/services` — conversación, voice ID, YouTube, actions
- `app/bot` — mic, VAD, wake word, playback
