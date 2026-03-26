import requests

BASE_URL = "http://127.0.0.1:8000/api"


def run_pipeline():
    print("Starting daily MLB pipeline...")
    resp = requests.post(f"{BASE_URL}/daily-run", timeout=120)
    resp.raise_for_status()
    result = resp.json()
    print(f"Date: {result['date']}")
    for step, data in result.get("steps", {}).items():
        print(f"  {step}: {data}")


if __name__ == "__main__":
    run_pipeline()
