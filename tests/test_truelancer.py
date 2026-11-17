"""Truelancer parsing / block detection (no browser in tests) + per-platform poll intervals."""

from datetime import UTC, datetime, timedelta

import pytest

from app.connectors.truelancer import check_blocked, extract_projects, parse_project
from tests.conftest import FIXTURES
from tests.test_freelance import FakeConnector, listing, make


def test_parse_real_page():
    projects = extract_projects((FIXTURES / "truelancer" / "jobs.html").read_text(encoding="utf-8"))
    assert len(projects) == 3
    job = parse_project(projects[0])
    assert job.platform == "truelancer" and job.external_id == "665475"
    assert job.url.endswith("-665475") and job.currency == "USD" and job.budget_max == 3
    assert job.proposals_count == 4 and job.budget_type == "fixed"
    assert job.posted_at == datetime(2026, 11, 16, 5, 59, 7, tzinfo=UTC)
    assert "Facebook Marketing" in job.skills


@pytest.mark.parametrize("status,url,html,blocked", [
    (200, "https://www.truelancer.com/freelance-python-jobs", '<script id="__NEXT_DATA__">{}</script>', False),
    (429, "https://www.truelancer.com/x", "", True),
    (200, "https://www.truelancer.com/login?next=/x", "", True),
    (200, "https://www.truelancer.com/x", "<title>Just a moment...</title> cf-challenge", True),
])
def test_block_detection(status, url, html, blocked):
    assert bool(check_blocked(status, url, html)) is blocked


def test_layout_change_is_loud():
    with pytest.raises(ValueError):
        extract_projects("<html></html>")


async def test_each_platform_keeps_its_own_interval(settings, sessionmaker):
    svc, _, _ = make(settings, sessionmaker, [])
    fast, slow = FakeConnector([listing(1)]), FakeConnector([])
    calls = {"fast": 0, "slow": 0}

    async def count(name, conn):
        calls[name] += 1
        return []

    fast.fetch_jobs = lambda: count("fast", fast)
    slow.fetch_jobs = lambda: count("slow", slow)
    svc.connectors = {"freelancer_com": fast, "truelancer": slow}  # 10 min vs 60 min in config.yaml
    t0 = datetime(2026, 11, 17, 6, tzinfo=UTC)
    for minutes in (0, 10, 20, 30, 40, 50, 60):
        await svc.run_cycle(t0 + timedelta(minutes=minutes))
    assert calls == {"fast": 7, "slow": 2}
    await svc.run_cycle(t0 + timedelta(minutes=61), force=True)
    assert calls == {"fast": 8, "slow": 3}


def test_truelancer_skipped_without_browser(settings, monkeypatch):
    """On Android/Termux Playwright is not installed: the connector must be skipped, not crash."""
    import app.connectors as connectors

    monkeypatch.setattr(connectors, "browser_available", lambda: False)
    assert "truelancer" not in connectors.build_connectors(settings)
    monkeypatch.setattr(connectors, "browser_available", lambda: True)
    assert "truelancer" in connectors.build_connectors(settings)
