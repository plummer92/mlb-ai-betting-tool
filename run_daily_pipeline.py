import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

from app.config import BASE_DIR

BASE_URL = "http://127.0.0.1:8000/api"
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (30, 120)
CONNECT_TIMEOUT_SECONDS = 30
READ_TIMEOUT_SECONDS = 300
STATUS_POLL_SECONDS = 30
MAX_STATUS_CHECKS = 60
RETRYABLE_STATUS_CODES = {500, 502, 503, 504}


def _wait_for_completion(resp, *, headers, params, timeout, sleep, max_status_checks):
    for check in range(max_status_checks):
        if resp.status_code != 202:
            return resp
        if resp.json().get("status") != "running":
            raise RuntimeError("Unexpected daily-run pending response")
        print(
            f"Daily pipeline already running; status check {check + 1}/{max_status_checks} "
            f"in {STATUS_POLL_SECONDS} s.", flush=True,
        )
        sleep(STATUS_POLL_SECONDS)
        resp = requests.get(
            f"{BASE_URL}/daily-run/status", headers=headers, params=params, timeout=timeout,
        )
        resp.raise_for_status()
    if resp.status_code == 202:
        raise requests.exceptions.ReadTimeout("Daily pipeline is still running after status checks")
    return resp


def run_pipeline(*, sleep=time.sleep, max_attempts=MAX_ATTEMPTS,
                 backoff_seconds=BACKOFF_SECONDS, read_timeout=READ_TIMEOUT_SECONDS,
                 max_status_checks=MAX_STATUS_CHECKS):
    if max_attempts < 1 or len(backoff_seconds) < max_attempts - 1:
        raise ValueError("Provide at least one attempt and a backoff for each retry")
    load_dotenv(BASE_DIR / ".env")
    print("Starting daily MLB pipeline...")
    headers = {"X-API-Key": os.getenv("API_SECRET_KEY", "")}
    # Pin the server's date across retries/status checks, including midnight.
    params = {"target_date": str(datetime.now(ZoneInfo("America/New_York")).date())}
    timeout = (CONNECT_TIMEOUT_SECONDS, read_timeout)
    for attempt in range(1, max_attempts + 1):
        print(f"Daily pipeline attempt {attempt}/{max_attempts}", flush=True)
        try:
            resp = requests.post(
                f"{BASE_URL}/daily-run", headers=headers, params=params, timeout=timeout,
            )
            resp.raise_for_status()
            resp = _wait_for_completion(
                resp, headers=headers, params=params, timeout=timeout, sleep=sleep,
                max_status_checks=max_status_checks,
            )
            result = resp.json()
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                requests.exceptions.HTTPError) as exc:
            status = exc.response.status_code if exc.response is not None else None
            retryable = not isinstance(exc, requests.exceptions.HTTPError) or status in RETRYABLE_STATUS_CODES
            error_type = type(exc).__name__ + (f" HTTP {status}" if status is not None else "")
            if not retryable or attempt == max_attempts:
                print(f"Attempt {attempt}/{max_attempts} failed: {error_type}; no retry.", flush=True)
                raise
            wait = backoff_seconds[attempt - 1]
            print(f"Attempt {attempt}/{max_attempts} failed: {error_type}; waiting {wait} s.", flush=True)
            sleep(wait)
            continue
        print(f"Date: {result['date']}")
        for step, data in result.get("steps", {}).items():
            print(f"  {step}: {data}")
        return result


if __name__ == "__main__":
    run_pipeline()
