"""Telegram commands. Owner filtering happens in OwnerOnlyMiddleware before any handler runs."""

from __future__ import annotations

import html
import logging
from datetime import UTC, datetime, timedelta

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.bot.cards import format_job_card
from app.bot.keyboards import job_card_keyboard
from app.bot.menu import main_menu
from app.config import Settings
from app.core.states import JobStatus
from app.db.models import Job, Proposal
from app.llm.gemini import GeminiClient
from app.llm.usage import cost_since

log = logging.getLogger(__name__)
router = Router(name="main")

NOT_YET = "⏳ Bu buyruq keyingi bosqichda qo'shiladi."


@router.message(CommandStart())
async def cmd_start(message: Message, settings: Settings) -> None:
    cfg = settings.config
    enabled = [
        f"{name}{' (dry-run)' if p.dry_run else ''}" for name, p in cfg.platforms.items() if p.enabled
    ]
    tenders = (
        f"yoqilgan, har {cfg.tenders.poll_interval_min} daqiqada tekshiradi"
        if cfg.tenders.enabled
        else "o'chiq"
    )
    await message.answer(
        f"👋 Salom, {html.escape(settings.profile.name.split()[0])}! Agent ishlayapti.\n\n"
        f"🏛 <b>Tenderlar:</b> {tenders}\n"
        f"💼 <b>Frilans platformalar:</b> {html.escape(', '.join(enabled) or 'hali ulanmagan')}\n"
        f"🤖 <b>Gemini:</b> {html.escape(cfg.gemini.fast_model)}\n\n"
        "Pastdagi tugmalar bilan bo'limni tanlang 👇",
        reply_markup=main_menu(),
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer("💼 Frilans: /freelance · 🏛 Tenderlar: /tenders\n"
                         "Yoki pastdagi tugmalardan foydalaning.", reply_markup=main_menu())


@router.message(Command("test"))
async def cmd_test(message: Message) -> None:
    """Render a fake job card so the layout and buttons can be checked. Nothing is saved."""
    job = Job(
        id=0,
        platform="truelancer",
        external_id="demo",
        url="https://www.truelancer.com/",
        title="Python API automation",
        currency="INR",
        budget_max=1000,
        budget_usd=11,
        proposals_count=8,
        posted_at=datetime.now(UTC) - timedelta(hours=2),
        fit=86,
        win_probability=40,
        risk_flags=[],
        summary_uz="Mijoz REST API orqali ma'lumotlarni avtomatik yig'uvchi Python skript "
        "so'ramoqda. Vazifa aniq va hajmi kichik.",
        status=JobStatus.AWAITING_APPROVAL,
    )
    proposal = Proposal(
        job_id=0,
        text="(Namuna) Hi! You need a Python script that pulls data from your API on a "
        "schedule and saves it cleanly. Plan: 1) ... 2) ... 3) ...",
        price=1000,
        currency="INR",
        deposit=400,
        timeline_days=2,
    )
    await message.answer(
        "🧪 <i>Test kartasi — tugmalar hali hech narsa yubormaydi.</i>\n\n"
        + format_job_card(job, proposal),
        reply_markup=job_card_keyboard(0, job.url),
    )


class _Ping(BaseModel):
    ok: bool
    reply_uz: str = Field(description="One short friendly sentence in Uzbek.")


@router.message(Command("llmtest"))
async def cmd_llmtest(
    message: Message, gemini: GeminiClient, sessionmaker: async_sessionmaker
) -> None:
    await message.answer("⏳ Gemini tekshirilmoqda…")
    try:
        result = await gemini.generate_json(
            "Return ok=true and greet a freelance developer in Uzbek.",
            _Ping,
            role="fast",
            purpose="llmtest",
        )
    except Exception as e:
        log.exception("llmtest.failed")
        await message.answer(f"❌ Gemini xatosi: <code>{html.escape(str(e)[:500])}</code>")
        return
    calls, cost = await cost_since(sessionmaker, hours=24)
    await message.answer(
        f"✅ Gemini ishlayapti ({html.escape(gemini.model_for('fast'))}).\n"
        f"«{html.escape(result.reply_uz)}»\n\n"
        f"24 soat: {calls} ta chaqiruv · ~${cost:.4f}"
    )


@router.message(
    Command(
        "stats", "quota", "pause", "resume", "settings", "profile", "export"
    )
)
async def not_yet(message: Message) -> None:
    await message.answer(NOT_YET)
