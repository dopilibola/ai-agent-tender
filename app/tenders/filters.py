"""Stage 1: free, fast keyword filter (Uzbek Latin + Cyrillic + Russian).

Rules (see config.yaml → tenders.keywords / tenders.exclude):
- every term matches from the START of a word: "sayt" finds "saytini", not "Muzrabot";
- a term ending in "$" must be a whole word: "bot$" finds "bot", not "botanika";
- IT lot = at least 1 strong term, or at least 2 different weak terms;
- "a|b|c" in one entry = one concept written several ways (Latin / Cyrillic / Russian):
  "tizim|тизим|систем" counts as ONE weak term, so a single word can't pass alone;
- an exclusion term (hardware, furniture, licence resale) rejects the lot unless an
  "override" term (development / creation / implementation) is also present.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.config import TendersConfig

_APOSTROPHES = re.compile(r"[ʻʼ‘’`´ʹ]")
_SEPARATORS = re.compile(r"[\-–—/_«»\"“”(),.;:!?]+")


def normalize(text: str) -> str:
    text = _APOSTROPHES.sub("'", text.lower()).replace("ё", "е")
    text = _SEPARATORS.sub(" ", text)
    return " ".join(text.split())


def compile_term(term: str) -> re.Pattern[str]:
    exact = term.endswith("$")
    tokens = normalize(term.rstrip("$")).split()
    if not tokens:
        raise ValueError(f"empty keyword: {term!r}")
    parts = [re.escape(tok) + r"[\w']*" for tok in tokens]
    if exact:
        parts[-1] = re.escape(tokens[-1]) + r"(?![\w'])"
    return re.compile(r"(?<![\w'])" + r"\s+".join(parts))


@dataclass
class MatchResult:
    is_it: bool
    strong: list[str] = field(default_factory=list)
    weak: list[str] = field(default_factory=list)
    excluded_by: list[str] = field(default_factory=list)
    override: list[str] = field(default_factory=list)

    @property
    def keywords(self) -> list[str]:
        return self.strong + self.weak

    @property
    def reason(self) -> str:
        if self.excluded_by and not self.override:
            return f"istisno: {', '.join(self.excluded_by)}"
        if self.is_it:
            return f"kalit so'zlar: {', '.join(self.keywords)}"
        return "kalit so'z yo'q" if not self.weak else f"faqat 1 ta zaif so'z: {self.weak[0]}"


def compile_group(entry: str) -> re.Pattern[str]:
    """'tizim|тизим|систем' -> one pattern matching any of the spellings."""
    alts = [a.strip() for a in entry.split("|") if a.strip()]
    if not alts:
        raise ValueError(f"empty keyword: {entry!r}")
    return re.compile("|".join(f"(?:{compile_term(a).pattern})" for a in alts))


class KeywordFilter:
    def __init__(self, cfg: TendersConfig) -> None:
        def build(terms: list[str]) -> list[tuple[str, re.Pattern[str]]]:
            return [(t.split("|")[0].strip().rstrip("$"), compile_group(t)) for t in terms]

        self.strong = build(cfg.keywords.strong)
        self.weak = build(cfg.keywords.weak)
        self.exclude = build(cfg.exclude.terms)
        self.override = build(cfg.exclude.override)

    @staticmethod
    def _hits(patterns, text: str) -> list[str]:
        return [label for label, rx in patterns if rx.search(text)]

    def match(self, *texts: str | None) -> MatchResult:
        text = normalize(" ".join(t for t in texts if t))
        strong = self._hits(self.strong, text)
        weak = self._hits(self.weak, text)
        excluded = self._hits(self.exclude, text)
        override = self._hits(self.override, text)
        is_it = bool(strong) or len(weak) >= 2
        if excluded and not override:
            is_it = False
        return MatchResult(is_it, strong, weak, excluded, override)


def is_it_lot(lot, kf: KeywordFilter | None = None) -> bool:
    """Should this stored lot be shown as an IT lot (site, /list, daily report)?

    Gemini reads the whole TZ and sets `is_it_lot`; when it says "not IT" the lot is hidden,
    unless the TITLE itself has a strong IT term (guards against an AI false negative).
    Lots without an analysis yet passed the keyword filter, so they are shown.
    """
    if (lot.analysis or {}).get("is_it_lot") is not False:
        return True
    return bool(kf and kf.match(lot.title).strong)
