"""💼 Freelance: menu, /jobs, /job, and the job-card buttons (approve / edit / rewrite / skip)."""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.cards import PLATFORM_NAME, format_job_card
from app.bot.keyboards import JobAction, job_card_keyboard
from app.bot.menu import FREELANCE_BTN, TENDERS_BTN
from app.core.states import STATUS_LABEL_UZ, JobStatus
from app.pipeline.service import FreelanceService

log = logging.getLogger(__name__)
router = Router(name="freelance")

FREELANCE_HELP = (
    "💼 <b>Frilans</b>\n"
    "Agent Freelancer.com, FL.ru, Kwork, Truelancer va (Gmail ulansa) Upwork/Fiverr'dan profilingizga mos "
    "yangi ishlarni topadi, Gemini bilan "
    "baholaydi va yaxshilariga taklif matni + narx yozib, shu yerga karta yuboradi.\n"
    "Mijozga <b>hech narsa o'zi yubormaydi</b> — ✅ bosganingizdan keyin matnni saytga o'zingiz "
    "joylaysiz (API token ulanmaguncha).\n\n"
    "/jobs — oxirgi ishlar · /job &lt;id&gt; — karta · /llmtest — Gemini tekshiruvi"
)


class FreelanceMenu(CallbackData, prefix="fmenu"):
    action: str  # recent | waiting | run | status


class EditFlow(StatesGroup):
    text = State()
    instruction = State()


def menu_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⏳ Tasdiq kutayotganlar", callback_data=FreelanceMenu(action="waiting"))
    kb.button(text="📋 Oxirgi ishlar", callback_data=FreelanceMenu(action="recent"))
    kb.button(text="🔄 Hozir qidirish", callback_data=FreelanceMenu(action="run"))
    kb.button(text="🩺 Holat", callback_data=FreelanceMenu(action="status"))
    kb.adjust(2, 2)
    return kb.as_markup()


def job_line(job) -> str:
    fit = f"fit {job.fit}" if job.fit is not None else "—"
    return (f"• <b>#{job.id}</b> {STATUS_LABEL_UZ.get(JobStatus(job.status), job.status)} · {fit} · "
            f"{html.escape(job.title[:60])} — /job {job.id}")


async def send_jobs(message: Message, svc: FreelanceService, *, waiting: bool) -> None:
    jobs = await svc.recent(15 if waiting else 10, (JobStatus.AWAITING_APPROVAL,) if waiting else None)
    if not jobs:
        await message.answer("⏳ Tasdiq kutayotgan ish yo'q." if waiting else
                             "📋 Hozircha mos ish topilmadi. Agent har 10 daqiqada qidiradi.")
        return
    title = "⏳ <b>Tasdiq kutayotganlar</b>" if waiting else "📋 <b>Oxirgi ishlar</b>"
    await message.answer("\n".join([title, *map(job_line, jobs)]))


@router.message(F.text == FREELANCE_BTN)
@router.message(Command("freelance"))
async def freelance_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(FREELANCE_HELP, reply_markup=menu_keyboard())


@router.callback_query(FreelanceMenu.filter())
async def menu_click(cb: CallbackQuery, callback_data: FreelanceMenu, freelance_service: FreelanceService) -> None:
    action = callback_data.action
    if action in ("recent", "waiting"):
        await cb.answer()
        await send_jobs(cb.message, freelance_service, waiting=action == "waiting")
    elif action == "status":
        await cb.answer()
        await cb.message.answer(await freelance_service.status_text())
    elif action == "run":
        if freelance_service.running:
            await cb.answer("Qidiruv allaqachon ketyapti…")
            return
        await cb.answer("Qidiruv boshlandi")
        await cb.message.answer("🔄 Barcha platformalar qidirilmoqda (Truelancer brauzer orqali ~2 daqiqa)… "
                                "Mos ish topilsa, karta keladi.")
        asyncio.create_task(_run_and_report(cb.message, freelance_service))


async def _run_and_report(message: Message, svc: FreelanceService) -> None:
    st = await svc.run_cycle(force=True)
    await message.answer(
        f"✅ Qidiruv tugadi: {st.fetched} ta loyiha ko'rildi, {st.new} ta yangi, filtrdan o'tmadi {st.filtered}, "
        f"Gemini baholadi {st.scored} (o'tkazdi {st.skipped_by_ai}), yangi taklif kartasi: {st.drafted}."
    )


@router.message(Command("jobs"))
async def cmd_jobs(message: Message, freelance_service: FreelanceService) -> None:
    await send_jobs(message, freelance_service, waiting=False)


