"""Query generation reads X_QUERY_* from the environment, and need_engine/telegram_crawler load a developer's .env
into it at import time (.env.example sets e.g. X_QUERY_MAX_PER_PRODUCT_TARGETED=4). Tests run against the code
defaults, like CI where there is no .env; a test that needs a value sets it with monkeypatch."""
import os

import pytest


@pytest.fixture(autouse=True)
def _code_default_query_settings(monkeypatch):
    for name in [n for n in os.environ if n.startswith("X_QUERY_")]:
        monkeypatch.delenv(name)
