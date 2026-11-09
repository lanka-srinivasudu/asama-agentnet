import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_agentnet.db")
os.environ.setdefault("REDIS_URL", "memory")
os.environ.setdefault("AGENT_WORKER", "off")
os.environ.setdefault("POSTS_BURST", "1000")
os.environ.setdefault("READS_BURST", "1000")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402
from app.taxonomy import make_handle  # noqa: E402

H = {k: make_handle(v) for k, v in {
    "swiggy": "Swiggy Foods", "phonepe": "PhonePe", "zepto": "Zepto", "irctc": "IRCTC",
    "bms": "BookMyShow", "hdfc": "HDFC Bank", "spam": "Dealz4U"}.items()}
ADMIN = {"X-Admin-Token": "dev-admin-token"}


@pytest.fixture(scope="module")
def keys():
    with TestClient(app):
        return seed(run_agents=True)


@pytest.fixture()
def client(keys):
    with TestClient(app) as c:
        yield c


def auth(keys, short):
    return {"Authorization": f"Bearer {keys[H[short]]}"}
