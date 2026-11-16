"""🏛 Tender commands and buttons. Owner-only filtering is done by OwnerOnlyMiddleware."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from app.bot.menu import TENDERS_BTN
from app.bot.notifier import split_text
from app.db.models import LotStatus
from app.tenders import cards
from app.tenders.service import TenderService

log = logging.getLogger(__name__)
router = Router(name="tenders")

TENDERS_HELP = (
    "🏛 <b>Davlat tenderlari</b>\n"
    "Agent har 30 daqiqada etender'dagi yangi IT lotlarni topadi, TZ'ni Gemini bilan tahlil qiladi "
    "va shu yerga yuboradi (❌ YO'Q lotlar faqat kunlik hisobotda). "
    "U <b>hech qachon</b> taklif topshirmaydi, E-IMZO ishlatmaydi va buyurtmachiga yozmaydi.\n\n"
    "/list — ochiq IT lotlar\n"
    "/lot &lt;raqam&gt; — to'liq ma'lumot\n"
    "/bid &lt;raqam&gt; — qatnashamiz (eslatmalar yoqiladi)\n"
    "/ignore &lt;raqam&gt; — kerak emas\n"
    "/analyze &lt;raqam&gt; — AI bilan qayta tahlil\n"
    "/status — agent holati"
)


async def _reply_long(message: Message, text: str, **kw) -> None:
    parts = split_text(text)
    for i, part in enumerate(parts):
        await message.answer(part, **(kw if i == len(parts) - 1 else {}))


async def _send_list(message: Message, svc: TenderService, *, bidding_only: bool) -> None:
    lots = await svc.open_lots(bidding_only=bidding_only)
    if not lots:
        await message.answer(
            "📌 Hozircha qatnashayotgan lot yo'q." if bidding_only
            else "📋 Hozircha ochiq IT lot yo'q. Agent har 30 daqiqada tekshiradi."
        )
        return
    title = "📌 <b>Qatnashayotganlar</b>" if bidding_only else f"📋 <b>Ochiq IT lotlar: {len(lots)}</b>"
    lines = [title]
    for lot in lots[:40]:
        mark = "📌 " if lot.status == LotStatus.BIDDING else ""
        lines.append(mark + cards.one_line(lot))
    lines.append("\nBatafsil: /lot &lt;raqam&gt;")
    await _reply_long(message, "\n".join(lines))


# ── Menu ────────────────────────────────────────────────────────────────────
@router.message(F.text == TENDERS_BTN)
@router.message(Command("tenders"))
async def tenders_menu(message: Message) -> None:
    await message.answer(TENDERS_HELP, reply_markup=cards.tender_menu_keyboard())


@router.callback_query(cards.TenderMenu.filter())
async def menu_click(cb: CallbackQuery, callback_data: cards.TenderMenu, tender_service: TenderService) -> None:
    action = callback_data.action
    msg = cb.message
    if action == "open":
        await cb.answer()
        await _send_list(msg, tender_service, bidding_only=False)
    elif action == "bidding":
        await cb.answer()
        await _send_list(msg, tender_service, bidding_only=True)
    elif action == "status":
        await cb.answer()
        await msg.answer(await tender_service.status_text())
    elif action == "report":
        await cb.answer()
        await _reply_long(msg, await tender_service.daily_report())
    elif action == "scan":
        if tender_service.scanning:
            await cb.answer("Tekshiruv allaqachon ketyapti…", show_alert=False)
            return
        await cb.answer("Tekshiruv boshlandi")
        await msg.answer("🔄 etender tekshirilmoqda… Yangi IT lotlar topilsa, alohida xabar keladi.")
        asyncio.create_task(_scan_and_report(msg, tender_service))


async def _scan_and_report(message: Message, svc: TenderService) -> None:
    try:
        results = await svc.run_scan()
    except Exception as e:  # pragma: no cover - run_scan already isolates sources
        await message.answer(f"❌ Tekshiruv xatosi: {cards.esc(str(e)[:300])}")
        return
    if not results:
        await message.answer("⚠️ Tekshiruv tugadi, lekin manbalar javob bermadi. /status")
        return
    parts = [
        f"{name}: {st.listed} ta ochiq lot, {st.new_seen} ta yangi, {st.new_lots} ta yangi IT lot"
        for name, st in results.items()
    ]
    await message.answer("✅ Tekshiruv tugadi.\n" + cards.esc("\n".join(parts)))


# ── Commands ────────────────────────────────────────────────────────────────
@router.message(Command("list"))
async def cmd_list(message: Message, tender_service: TenderService) -> None:
    await _send_list(message, tender_service, bidding_only=False)


@router.message(Command("lot"))
async def cmd_lot(message: Message, command: CommandObject, tender_service: TenderService) -> None:
    if not command.args:
        await message.answer("Foydalanish: /lot 524524")
        return
    lot, text = await tender_service.lot_text(command.args.split()[0])
    await _reply_long(message, text, reply_markup=cards.lot_keyboard(lot) if lot else None)


async def _set(message: Message, command: CommandObject, svc: TenderService, status: LotStatus) -> None:
    if not command.args:
        await message.answer(f"Foydalanish: /{command.command} 524524")
        return
    ref = command.args.split()[0]
    lot = await svc.set_status(ref, status)
    if lot is None:
        await message.answer(f"«{cards.esc(ref)}» raqamli lot bazada yo'q. /list")
        return
    await message.answer(_status_reply(lot.external_id, status))


def _status_reply(external_id: str, status: LotStatus) -> str:
    if status == LotStatus.BIDDING:
        return (f"📌 {cards.esc(external_id)} — qatnashamiz. Eslatmalar yoqildi: savol muddati, "
                "zakalat (3 ish kuni), topshirishga 24 va 3 soat qolganda.")
    if status == LotStatus.IGNORED:
        return f"🚫 {cards.esc(external_id)} — kerak emas deb belgilandi."
    return f"↩️ {cards.esc(external_id)} — qatnashish bekor qilindi, eslatmalar o'chdi."


@router.message(Command("bid"))
async def cmd_bid(message: Message, command: CommandObject, tender_service: TenderService) -> None:
    await _set(message, command, tender_service, LotStatus.BIDDING)


@router.message(Command("ignore"))
async def cmd_ignore(message: Message, command: CommandObject, tender_service: TenderService) -> None:
    await _set(message, command, tender_service, LotStatus.IGNORED)


@router.message(Command("analyze"))
async def cmd_analyze(message: Message, command: CommandObject, tender_service: TenderService) -> None:
    if not command.args:
        await message.answer("Foydalanish: /analyze 524524 — lotni Gemini bilan qayta tahlil qilish")
        return
    await message.answer("🤖 Tahlil qilinmoqda… (TZ o'qiladi, 10–60 soniya)")
    lot, text = await tender_service.reanalyze(command.args.split()[0])
    await _reply_long(message, text, reply_markup=cards.lot_keyboard(lot) if lot else None)


@router.message(Command("status"))
async def cmd_status(message: Message, tender_service: TenderService) -> None:
    await message.answer(await tender_service.status_text())


# ── Buttons on a lot card ───────────────────────────────────────────────────
@router.callback_query(cards.TenderAction.filter())
async def lot_click(cb: CallbackQuery, callback_data: cards.TenderAction, tender_service: TenderService) -> None:
    action, lot_id = callback_data.action, callback_data.lot_id
    if action == "full":
        await cb.answer()
        lot, text = await tender_service.lot_text(lot_id)
        await _reply_long(cb.message, text, reply_markup=cards.lot_keyboard(lot) if lot else None)
        return
    status = {"bid": LotStatus.BIDDING, "ignore": LotStatus.IGNORED, "unbid": LotStatus.NOTIFIED}[action]
    lot = await tender_service.set_status(lot_id, status)
    if lot is None:
        await cb.answer("Lot topilmadi", show_alert=True)
        return
    await cb.answer({"bid": "📌 Qatnashamiz", "ignore": "🚫 Kerak emas", "unbid": "↩️ Bekor qilindi"}[action])
    with contextlib.suppress(TelegramBadRequest):  # "message is not modified"
        await cb.message.edit_reply_markup(reply_markup=cards.lot_keyboard(lot))
    await cb.message.answer(_status_reply(lot.external_id, status))
