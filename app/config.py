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

THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY", "")
THE_ODDS_API_URL = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"

ALERT_MIN_EV = float(os.getenv("ALERT_MIN_EV", "0.03"))
ALERT_MIN_EDGE = float(os.getenv("ALERT_MIN_EDGE", "0.04"))
ALERT_CONFIDENCE_LEVELS = set(os.getenv("ALERT_CONFIDENCE_LEVELS", "medium,strong").split(","))
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
ALERT_DESTINATION = os.getenv("ALERT_DESTINATION", "discord")
POSTGAME_LOOKBACK_HOURS = int(os.getenv("POSTGAME_LOOKBACK_HOURS", "12"))

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

BETTING_ENABLED = os.getenv("BETTING_ENABLED", "false").strip().lower() == "true"
BETTING_MODE = os.getenv("BETTING_MODE", "paper").strip().lower()
BOOK_PROVIDER = os.getenv("BOOK_PROVIDER", "paper").strip().lower()
DEFAULT_BANKROLL = float(os.getenv("DEFAULT_BANKROLL", "1000"))
KILL_SWITCH = os.getenv("KILL_SWITCH", "false").strip().lower() == "true"
