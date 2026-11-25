# Bottle AI — Backend

FastAPI + MySQL + providers intercambiables (LLM / STT / TTS).

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
