import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

APP_NAME = "MLB AI Betting Tool"
APP_VERSION = "0.1.0"
DEBUG = os.getenv("DEBUG", "false").strip().lower() == "true"
MLB_API_BASE = "https://statsapi.mlb.com/api/v1"

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL is missing from .env")

THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY", "")
THE_ODDS_API_URL = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"
ODDS_DATA_PROVIDER = os.getenv("ODDS_DATA_PROVIDER", "the_odds_api").strip().lower()
BETSTACK_API_KEY = os.getenv("BETSTACK_API_KEY", "")
BETSTACK_API_URL = os.getenv("BETSTACK_API_URL", "https://api.betstack.dev/api/v1")
ODDS_API_MONTHLY_QUOTA = int(os.getenv("ODDS_API_MONTHLY_QUOTA", "500"))
ODDS_API_QUOTA_EXHAUSTED_COOLDOWN_MINUTES = int(
    os.getenv("ODDS_API_QUOTA_EXHAUSTED_COOLDOWN_MINUTES", "180")
)

ALERT_MIN_EV = float(os.getenv("ALERT_MIN_EV", "0.03"))
ALERT_MIN_EDGE = float(os.getenv("ALERT_MIN_EDGE", "0.04"))
ALERT_CONFIDENCE_LEVELS = set(os.getenv("ALERT_CONFIDENCE_LEVELS", "medium,strong").split(","))
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
ALERT_DESTINATION = os.getenv("ALERT_DESTINATION", "discord")
POSTGAME_LOOKBACK_HOURS = int(os.getenv("POSTGAME_LOOKBACK_HOURS", "12"))
PREGAME_BET_REMINDER_MINUTES = int(os.getenv("PREGAME_BET_REMINDER_MINUTES", "5"))

BETTING_ENABLED = os.getenv("BETTING_ENABLED", "false").strip().lower() == "true"
BETTING_MODE = os.getenv("BETTING_MODE", "paper").strip().lower()
BOOK_PROVIDER = os.getenv("BOOK_PROVIDER", "paper").strip().lower()
DEFAULT_BANKROLL = float(os.getenv("DEFAULT_BANKROLL", "1000"))
KILL_SWITCH = os.getenv("KILL_SWITCH", "false").strip().lower() == "true"
