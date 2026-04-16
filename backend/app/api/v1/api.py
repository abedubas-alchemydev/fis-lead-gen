from fastapi import APIRouter

from app.api.v1.endpoints import alerts, auth, broker_dealers, export, health, pipeline, settings, stats

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(broker_dealers.router, tags=["broker-dealers"])
api_router.include_router(alerts.router, tags=["alerts"])
api_router.include_router(export.router, tags=["export"])
api_router.include_router(pipeline.router, tags=["pipeline"])
api_router.include_router(settings.router, tags=["settings"])
api_router.include_router(stats.router, tags=["stats"])
