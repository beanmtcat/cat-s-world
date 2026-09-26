from fastapi import APIRouter

from app.api.v1 import auth, public, studio

api_router = APIRouter()
api_router.include_router(public.router)
api_router.include_router(auth.router)
api_router.include_router(studio.router)
