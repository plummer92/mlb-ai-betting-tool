"""Keep the test suite offline and prevent loading local credentials."""

import os
from contextlib import ExitStack
from unittest.mock import patch


os.environ["PYTHON_DOTENV_DISABLED"] = "1"
_offline_patches = ExitStack()


def _deny_network(*args, **kwargs):
    raise AssertionError("Network calls are disabled in tests; mock the request.")


def pytest_sessionstart(session):
    # These patches start before collection/imports, including app.config.
    _offline_patches.enter_context(patch("dotenv.load_dotenv", return_value=False))
    for target in (
        "httpx.HTTPTransport.handle_request",
        "httpx.AsyncHTTPTransport.handle_async_request",
        "requests.sessions.Session.request",
        "urllib.request.urlopen",
        "socket.create_connection",
        "socket.getaddrinfo",
    ):
        _offline_patches.enter_context(patch(target, side_effect=_deny_network))


def pytest_sessionfinish(session, exitstatus):
    _offline_patches.close()
