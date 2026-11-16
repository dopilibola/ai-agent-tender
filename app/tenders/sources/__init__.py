from __future__ import annotations

from app.config import TendersConfig
from app.tenders.sources.base import TenderSource


def build_sources(cfg: TendersConfig) -> dict[str, TenderSource]:
    from app.tenders.sources.etender import EtenderSource
    from app.tenders.sources.xarid import XaridSource

    available = {
        "etender": lambda: EtenderSource(delay_s=cfg.request_delay_s, page_size=cfg.page_size),
        "xarid": lambda: XaridSource(delay_s=cfg.request_delay_s),
    }
    return {
        name: factory()
        for name, factory in available.items()
        if cfg.sources.get(name) is None or cfg.sources[name].enabled
    }
