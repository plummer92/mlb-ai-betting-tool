import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

APP_NAME = "MLB AI Betting Tool"
APP_VERSION = "0.1.0"
MLB_API_BASE = "https://statsapi.mlb.com/api/v1"

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL is missing from .env")
