# Asistente personal TORI

Bot de voz headless (sin frontend): escucha continua, se activa con **hola soy {username}**, conversación STT → LLM → TTS.

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

# Correr el bot
python scripts/run_bot.py
# o: python -m app.bot
```

## Flujo

1. Idle: VAD local y keyword spotting local. No llama al STT hasta activar. El modelo (~5 MB) se descarga solo la primera vez a `backend/models/kws-es/`.
2. La frase detectada elige al usuario (`hola soy walter`). No usa el embedding de voz.
3. Inyecta CRM JSON al system prompt
4. Turnos por `BOT_SILENCE_MS`; cierra por `BOT_END_CALL_MS`
5. Acciones YouTube vía `config/bot_action_triggers.json`

## Frase de activación

No va en `.env`. Al arrancar, el bot toma cada usuario activo y arma `hola soy {username}` (minúsculas, sin acentos: «hola, soy Walter» se escucha como `hola soy walter`).

Esas frases se escriben en `backend/models/kws-es/keywords.wake.txt`. El archivo se regenera en cada inicio: no editarlo a mano. `backend/models/` no va al repositorio.

Un usuario nuevo entra en la lista al reiniciar el bot. Si el nombre usa un token que el modelo no tiene, el arranque falla. Dos usernames que normalizan igual también frenan el arranque.

Sensibilidad (en `.env`):

- `BOT_KWS_THRESHOLD=0.25` — más bajo, más fácil de activar
- `BOT_KWS_SCORE=1.0` — más alto, más sesgo hacia la frase

## Estructura

- `app/core` — config y DB
- `app/providers` — STT / TTS / LLM (Deepgram, Google, ElevenLabs, OpenAI, Ollama, mock)
- `app/services` — conversación, voice ID, YouTube, actions
- `app/bot` — mic, VAD, wake word, playback
