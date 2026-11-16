"""Telegram texts for tenders (Uzbek, HTML). All source text is escaped — it is untrusted."""

from __future__ import annotations

from datetime import datetime
from html import escape as _escape

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.db.models import Lot, LotStatus
from app.tenders.timeutil import fmt_local, remaining_uz

UNKNOWN_CUSTOMER = "buyurtmachi noma'lum"
SOURCE_NAME = {"etender": "etender", "xarid": "xarid"}
VERDICT_BADGE = {"QATNASHAMIZ": "✅ QATNASHAMIZ", "SHARTLI": "🟡 SHARTLI", "YO'Q": "❌ YO'Q"}
STATUS_UZ = {
    LotStatus.NEW: "🆕 yangi",
    LotStatus.NOTIFIED: "📬 xabar qilingan",
    LotStatus.IGNORED: "🚫 kerak emas",
    LotStatus.BIDDING: "📌 qatnashyapmiz",
    LotStatus.CLOSED: "🔒 yopilgan",
}


def esc(value) -> str:
    return _escape(str(value), quote=False) if value is not None else ""


def short(text: str | None, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def money_uzs(amount: float | None, currency: str = "UZS") -> str:
    if amount is None:
        return "noma'lum"
    if currency != "UZS":
        return f"{amount:,.0f} {currency}".replace(",", " ")
    if amount >= 1e9:
        return f"{amount / 1e9:.2f}".rstrip("0").rstrip(".") + " mlrd so'm"
    if amount >= 1e6:
        return f"{amount / 1e6:.1f}".rstrip("0").rstrip(".") + " mln so'm"
    return f"{amount:,.0f}".replace(",", " ") + " so'm"


def usd(amount: float | None, currency: str, rate: float | None) -> float | None:
    if amount is None:
        return None
    if currency == "USD":
        return amount
    if currency == "UZS" and rate:
        return amount / rate
    return None


def fmt_usd(value: float | None) -> str:
    return f"≈ ${value:,.0f}" if value is not None else ""


def pledge_usd(lot: Lot) -> float | None:
    return usd(lot.pledge_amount, lot.currency, lot.usd_rate)


def pledge_line(lot: Lot, used_usd: float, limit_usd: float) -> str:
    if not lot.pledge_percent:
        return "Zakalat yo'q"
    amount = money_uzs(lot.pledge_amount, lot.currency).replace(" so'm", "")
    value = pledge_usd(lot)
    line = f"Zakalat {lot.pledge_percent:g}% = {amount}"
    if value is not None:
        line += f" ({fmt_usd(value)})"
        if limit_usd:
            if used_usd + value <= limit_usd:
                line += " ✅ limit ichida"
            elif used_usd:
                line += f" ❌ limitdan oshadi (band: ${used_usd:,.0f} / ${limit_usd:,.0f})"
            else:
                line += f" ❌ limitdan oshadi (limit ${limit_usd:,.0f})"
    return line


def header(lot: Lot, prefix: str = "🆕 Yangi IT lot") -> str:
    a = lot.analysis or {}
    if not a:
        return f"{prefix} — ⏳ AI tahlili kutilmoqda"
    if a.get("error"):
        return f"{prefix} — ⚠️ baholanmagan"
    badge = VERDICT_BADGE.get(lot.verdict or "", lot.verdict or "?")
    win = a.get("win_chance_percent") or []
    win_txt = f", yutish ≈ {win[0]}–{win[1]}%" if len(win) == 2 else ""
    return f"{prefix} — {badge} (fit {a.get('fit_score', '?')}/100{win_txt})"


def lot_card(lot: Lot, *, used_usd: float = 0.0, limit_usd: float = 0.0, now: datetime | None = None) -> str:
    cost_usd = usd(lot.start_cost, lot.currency, lot.usd_rate)
    lines = [
        f"<b>{esc(header(lot))}</b>",
        f"📌 <b>{esc(lot.external_id)}</b> — {esc(short(lot.customer_name, 90) or UNKNOWN_CUSTOMER)}",
        f"🏛 {esc(SOURCE_NAME.get(lot.source, lot.source))} · {esc(lot.lot_type or '?')}",
        f"🛠 {esc(short((lot.analysis or {}).get('summary_uz') or lot.title, 220))}",
        f"💰 {esc(money_uzs(lot.start_cost, lot.currency))}"
        + (f" ({fmt_usd(cost_usd)})" if cost_usd is not None else "")
        + f" | {esc(pledge_line(lot, used_usd, limit_usd))}",
    ]
    weights = []
    if lot.price_weight is not None and lot.tech_weight is not None:
        weights.append(f"Narx {lot.price_weight:g}% / texnik {lot.tech_weight:g}%")
    elif lot.valuation_method:
        weights.append(lot.valuation_method)
    if lot.pass_mark is not None:
        weights.append(f"O'tish bali {lot.pass_mark:g}")
    if weights:
        lines.append("⚖️ " + esc(" | ".join(weights)))
    lines.append(f"⏰ Topshirish: {fmt_local(lot.deadline_at)} ({remaining_uz(lot.deadline_at, now)})")
    a = lot.analysis or {}
    blockers = [b.rstrip(" .") for b in a.get("blockers") or []]
    risks = [r.rstrip(" .") for r in a.get("risks") or []]
    if blockers:
        lines.append("⛔ To'siqlar: " + esc(short("; ".join(blockers[:2]), 300)))
    if risks:
        lines.append("⚠️ Risklar: " + esc(short("; ".join(risks[:2]), 300)))
    elif lot.matched_keywords:
        lines.append("🏷 " + esc(", ".join(lot.matched_keywords[:6])))
    lines.append(f"🔗 {esc(lot.url)}")
    return "\n".join(lines)


def lot_full(lot: Lot, *, used_usd: float = 0.0, limit_usd: float = 0.0, now: datetime | None = None) -> str:
    out = [lot_card(lot, used_usd=used_usd, limit_usd=limit_usd, now=now), ""]
    out.append(f"<b>Holat:</b> {STATUS_UZ.get(lot.status, lot.status)} · sayt: {esc(lot.platform_status or '?')}")
    out.append(f"<b>Turi:</b> {esc(lot.lot_type or '?')} · <b>Viloyat:</b> {esc(lot.customer_region or '?')}"
               f" · <b>INN:</b> {esc(lot.customer_tin or '?')}")
    terms = [
        f"avans {lot.advance_percent:g}%" if lot.advance_percent is not None else None,
        f"to'lov {lot.payment_days} kun" if lot.payment_days else None,
        f"ish muddati {lot.execution_days} kun" if lot.execution_days else None,
        f"kafolat {lot.warranty}" if lot.warranty else None,
    ]
    out.append("<b>Shartlar:</b> " + esc(", ".join(t for t in terms if t) or "noma'lum"))
    out.append(f"<b>E'lon:</b> {fmt_local(lot.published_at)} · <b>Til:</b> {esc(', '.join(lot.languages) or '?')}"
               f" · <b>Moliya:</b> {esc(lot.financing_source or '?')}")
    if lot.criteria:
        out.append("\n<b>Texnik mezonlar:</b>")
        for c in lot.criteria[:12]:
            score = f" — {c.get('max_score'):g} ball" if c.get("max_score") is not None else ""
            out.append(f"• {esc(short(c.get('label'), 160))}{score}")
    if lot.qualification:
        out.append("\n<b>Malaka talablari:</b>")
        out.extend(f"• {esc(short(q, 200))}" for q in lot.qualification[:10])
    if lot.files:
        out.append("\n<b>Fayllar:</b> " + esc(", ".join(
            f"{f.get('label')}{' ✓' if f.get('local') else ''}" for f in lot.files)))
    a = lot.analysis or {}
    for key, title in [("blockers", "⛔ To'siqlar"), ("contradictions", "↔️ Qarama-qarshiliklar"),
                       ("questions_for_customer_uz", "❓ Buyurtmachiga savollar")]:
        if a.get(key):
            out.append(f"\n<b>{title}:</b>")
            out.extend(f"• {esc(short(x, 250))}" for x in a[key][:8])
    if a.get("verdict_reason_uz"):
        out.append(f"\n<b>Xulosa:</b> {esc(a['verdict_reason_uz'])}")
    if a and not a.get("error"):
        tech = a.get("estimated_tech_score") or []
        extra = []
        if len(tech) == 2:
            extra.append(f"taxminiy texnik ball {tech[0]:g}–{tech[1]:g}"
                         + (f" (o'tish {a['pass_mark']:g})" if a.get("pass_mark") is not None else ""))
        if a.get("hardware_or_server_required"):
            extra.append(f"server/uskuna: {short(a['hardware_or_server_required'], 160)}")
        q_deadline = (a.get("key_dates") or {}).get("questions_deadline_estimate")
        if q_deadline:
            extra.append(f"savol muddati (taxmin): {q_deadline}")
        if extra:
            out.append("<b>AI:</b> " + esc("; ".join(extra)))
        docs = a.get("_docs") or []
        if docs:
            read = [d["label"] for d in docs if d.get("mode") != "skipped"]
            missed = [f"{d['label']} ({d.get('note')})" for d in docs if d.get("mode") == "skipped"]
            out.append("📄 <i>O'qildi: " + esc(", ".join(read) or "—")
                       + ("; o'qilmadi: " + esc(short(", ".join(missed), 250)) if missed else "") + "</i>")
    elif a.get("error"):
        out.append(f"\n⚠️ <b>AI baholamadi:</b> {esc(short(a.get('detail') or a['error'], 250))}. "
                   "Qayta urinish: /analyze " + esc(lot.external_id))
    return "\n".join(out)


def change_message(lot: Lot, changes: list[str]) -> str:
    return "\n".join(
        [
            "<b>🔄 O'zgarish</b>",
            f"📌 <b>{esc(lot.external_id)}</b> — {esc(short(lot.customer_name, 90))}",
            f"🛠 {esc(short(lot.title, 160))}",
            *[f"• {esc(c)}" for c in changes],
            f"🔗 {esc(lot.url)}",
        ]
    )


VERDICT_DOT = {"QATNASHAMIZ": "✅", "SHARTLI": "🟡", "YO'Q": "❌"}


def one_line(lot: Lot, now: datetime | None = None) -> str:
    a = lot.analysis or {}
    dot = VERDICT_DOT.get(lot.verdict or "", "⚠️" if a.get("error") else "•")
    fit = f" ({a['fit_score']})" if a.get("fit_score") is not None else ""
    return (
        f"{dot}{fit} <b>{esc(lot.external_id)}</b> {esc(short(lot.title, 70))} — "
        f"{esc(money_uzs(lot.start_cost, lot.currency))}, {remaining_uz(lot.deadline_at, now)}"
    )


# ── Keyboards ───────────────────────────────────────────────────────────────
class TenderAction(CallbackData, prefix="tnd"):
    action: str  # bid | ignore | full | unbid
    lot_id: int


class TenderMenu(CallbackData, prefix="tmenu"):
    action: str  # open | bidding | scan | status | report


def lot_keyboard(lot: Lot) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if lot.status == LotStatus.BIDDING:
        kb.button(text="↩️ Qatnashmaymiz", callback_data=TenderAction(action="unbid", lot_id=lot.id))
    else:
        kb.button(text="📌 Qatnashamiz", callback_data=TenderAction(action="bid", lot_id=lot.id))
    if lot.status != LotStatus.IGNORED:
        kb.button(text="🚫 Kerak emas", callback_data=TenderAction(action="ignore", lot_id=lot.id))
    kb.button(text="📄 To'liq", callback_data=TenderAction(action="full", lot_id=lot.id))
    kb.button(text="🔗 Saytda ochish", url=lot.url)
    kb.adjust(2, 2)
    return kb.as_markup()


def tender_menu_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📋 Ochiq lotlar", callback_data=TenderMenu(action="open"))
    kb.button(text="📌 Qatnashayotganlar", callback_data=TenderMenu(action="bidding"))
    kb.button(text="🔄 Hozir tekshirish", callback_data=TenderMenu(action="scan"))
    kb.button(text="📊 Hisobot", callback_data=TenderMenu(action="report"))
    kb.button(text="🩺 Holat", callback_data=TenderMenu(action="status"))
    kb.adjust(2, 2, 1)
    return kb.as_markup()
