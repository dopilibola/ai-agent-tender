from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from pydantic import SecretStr

# Make sure tests never pick up real secrets from .env.
os.environ.setdefault("GOOGLE_API_KEY", "test-key")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123:test")
os.environ.setdefault("TELEGRAM_OWNER_CHAT_ID", "42")

from app.config import BASE_DIR, Secrets, load_settings  # noqa: E402
from app.db.models import Base  # noqa: E402
from app.db.session import create_engine, create_sessionmaker  # noqa: E402

OWNER_ID = 42


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def settings(tmp_path):
    secrets = Secrets(
        _env_file=None,
        google_api_key=SecretStr("test-key"),
        telegram_bot_token=SecretStr("123:test"),
        telegram_owner_chat_id=OWNER_ID,
        config_path=BASE_DIR / "config.yaml",
        profile_path=BASE_DIR / "profile.yaml",
        company_path=BASE_DIR / "company.yaml",
        data_dir=tmp_path / "data",
    )
    return load_settings(secrets)


def load_fixture(*parts: str):
    return json.loads((FIXTURES.joinpath(*parts)).read_text(encoding="utf-8"))


@pytest.fixture
async def sessionmaker():
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield create_sessionmaker(engine)
    await engine.dispose()