@router.message(Command("job"))
async def cmd_job(message: Message, command: CommandObject, freelance_service: FreelanceService) -> None:
    arg = (command.args or "").strip().lstrip("#")
    if not arg.isdigit():
        await message.answer("Foydalanish: /job 12")
        return
    job, text = await freelance_service.card(int(arg))
    markup = job_card_keyboard(job.id, job.url) if job and job.status == JobStatus.AWAITING_APPROVAL else None
    await message.answer(text, reply_markup=markup)


# ── Card buttons ────────────────────────────────────────────────────────────
@router.callback_query(JobAction.filter(F.job_id == 0))
async def demo_buttons(cb: CallbackQuery, callback_data: JobAction) -> None:
    await cb.answer(f"Bu test kartasi — «{callback_data.action}» hech narsa qilmaydi.")


@router.callback_query(JobAction.filter())
async def job_click(cb: CallbackQuery, callback_data: JobAction, state: FSMContext,
                    freelance_service: FreelanceService) -> None:
    job_id, action = callback_data.job_id, callback_data.action
    if action == "approve":
        result = await freelance_service.approve(job_id, cb.from_user.id)
        if result not in ("manual", "queued"):
            await cb.answer(result, show_alert=True)
            return
        await cb.answer("✅ Tasdiqlandi")
        with contextlib.suppress(TelegramBadRequest):
            await cb.message.edit_reply_markup(reply_markup=None)
        job, _ = await freelance_service.card(job_id)
        async with freelance_service.sessionmaker() as s:
            _, proposal = await freelance_service.latest(s, job_id)
        platform = PLATFORM_NAME.get(job.platform, job.platform)
        if result == "manual":
            await cb.message.answer(
                f"✅ <b>#{job_id} tasdiqlandi.</b> {html.escape(platform)} uchun avtomatik yuborish hali ulanmagan "
                "(API token yo'q / dry-run), shuning uchun taklifni o'zingiz joylang:\n"
                f"1) Saytda oching: {html.escape(job.url)}\n"
                f"2) Narx: <b>{proposal.price:g} {html.escape(proposal.currency)}</b>"
                f"{', muddat ' + str(proposal.timeline_days) + ' kun' if proposal.timeline_days else ''}\n"
                "3) Matnni nusxalang (bosib turing → Copy):"
            )
            await cb.message.answer(f"<code>{html.escape(proposal.text)}</code>")
        else:
            await cb.message.answer(f"✅ #{job_id} tasdiqlandi — yuborish navbatiga qo'yildi.")
    elif action == "skip":
        await freelance_service.skip(job_id, cb.from_user.id)
        await cb.answer("⏭ O'tkazib yuborildi")
        with contextlib.suppress(TelegramBadRequest):
            await cb.message.edit_reply_markup(reply_markup=None)
    elif action == "edit":
        await state.set_state(EditFlow.text)
        await state.update_data(job_id=job_id)
        await cb.answer()
        await cb.message.answer(f"✏️ #{job_id} uchun yangi taklif matnini bitta xabarda yuboring. "
                                "Bekor qilish: /cancel")
    elif action == "rewrite":
        await state.set_state(EditFlow.instruction)
        await state.update_data(job_id=job_id)
        await cb.answer()
        await cb.message.answer(f"🔁 #{job_id}: qanday qayta yozay? Masalan: <i>qisqaroq, Django tajribasini "
                                "ta'kidla, narxni 80$ qil</i>. Bekor qilish: /cancel")


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Bekor qilindi.")


_NOT_MENU = F.text & ~F.text.startswith("/") & ~F.text.in_({FREELANCE_BTN, TENDERS_BTN})


@router.message(EditFlow.text, _NOT_MENU)
async def got_text(message: Message, state: FSMContext, freelance_service: FreelanceService) -> None:
    job_id = (await state.get_data())["job_id"]
    await state.clear()
    job, proposal = await freelance_service.replace_text(job_id, message.text.strip())
    if job is None:
        await message.answer("Bu ish endi tasdiq kutmayapti.")
        return
    await message.answer(format_job_card(job, proposal), reply_markup=job_card_keyboard(job.id, job.url))


@router.message(EditFlow.instruction, _NOT_MENU)
async def got_instruction(message: Message, state: FSMContext, freelance_service: FreelanceService) -> None:
    job_id = (await state.get_data())["job_id"]
    await state.clear()
    await message.answer("🔁 Gemini qayta yozmoqda…")
    try:
        job, proposal = await freelance_service.rewrite(job_id, message.text.strip())
    except Exception as e:
        log.exception("freelance.rewrite_failed")
        await message.answer(f"❌ Qayta yozib bo'lmadi: <code>{html.escape(str(e)[:200])}</code>")
        return
    if job is None:
        await message.answer("Bu ish endi tasdiq kutmayapti.")
        return
    await message.answer(format_job_card(job, proposal), reply_markup=job_card_keyboard(job.id, job.url))
