from __future__ import annotations

import importlib.util
import logging

from app.config import Settings
from app.connectors.base import Connector

log = logging.getLogger(__name__)


def browser_available() -> bool:
    return importlib.util.find_spec("playwright") is not None


def email_configured(settings: Settings) -> bool:
    sec = settings.secrets
    return bool(settings.config.email_watch.enabled and sec.imap_user and sec.imap_app_password)


def build_connectors(settings: Settings, *, gemini=None, sessionmaker=None, notify=None) -> dict[str, Connector]:
    from app.connectors.fl_ru import FlRuConnector
    from app.connectors.freelancer_com import FreelancerComConnector
    from app.connectors.kwork import KworkConnector

    platforms = settings.config.platforms

    def on(name: str) -> bool:
        return bool(platforms.get(name) and platforms[name].enabled)

    out: dict[str, Connector] = {}
    if on("freelancer_com"):
        token = settings.secrets.freelancer_com_token
        out["freelancer_com"] = FreelancerComConnector(
            token=token.get_secret_value() if token else None,
            lookback_hours=settings.config.filters.max_job_age_hours,
        )
    if on("fl_ru"):
        out["fl_ru"] = FlRuConnector()
    if on("kwork"):
        out["kwork"] = KworkConnector()
    if on("truelancer") and not browser_available():
        log.warning("truelancer.disabled_no_browser")  # e.g. Android/Termux: no Chromium
    elif on("truelancer"):
        from app.connectors.truelancer import TruelancerConnector

        tl = platforms["truelancer"]
        out["truelancer"] = TruelancerConnector(
            settings.secrets.data_dir / "browser_profile" / "truelancer",
            min_delay_s=tl.min_action_delay_s, max_delay_s=tl.max_action_delay_s, daily_cap=tl.daily_action_cap,
        )
    if email_configured(settings) and gemini is not None and sessionmaker is not None:
        from app.connectors.email_watch import EmailWatchConnector, ImapFetcher

        ew = settings.config.email_watch
        fetcher = ImapFetcher(settings.secrets.imap_host, settings.secrets.imap_user,
                              settings.secrets.imap_app_password.get_secret_value(), ew.senders, ew.lookback_days)
        out["email"] = EmailWatchConnector(fetcher, gemini, sessionmaker, notify,
                                           max_per_poll=ew.max_emails_per_poll)
    return out
