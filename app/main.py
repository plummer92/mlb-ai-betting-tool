from contextlib import asynccontextmanager
from fastapi import FastAPI

from app.config import APP_NAME, APP_VERSION

from app.routes.games import router as games_router
from app.routes.model import router as model_router
from app.routes.edges import router as edges_router
from app.routes.daily import router as daily_router
from app.routes.backtest import router as backtest_router
from app.routes.ranked import router as ranked_router
from app.routes.debug import router as debug_router
from app.routes.alerts import router as alerts_router
from app.routes.reviews import router as reviews_router
from app.routes.status import router as status_router
from app.routes.dashboard import router as dashboard_router

from app.scheduler import scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start()
    # ── v0.4 seed data (non-fatal) ────────────────────────────────────────
    try:
        from app.db import SessionLocal
        from app.services.bullpen_calc import seed_manager_tendencies
        from app.services.umpire_service import seed_known_umpires
        _db = SessionLocal()
        try:
            seed_manager_tendencies(_db)
            seed_known_umpires(_db)
        finally:
            _db.close()
    except Exception as e:
        print(f"[v4 startup] seed error (non-fatal): {e}", flush=True)
    yield
    scheduler.shutdown()


app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    lifespan=lifespan,
)


app.include_router(alerts_router)
app.include_router(games_router)
app.include_router(model_router)
app.include_router(edges_router)
app.include_router(daily_router)
app.include_router(backtest_router)
app.include_router(ranked_router)
app.include_router(debug_router)
app.include_router(reviews_router)
app.include_router(status_router)
app.include_router(dashboard_router)


@app.get("/")
def root():
    return {
        "app": APP_NAME,
        "version": APP_VERSION,
        "status": "running",
    }
