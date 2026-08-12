from fastapi import FastAPI

from productivity_bot.entrypoints.http.routers.health import router as health_router


def create_app() -> FastAPI:
    application = FastAPI(title="Productivity Bot")

    application.include_router(health_router)

    return application
