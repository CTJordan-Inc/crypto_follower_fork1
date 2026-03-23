from fastapi import APIRouter

from app.api.routes import data, performance, watchlist

api_router = APIRouter()
api_router.include_router(watchlist.router)
api_router.include_router(data.router)
api_router.include_router(performance.router)

