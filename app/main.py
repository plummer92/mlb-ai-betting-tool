from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import APP_NAME, APP_VERSION
from app.db import Base, SessionLocal, engine
from app.models.schema import BacktestResult
from app.routes.games import router as games_router
from app.routes.model import router as model_router
from app.routes.edges import router as edges_router
from app.routes.daily import router as daily_router
from app.routes.backtest import router as backtest_router
from app.routes.alerts import router as alerts_router
from app.routes.ranked import router as ranked_router
from app.scheduler import scheduler
from app.services.backtest_service import apply_backtest_weights

Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Restore latest backtest weights into the simulator on startup
    db = SessionLocal()
    try:
        latest = db.query(BacktestResult).order_by(BacktestResult.run_at.desc()).first()
        if latest:
            apply_backtest_weights(latest)
    finally:
        db.close()

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
app.include_router(alerts_router)
app.include_router(ranked_router)


@app.get("/")
def root():
    return {
        "app": APP_NAME,
        "version": APP_VERSION,
        "status": "running",
    }
