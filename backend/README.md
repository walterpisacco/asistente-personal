# Bottle AI — Backend

FastAPI + MySQL + providers intercambiables (LLM / STT / TTS).

## Modos de conversación del bot

El bot local (`python -m app.bot`) elige el camino con `CONVERSATION_MODE` en `.env`:

| Modo | Valor | Flujo de audio |
|------|--------|----------------|
| **Realtime** | `realtime` (default actual) | Speech-to-speech en una sola sesión OpenAI Realtime |
| **Pipeline / turnos** | `pipeline` | STT → LLM → TTS por turno, con providers del `.env` |

### Realtime (`CONVERSATION_MODE=realtime`)

- **No usa** `STT_PROVIDER` ni `TTS_PROVIDER`.
- Mic → OpenAI Realtime (`OPENAI_REALTIME_MODEL`, p.ej. `gpt-realtime-2.1`).
- Voz de salida: `OPENAI_REALTIME_VOICE` (p.ej. `marin`).
- Transcripción del usuario (para logs/acciones): `OPENAI_REALTIME_TRANSCRIPTION_MODEL` (p.ej. `gpt-4o-mini-transcribe`), dentro de la misma sesión WebSocket.
- Acciones del backend vía tool `run_action` en la sesión Realtime.
- Requiere `OPENAI_API_KEY`.

### Pipeline / turnos (`CONVERSATION_MODE=pipeline`)

- **STT**: `STT_PROVIDER` (Deepgram, Google, mock, …).
- **LLM**: `LLM_PROVIDER` / modelo OpenAI u otro.
- **TTS**: `TTS_PROVIDER` (Deepgram, ElevenLabs, Google, mock, …).
- Cada turno: captura de audio → transcripción → respuesta → síntesis → reproducción.
- Las variables `STT_*` / `TTS_*` / `LLM_*` solo aplican en este modo (y en la API HTTP de conversaciones).

### Métricas de llamada (`call_sessions`)

Al colgar (finalizar, silencio o error) se inserta una fila en `call_sessions` con tiempos, turnos y costo estimado (`CALL_METRICS_*`).

El cálculo de `estimated_cost` depende del modo:

- **Pipeline**: `CALL_METRICS_PRICE_LLM_*` + `CALL_METRICS_PRICE_STT_PER_MINUTE` + `CALL_METRICS_PRICE_TTS_PER_1K_CHARS`.
- **Realtime** (`gpt-realtime-2.1`): tarifas OpenAI por modalidad (USD / 1M tokens) vía `CALL_METRICS_PRICE_RT_*`:
  - Audio in/out: $32 / $64
  - Text in/out: $4 / $24
  - Cached audio/text in: $0.40
  - Transcribe (`gpt-4o-mini-transcribe`): `CALL_METRICS_PRICE_RT_TRANSCRIBE_PER_MINUTE` (~$0.003/min)
- El desglose queda en `providers_json.cost_breakdown` para comparar sesiones.

## Endpoints

- `GET /api/v1/products/{qr_code}`
- `GET /api/v1/characters/{character_id}`
- `POST /api/v1/conversations`
- `POST /api/v1/conversations/{id}/audio`
- `GET /api/v1/conversations/{id}`
- `POST /api/v1/metrics`
- `WS /ws/v1/conversations/{id}`

### Seed de demo

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
sudo pip install -r requirements.txt
python ../scripts/seed_database.py
```
## Levantar BackEnd

```bash
source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8005 --reload
```

