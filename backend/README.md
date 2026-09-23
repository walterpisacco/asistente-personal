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

1. Idle: escucha hasta detectar «HOLA TORI»
2. Identifica al hablante solo buscando en `user_embeddings` (si no matchea y no hay guest en DB, rechaza y vuelve a idle)
3. Inyecta CRM JSON al system prompt
4. Turnos por `BOT_SILENCE_MS`; cierra por `BOT_END_CALL_MS`
5. Acciones YouTube vía `config/bot_action_triggers.json`

## Estructura

- `app/core` — config y DB
- `app/providers` — STT / TTS / LLM (Deepgram, Google, ElevenLabs, OpenAI, Ollama, mock)
- `app/services` — conversación, voice ID, YouTube, actions
- `app/bot` — mic, VAD, wake word, playback
