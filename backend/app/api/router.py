from fastapi import APIRouter

from app.api import actions, admin, auth, characters, conversations, metrics, proyects

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(admin.router)
api_router.include_router(characters.router)
api_router.include_router(conversations.router)
api_router.include_router(metrics.router)
api_router.include_router(proyects.router)
api_router.include_router(actions.router)
