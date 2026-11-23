"""Gemini summary of one goods lot for the «Uzum narxlari» lot page: what is bought, how the tender prices
compare with Uzum, whether supplying it is worth it, red flags. Cached in KV `pai:<lot id>` by PriceWatch."""

from __future__ import annotations

import json
import logging
from datetime import datetime, time
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.db.models import LLMCall
from app.llm.prompts import load_prompt
from app.llm.safety import new_tag, untrusted_rule, wrap_untrusted
from app.tenders import cards
from app.tenders.prices import AI_PRICE_FOR, STATUS
from app.tenders.timeutil import TASHKENT, as_utc, now_utc

log = logging.getLogger(__name__)
PURPOSE = "goods_analysis"
PRICE_VERDICTS = ("QIMMAT", "ME'YORIDA", "ARZON", "NOANIQ")
SUPPLY_VERDICTS = ("QATNASHISH MUMKIN", "SHARTLI", "TAVSIYA ETILMAYDI")


class ItemNote(BaseModel):
    name: str
    note: str


class ItemPrice(BaseModel):
    i: int = Field(description="mahsulotning «i» raqami")
    price_uzs: float = Field(description="1 birlik uchun O'zbekistondagi odatiy bozor narxi, so'm, QQS bilan")
    basis: str = Field(description="qisqa: nimaga asoslanib (masalan: 'A-92 benzin 1 litr chakana')")


class GoodsAnalysis(BaseModel):
    summary_uz: str = Field(description="2-3 gap: nima, qancha, kim uchun xarid qilinmoqda")
    price_verdict: Literal["QIMMAT", "ME'YORIDA", "ARZON", "NOANIQ"]
    price_comment_uz: str = Field(description="tender narxlari Uzum narxiga nisbatan qanday, 1-3 gap")
    supplier_verdict: Literal["QATNASHISH MUMKIN", "SHARTLI", "TAVSIYA ETILMAYDI"]
    supplier_reason_uz: str
    est_margin_percent: list[int] = Field(description="[min, max] yoki []")
    win_chance_percent: list[int] = Field(description="[min, max], 0-100: yetkazib beruvchi yutish ehtimoli")
    key_conditions: list[str] = Field(description="yetkazish, to'lov, zakalat, kafolat shartlari")
    risks: list[str]
    red_flags: list[str]
    item_notes: list[ItemNote]
    item_prices: list[ItemPrice] = Field(description="ai_narx_kerak=true bo'lgan har bir mahsulot uchun")
    questions_for_customer_uz: list[str]


def _money(v: float | None, cur: str = "UZS") -> str | None:
    return cards.money_uzs(v, cur) if v else None


def lot_input(lp, detail: dict[str, Any], big: float) -> dict[str, Any]:
    lot = lp.lot
    d = detail or {}
    ok = lp.on_uzum
    items = []
    for r in lp.rows:
        it = r.item
        row = {"i": r.idx, "nomi": it.name, "miqdori": it.qty, "birlik": it.unit or None,
               "tender_narxi_1ta": cards.money_uzs(r.price_uzs), "holat": STATUS.get(r.status, r.status)}
        if r.status in AI_PRICE_FOR:
            row["ai_narx_kerak"] = True
        if r.status in ("ok", "far"):
            row.update({"uzum_orta": cards.money_uzs(r.uzum_med), "uzum_eng_arzon": cards.money_uzs(r.uzum_min),
                        "uzumda_mos": r.n, "nisbat_tender_uzum": round(r.ratio, 2) if r.ratio else None,
                        "uzumdagi_misol": r.best.title if r.best else None})
        items.append(row)
    return {
        "lot_raqami": lot.external_id, "sayt": lot.source, "predmet": d.get("title") or lot.title,
        "tavsif": d.get("description") or None, "buyurtmachi": d.get("customer") or lot.customer_name,
        "viloyat": d.get("region"), "boshlangich_narx": _money(d.get("cost") or lot.start_cost,
                                                              d.get("cur") or lot.currency),
        "topshirish_muddati": d.get("deadline"), "zakalat_foiz": d.get("pledge_pct"),
        "avans_foiz": d.get("advance_pct"), "tolov_muddati_kun": d.get("pay_days"),
        "yetkazish_muddati_kun": d.get("exec_days"), "kafolat": d.get("warranty"),
        "moliyalash": d.get("financing"), "malaka_talablari": d.get("qualification") or None,
        "jami": {"mahsulotlar": len(lp.rows), "uzum_bilan_solishtirilgan": len(ok),
                 "solishtirilganlar_tenderda": cards.money_uzs(sum(r.price_uzs * (r.item.qty or 1) for r in ok)),
                 "solishtirilganlar_uzumda": cards.money_uzs(sum(r.uzum_med * (r.item.qty or 1) for r in ok))},
        "mahsulotlar": items[:80],
    }


class GoodsAnalyzer:
    def __init__(self, gemini, sessionmaker, *, max_per_day: int = 150) -> None:
        self.gemini = gemini
        self.sessionmaker = sessionmaker
        self.max_per_day = max_per_day
        self.prompt = load_prompt("goods_analysis")

    async def requests_today(self) -> int:
        midnight = datetime.combine(now_utc().astimezone(TASHKENT).date(), time(0), TASHKENT)
        async with self.sessionmaker() as s:
            return await s.scalar(select(func.count()).select_from(LLMCall).where(
                LLMCall.purpose == PURPOSE, LLMCall.created_at >= as_utc(midnight))) or 0

    async def __call__(self, lp, detail: dict[str, Any], *, big: float) -> dict[str, Any]:
        if await self.requests_today() >= self.max_per_day:
            return {"error": "kunlik AI limiti tugadi — ertaga qayta urinib ko'ring"}
        tag = new_tag()
        system = self.prompt.render(untrusted_rule=untrusted_rule(tag))
        body = json.dumps(lot_input(lp, detail, big), ensure_ascii=False, indent=1)
        prompt = (f"Bugun: {now_utc().astimezone(TASHKENT):%d.%m.%Y} (Toshkent).\n\n## LOT\n"
                  + wrap_untrusted("lot", body, tag) + "\n\nYuqoridagi JSON tuzilishi bo'yicha javob ber.")
        res: GoodsAnalysis = await self.gemini.generate_json(prompt, GoodsAnalysis, role="fast", system=system,
                                                             purpose=PURPOSE, temperature=0.2)
        data = res.model_dump()
        for k, lo, hi in (("est_margin_percent", -100, 1000), ("win_chance_percent", 0, 100)):
            m = sorted(min(max(int(x), lo), hi) for x in data[k][:2])
            data[k] = [m[0], m[0]] if len(m) == 1 else m
        for k in ("key_conditions", "risks", "red_flags", "questions_for_customer_uz"):
            data[k] = [x.strip() for x in data[k] if x and x.strip()][:8]
        data["item_notes"] = [n for n in data["item_notes"] if n["name"] and n["note"]][:8]
        want = {r.idx for r in lp.rows if r.status in AI_PRICE_FOR}
        data["item_prices"] = {str(x["i"]): {"p": float(x["price_uzs"]), "n": x["basis"][:160]}
                               for x in data["item_prices"] if x["i"] in want and x["price_uzs"] > 0}
        data["_model"] = self.gemini.model_for("fast")
        data["_prompt"] = self.prompt.version
        log.info("prices.ai_done", extra={"lot": lp.id, "price": data["price_verdict"],
                                          "supply": data["supplier_verdict"]})
        return data
