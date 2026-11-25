from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.core.config import get_settings
from app.core.database import Base, engine
from app.core.logging import setup_logging
from app.core.security import is_dev_environment
from app.middleware.security import ApiSecurityMiddleware
from app.models import (  # noqa: F401 — registra tablas en Base.metadata
    Action,
    Avatar,
    Character,
    Conversation,
    ConversationMessage,
    Interaction,
    Proyects,
    Recordatory,
    Tasks,
    User,
    UserEmbedding,
    UserProfile,
)
from app.services.auth_service import bootstrap_admin_user
from app.websocket.conversation import router as ws_router

settings = get_settings()
setup_logging()


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    bootstrap_admin_user(settings)
    yield


app = FastAPI(
    title="Alive AI",
    version="0.1.0",
    description="Asistente personal de voz con personajes",
    lifespan=lifespan,
    docs_url="/docs" if is_dev_environment(settings.app_env) else None,
    redoc_url="/redoc" if is_dev_environment(settings.app_env) else None,
    openapi_url="/openapi.json" if is_dev_environment(settings.app_env) else None,
)

# Orden: CORS primero (respuestas preflight), luego seguridad.
cors_origins = settings.cors_origins_list
if "*" in cors_origins:
    # credentials + * es inválido; en ese caso abrir orígenes sin credentials.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or ["http://localhost:8081"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*", "X-App-Token", "Authorization", "Content-Type"],
    )

app.add_middleware(ApiSecurityMiddleware, settings=settings)

app.mount("/static", StaticFiles(directory="static"), name="static")

# Avatar.avatar_url guarda paths tipo `assets/avatars/chef_0.jpg`.
_assets_dir = Path(__file__).resolve().parents[2] / "mobile" / "assets"
if not _assets_dir.is_dir():
    _assets_dir = Path("static") / "assets"
if _assets_dir.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="assets")

app.include_router(api_router)
app.include_router(ws_router)


@app.get("/health")
def health() -> dict:
    payload = {"status": "ok"}
    if is_dev_environment(settings.app_env):
        payload.update(
            {
                "env": settings.app_env,
                "llm": settings.llm_provider,
                "stt": settings.stt_provider,
                "tts": settings.tts_provider,
                "geo_restrict": settings.geo_restrict_enabled,
            }
        )
    return payload
