from __future__ import annotations

from pathlib import Path

from app.config import TendersConfig
from app.tenders.sources.base import TenderSource


def build_sources(cfg: TendersConfig, *, phone_cfg=None, data_dir: Path | None = None) -> dict[str, TenderSource]:
    """Every source enabled in config.yaml (a source not listed there is on).

    The phone-read sources (xarid auksion, Hayot birja) need the phone settings and a cache folder;
    without them (tests, a machine with no phone) they are left out.
    """
    from app.tenders.sources.etender import EtenderSource
    from app.tenders.sources.xarid import XaridSource
    from app.tenders.sources.xarid_tanlov import XaridTanlovSource

    available = {
        "etender": lambda: EtenderSource(delay_s=cfg.request_delay_s, page_size=cfg.page_size),
        "xarid": lambda: XaridSource(delay_s=cfg.request_delay_s),
        "xarid_tanlov": lambda: XaridTanlovSource(delay_s=cfg.request_delay_s),
    }
    if phone_cfg is not None and data_dir is not None:
        from app.phone.device import Phone
        from app.tenders.sources.hayotbirja import HayotbirjaSource
        from app.tenders.sources.phone_browser import PhoneBrowser
        from app.tenders.sources.xarid_auction import XaridAuctionSource

        browser = PhoneBrowser(Phone(phone_cfg.adb_path, phone_cfg.cdp_port, phone_cfg.mdns_cmd))
        cache = data_dir / "phone_cards"
        available["xarid_auction"] = lambda: XaridAuctionSource(browser, cache)
        available["hayotbirja"] = lambda: HayotbirjaSource(browser, cache)
    return {
        name: factory()
        for name, factory in available.items()
        if cfg.sources.get(name) is None or cfg.sources[name].enabled
    }
