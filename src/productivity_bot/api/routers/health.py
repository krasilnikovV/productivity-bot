from fastapi import APIRouter

from productivity_bot.api.schemas.health import HealthResponse

router = APIRouter(tags=["health"])

@router.get("/health")
async def health() -> HealthResponse:
    return HealthResponse(status="ok")