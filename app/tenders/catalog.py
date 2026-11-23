"""«Barcha tenderlar» catalog: every open lot from the last scan, sorted into goods / services / IT.

The 30-minute tender scan already downloads the full open-lot listing of every source; IT lots go on to the
`lots` table, the rest used to be dropped. This module keeps the latest listing (one KV row per source, so it
survives restarts) and labels each row, so the site can show goods-supply tenders — "tovar olib kelib berib
pul oladigan" lots — without any extra requests to the platforms.

Kinds:
- "goods"   — tovar yetkazib berish (supply / purchase of products);
- "service" — xizmat ko'rsatish, qurilish va ta'mirlash ishlari;
- "it"      — passes the IT keyword filter (those live on the IT tenders page).
xarid.uzex.uz rows carry a product-classifier category ("Уголь", "Услуги почтовой связи…"), so the category
decides. etender rows only have a free-text name: a supply verb ("yetkazib berish", "xarid qilish", "поставка")
makes it goods, else a service/work word makes it a service; a name with neither (usually just the item,
e.g. "Ofis qog'ozi") counts as goods.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from app.tenders.filters import KeywordFilter, compile_term, normalize
from app.tenders.schemas import LotSummary

KV_PREFIX = "tender_catalog:"
KIND_LABEL = {"goods": "Tovar", "service": "Xizmat / ish", "it": "IT"}

# Word starts (see filters.compile_term): Uzbek Latin, Uzbek Cyrillic (with and without ў/қ/ҳ/ғ), Russian.
# Supply verbs: the lot is a delivery of products, whatever else the name says.
# ("xarid qilish" / "закупка" only mean "procure" — "xizmatlarni xarid qilish" is a service — so they are not here.)
SUPPLY_TERMS = [
    "yetkazib berish", "etkazib berish", "етказиб бер", "yetkazib ber", "etkazib ber",
    "sotib olish", "сотиб олиш", "sotib olin", "сотиб олин", "поставк", "приобретени", "покупк",
]
# Services and works (checked after the supply verbs).
SERVICE_TERMS = [
    "xizmat", "хизмат", "услуг", "ishlari", "ишлари", "ishlarini", "ишларини", "работ$", "работы", "работ по",
    "ta'mir", "таъмир", "тамир", "ремонт", "qurilish", "қурилиш", "курилиш", "qurish", "қуриш", "куриш",
    "строител", "строительств", "rekonstruk", "реконструк", "реконсртук", "капитальн", "мукаммал", "mukammal",
    "loyiha", "лойиҳа", "лойиха", "проект", "montaj", "монтаж", "o'rnatish", "ўрнатиш", "урнатиш",
    "топосъём", "топосём", "топосъем", "топосем", "геодез", "изыскан",
    "ijara", "ижара", "аренд", "lizing", "лизинг", "autsorsing", "аутсорсинг", "кейтринг", "keytring",
    "konsalting", "консалтинг", "консультац", "baholash", "баҳолаш", "бахолаш", "оценк", "audit", "аудит",
    "o'qitish", "ўқитиш", "укитиш", "обучени", "trening", "тренинг",
    "obodonlashtir", "ободонлаштир", "благоустр", "tashish", "ташиш", "перевоз", "транспортн",
    "qo'riqlash", "қўриқлаш", "куриклаш", "охран", "tozalash", "тозалаш", "уборк", "чистк",
    "nazorat", "назорат", "надзор", "tadqiqot", "тадқиқот", "тадкикот", "исследован", "ekspertiza", "экспертиз",
    "sug'urta", "суғурта", "сугурта", "страхован", "reklama", "реклама", "nashr", "нашр", "издатель",
    "tashkil etish", "ташкил этиш", "jalb qilish", "жалб қилиш", "жалб килиш",
    "обслуживан", "сервис", "servis", "saqlash", "сақлаш", "саклаш",
    "demontaj", "демонтаж", "демантаж", "buzish", "бузиш", "maslahat", "маслаҳат", "маслахат", "консультант",
    "tadbir", "тадбир", "o'tkazish", "ўтказиш", "утказиш", "проведени", "joriy qilish", "жорий қилиш",
    "aniqlash", "аниқлаш", "аниклаш", "форензик", "spektakl", "спектакл", "обработк",
]

# xarid classifier: services / works categories START with these ("Продукция … и услуги …" is goods).
SERVICE_CATEGORY_STARTS = ("услуг", "работ", "здания и работ")


def _build(terms: list[str]) -> list[re.Pattern[str]]:
    return [compile_term(t) for t in terms]


_SUPPLY = _build(SUPPLY_TERMS)
_SERVICE = _build(SERVICE_TERMS)


def _any(patterns: list[re.Pattern[str]], text: str) -> bool:
    return any(p.search(text) for p in patterns)


def classify(row: LotSummary, kf: KeywordFilter | None = None) -> str:
    text = normalize(row.title)
    if kf is not None and kf.match(row.title).is_it:
        return "it"
    if row.title_is_category:  # xarid: classifier category
        return "service" if text.startswith(SERVICE_CATEGORY_STARTS) else "goods"
    if _any(_SUPPLY, text):
        return "goods"
    if _any(_SERVICE, text):
        return "service"
    return "goods"  # just the item name ("Ofis qog'ozi", "Темир йўл тарози")


@dataclass
class CatalogRow:
    """One open lot as the site shows it (plain data, safe to keep in KV JSON)."""

    source: str
    external_id: str
    url: str
    title: str
    kind: str
    deadline_at: datetime | None
    start_cost: float | None
    currency: str
    customer_name: str | None
    customer_region: str | None
    lot_type: str | None
    is_category: bool
    usd: float | None = None  # filled when shown (today's rate); not stored

    # tables.Col / table_html expect an `id`; catalog rows are not DB rows.
    @property
    def id(self) -> str:
        return f"{self.source}-{self.external_id}"

    def to_json(self) -> dict:
        return {"s": self.source, "i": self.external_id, "u": self.url, "t": self.title, "k": self.kind,
                "d": self.deadline_at.isoformat() if self.deadline_at else None, "c": self.start_cost,
                "cur": self.currency, "cn": self.customer_name, "r": self.customer_region, "lt": self.lot_type,
                "cat": self.is_category}

    @classmethod
    def from_json(cls, d: dict) -> CatalogRow:
        return cls(d["s"], d["i"], d["u"], d["t"], d["k"], datetime.fromisoformat(d["d"]) if d.get("d") else None,
                   d.get("c"), d.get("cur") or "UZS", d.get("cn"), d.get("r"), d.get("lt"), bool(d.get("cat")))

    @classmethod
    def from_summary(cls, row: LotSummary, kind: str) -> CatalogRow:
        return cls(row.source, row.external_id, row.url, row.title, kind, row.deadline_at, row.start_cost,
                   row.currency, row.customer_name, row.customer_region, row.lot_type, row.title_is_category)
