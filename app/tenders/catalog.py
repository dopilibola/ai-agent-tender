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
    direction: str = ""  # yo'nalish key, see DIRECTIONS (computed on load, so new terms apply at once)

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
                   d.get("c"), d.get("cur") or "UZS", d.get("cn"), d.get("r"), d.get("lt"), bool(d.get("cat")),
                   direction=direction(d["t"]))

    @classmethod
    def from_summary(cls, row: LotSummary, kind: str) -> CatalogRow:
        return cls(row.source, row.external_id, row.url, row.title, kind, row.deadline_at, row.start_cost,
                   row.currency, row.customer_name, row.customer_region, row.lot_type, row.title_is_category,
                   direction=direction(row.title))


# ── Yo'nalish (direction): what the lot is about, for the site filter ─────────
# First match wins, so the order matters (e.g. "ремонт автотранспорта" is transport, not construction).
# Terms run on the etender name or the xarid category, both normalised like the IT filter.
DIRECTIONS: list[tuple[str, str, list[str]]] = [
    ("furniture", "Mebel va inventar", [
        "mebel", "мебел", "stol$", "stollar", "стол$", "столы", "stul", "стул", "kreslo", "кресл", "шкаф",
        "inventar", "инвентар", "trenajyor", "тренажер", "matras", "матрас", "подушк", "одеял", "gilam",
        "гилам", "kresol", "холодильник", "xolodilnik", "бытовая техника", "maishiy texnika", "маиший техника"]),
    ("tech", "Kompyuter va elektronika", [
        "kompyuter", "компьютер", "noutbuk", "ноутбук", "printer", "принтер", "monitor", "монитор", "server",
        "сервер", "kamera", "камер", "kuzatuv", "кузатув", "telefon", "телефон", "terminal", "терминал",
        "elektron", "электрон", "datchik", "датчик", "оптическ", "программ", "telekommunik", "телекоммуник",
        "информационн", "axborot", "ахборот", "litsenziya", "лицензи", "dasturiy", "дастурий", "kaspersky",
        "simsiz", "симсиз", "aloqa", "алоқа", "ситуацион", "vaziyatlar markazi", "erp$", "табло", "tablo"]),
    ("fuel", "Yoqilg'i va energiya", [
        "yoqilg'i", "ёқилғи", "ёкилги", "топлив", "бензин", "dizel", "дизел", "neft", "нефт", "gaz$", "газ$",
        "газ природный", "komir", "кўмир", "уголь", "кокс", "мазут", "электроэнерг", "energiya", "энергия"]),
    ("food", "Oziq-ovqat va ovqatlanish", [
        "oziq", "озиқ", "озик", "продукты питания", "продовольств", "питани", "пищев", "go'sht", "гўшт", "мясо",
        "sut$", "сут$", "молок", "non$", "нон$", "хлеб", "shakar", "шакар", "сахар",
        "shokolad", "шоколад", "keks", "кекс", "kolbasa", "колбас", "kartoshka", "картошка", "гурунч",
        "meva", "мева", "фрукт",
        "sabzavot", "сабзавот", "овощ", "guruch", "гуруч", "makkajo'xori", "konserv", "консерв", "choy$", "чой$",
        "ovqat", "овқат", "овкат", "кейтринг", "keytring", "ichimlik", "ичимлик", "напит"]),
    ("medical", "Dori va tibbiyot", [
        "dori", "дори", "медикамент", "препарат", "лекарств", "tibbiy", "тиббий", "медицин", "фармац", "шприц",
        "здравоохран", "laborator", "лаборатор", "immunoglobulin", "иммуноглобулин", "gematolog", "гематолог",
        "defibrillyator", "дефибриллятор", "ekg", "экг", "kardiomonitor", "кардиомонитор", "sterilizator",
        "стерилизатор", "laparoskop", "лапароскоп", "эндоскоп", "магнитотерап", "кров", "eritma", "эритма",
        "flakon", "флакон", "perinatal", "перинатал", "неонатал", "uzi$", "узи$", "рентген", "rentgen"]),
    ("chemical", "Kimyo va xo'jalik mollari", [
        "химическ", "kimyo", "кимё", "катализатор", "реагент", "sovun", "совун", "мыло", "моющ", "dezinfek",
        "дезинфек", "gigiyen", "гигиен", "резинов", "пластмасс"]),
    ("transport", "Transport, pochta va logistika", [
        "avtomobil", "автомобил", "автотранспорт", "транспорт", "transport", "tashish", "ташиш", "перевоз",
        "ehtiyot qism", "эҳтиёт қисм", "эхтиёт кисм", "запчаст", "запасные части", "shina", "шина",
        "akkumulyator", "аккумулятор", "avtobus", "автобус", "traktor", "трактор", "avtokran", "автокран",
        "автогрейд", "maxsus texnika", "махсус техника", "складир", "logistika", "логистик", "почтов", "pochta",
        "курьер", "kuryer", "воздушного"]),
    ("electric", "Elektr jihozlari", [
        "elektr", "электр", "yoritish", "ёритиш", "освещ", "светил", "lampa", "лампа", "чироқ", "chiroq",
        "transformator", "трансформатор", "generator", "генератор", "svetofor", "светофор", "кабел", "kabel",
        "fotoelektr", "фотоэлектр", "quyosh", "қуёш", "солнечн", "batareya", "батаре", "razedinitel",
        "разъединител", "ajratgich", "выключател", "o'chirgich", "ўчиргич", "учиргич"]),
    ("clothes", "Kiyim va to'qimachilik", [
        "kiyim", "кийим", "одежд", "poyabzal", "пойабзал", "обув", "mato$", "ткан", "текстил", "чойшаб",
        "choyshab", "кож", "tikish", "тикиш", "himoya vosita", "ҳимоя восита", "химоя восита",
        "индивидуальной защиты", "kaska", "каск", "войлок"]),
    ("paper", "Kanselyariya, nashr va reklama", [
        "kanselyar", "канцеляр", "qog'oz", "қоғоз", "когоз", "бумаг", "kitob", "китоб", "книг", "jurnal",
        "журнал", "darslik", "дарслик", "учебник", "издател", "печат", "nashr", "нашр", "reklama", "реклама",
        "кинофильм", "video", "видео", "banner", "баннер", "film", "фильм", "стенд", "stend", "monument",
        "монумент"]),
    ("realty", "Ko'chmas mulk va ijara", [
        "недвижим", "ijara", "ижара", "аренд", "lizing", "лизинг", "ko'chmas mulk", "кўчмас мулк", "mulk",
        "мулк", "yer uchastka", "ер участка", "ер майдон", "проживани"]),
    ("consulting", "Konsalting, audit va ta'lim", [
        "konsalting", "консалтинг", "консульт", "audit", "аудит", "форензик", "baholash", "баҳолаш", "бахолаш",
        "оценк", "bahosini", "юридич", "бухгалтер", "o'qitish", "ўқитиш", "обучени", "trening", "тренинг",
        "maslahat", "маслаҳат", "маслахат", "ekspertiza", "экспертиз", "tadqiqot", "тадқиқот", "исследован",
        "страхован", "sug'urta", "суғурта", "научн", "архитектур", "проектир"]),
    ("agro", "Qishloq va o'rmon xo'jaligi", [
        "сельского хозяйства", "qishloq xo'jalik", "қишлоқ хўжалик", "лесовод", "лесозагот", "urug'", "уруғ",
        "семен", "ko'chat", "кўчат", "саженц", "o'g'it", "ўғит", "удобрен", "корм", "yog'och", "ёғоч", "древес",
        "дрова", "томорқа", "суғориш", "сугориш", "sug'orish"]),
    ("culture", "Sport, madaniyat va tadbirlar", [
        "sport", "спорт", "мяч", "muzey", "музей", "экспонат", "tadbir", "тадбир", "spektakl", "спектакл",
        "развлечен", "творчеств", "искусств", "musiqa", "мусиқа", "музык"]),
    ("facility", "Qo'riqlash, tozalash, kommunal", [
        "qo'riqlash", "қўриқлаш", "куриклаш", "охран", "безопасност", "tozalash", "тозалаш", "уборк", "чистк",
        "отход", "chiqindi", "чиқинди", "обслуживанию зданий", "административного", "водоснабж", "kommunal",
        "коммунал"]),
    ("equipment", "Uskuna va asboblar", [
        "uskuna", "ускуна", "оборудован", "nasos", "насос", "agregat", "агрегат", "stanok", "станок", "машины",
        "kompressor", "компрессор", "filtr", "фильтр", "asbob", "асбоб", "прибор", "инструмент", "o'lchagich",
        "ўлчагич", "tarozi", "тарози", "lift", "лифт", "горнодобыв", "stanog", "press$", "пресс", "дефектоскоп",
        "печь", "печи", "машина", "ножниц", "фреза", "kran", "кран", "редуктор", "дрел", "молоток", "болторез",
        "gidravlik", "гидравлик", "apparat", "аппарат", "analizator", "анализатор", "payvand", "пайванд", "сварк",
        "texnika", "техника"]),
    ("build", "Qurilish va ta'mirlash", [
        "qurilish", "қурилиш", "курилиш", "qurish", "қуриш", "куриш", "строител", "строительн", "rekonstruk",
        "реконструк", "реконсртук", "ta'mir", "таъмир", "тамир", "ремонт", "mukammal", "мукаммал", "капитальн",
        "montaj", "монтаж", "demontaj", "демонтаж", "демантаж", "buzish", "бузиш", "sement", "цемент", "g'isht",
        "ғишт", "кирпич", "beton", "бетон", "armatura", "арматур", "metall", "металл", "quvur", "қувур", "труб",
        "bo'yoq", "бўёқ", "краск", "plitka", "плитк", "стекл", "топосъём", "топосём", "топосъем", "топосем",
        "геодез", "изыскан", "obodonlash", "ободонлаш", "благоустр", "loyiha", "лойиҳа", "лойиха", "проект",
        "здани", "минеральн", "изделия металлические", "kanal", "канал", "коллектор", "kollektor", "yo'l$",
        "йўл$", "йул$", "дорог", "ko'cha", "кўча", "куча", "огнеупор", "o'tga chidamli", "ўтга чидамли",
        "оловга чидамли", "асфальт", "asfalt", "глина", "гравий", "песчан", "песок", "доска", "теплоизол",
        "анкер", "quduq", "қудуқ", "кудук", "қидуқ", "suv ombor", "сув омбор"]),
]
DIRECTION_LABEL = {key: label for key, label, _ in DIRECTIONS} | {"other": "Boshqa"}
_DIRECTION_RX = [(key, _build(terms)) for key, _, terms in DIRECTIONS]


def direction(title: str) -> str:
    text = normalize(title)
    for key, patterns in _DIRECTION_RX:
        if _any(patterns, text):
            return key
    return "other"
