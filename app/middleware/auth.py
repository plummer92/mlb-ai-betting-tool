import os
from fastapi import Header, HTTPException


def verify_api_key(x_api_key: str = Header(None)):
    expected = os.getenv("API_SECRET_KEY")
    if not expected or x_api_key != expected:
        raise HTTPException(status_code=403, detail="Forbidden")
