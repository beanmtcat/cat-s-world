from fastapi import APIRouter

from app.api.v1 import auth, feed, public, studio

api_router = APIRouter()
api_router.include_router(public.router)
api_router.include_router(auth.router)
api_router.include_router(studio.router)
api_router.include_router(feed.public_router)
api_router.include_router(feed.studio_router)
