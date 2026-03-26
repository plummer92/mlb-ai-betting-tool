from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import APP_NAME, APP_VERSION
from app.db import Base, engine
from app.routes.games import router as games_router
from app.routes.model import router as model_router
from app.routes.edges import router as edges_router
from app.routes.daily import router as daily_router
from app.routes.backtest import router as backtest_router
from app.scheduler import scheduler

Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    lifespan=lifespan,
)

app.include_router(games_router)
app.include_router(model_router)
app.include_router(edges_router)
app.include_router(daily_router)
app.include_router(backtest_router)


@app.get("/")
def root():
    return {
        "app": APP_NAME,
        "version": APP_VERSION,
        "status": "running",
    }
