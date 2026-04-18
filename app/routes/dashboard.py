from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["dashboard"])

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"


@lru_cache(maxsize=3)
def _load_template(name: str) -> str:
    return (TEMPLATE_DIR / name).read_text(encoding="utf-8")


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return HTMLResponse(content=_load_template("dashboard.html"))


@router.get("/system", response_class=HTMLResponse)
def system_dashboard():
    return HTMLResponse(content=_load_template("system.html"))


@router.get("/simulator", response_class=HTMLResponse)
def simulator():
    return HTMLResponse(content=_load_template("simulator.html"))
