import os

import requests
from dotenv import load_dotenv

from app.config import BASE_DIR

BASE_URL = "http://127.0.0.1:8000/api"


def run_pipeline():
    load_dotenv(BASE_DIR / ".env")
    print("Starting daily MLB pipeline...")
    headers = {"X-API-Key": os.getenv("API_SECRET_KEY", "")}
    resp = requests.post(f"{BASE_URL}/daily-run", headers=headers, timeout=120)
    resp.raise_for_status()
    result = resp.json()
    print(f"Date: {result['date']}")
    for step, data in result.get("steps", {}).items():
        print(f"  {step}: {data}")


if __name__ == "__main__":
    run_pipeline()
