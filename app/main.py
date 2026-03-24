from fastapi import FastAPI

from app.config import APP_NAME, APP_VERSION
from app.db import Base, engine
from app.routes.games import router as games_router
from app.routes.model import router as model_router

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
)

app.include_router(games_router)
app.include_router(model_router)


@app.get("/")
def root():
    return {
        "app": APP_NAME,
        "version": APP_VERSION,
        "status": "running",
    }
