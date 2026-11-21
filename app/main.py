"""Entry point: migrations → DB → Gemini → scheduler → Telegram bot (long polling)."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.bot.feed import FeedSender, backfill
from app.bot.freelance import router as freelance_router
from app.bot.handlers import router
from app.bot.menu import main_menu
from app.bot.middleware import OwnerOnlyMiddleware
from app.bot.notifier import Notifier
from app.config import get_settings
from app.connectors import build_connectors
from app.core.status import flush_notifications
from app.db.session import create_engine, create_sessionmaker, run_migrations
from app.llm.gemini import GeminiClient
from app.llm.usage import make_db_sink
from app.logging_setup import setup_logging
from app.pipeline.service import FreelanceService
from app.tenders.analyzer import TenderAnalyzer
from app.tenders.bot import router as tenders_router
from app.tenders.fx import UsdRate
from app.tenders.service import TenderService
from app.tenders.sources import build_sources

log = logging.getLogger("freelancehunter")

COMMANDS = [
    BotCommand(command="start", description="Asosiy menyu"),
    BotCommand(command="tenders", description="🏛 Tenderlar menyusi"),
    BotCommand(command="list", description="🏛 Ochiq IT lotlar"),
    BotCommand(command="lot", description="🏛 Lot to'liq ma'lumoti: /lot 524524"),
    BotCommand(command="bid", description="🏛 Qatnashamiz: /bid 524524"),
    BotCommand(command="ignore", description="🏛 Kerak emas: /ignore 524524"),
    BotCommand(command="analyze", description="🏛 AI qayta tahlil: /analyze 524524"),
    BotCommand(command="status", description="🏛 Agent holati"),
    BotCommand(command="freelance", description="💼 Frilans menyusi"),
    BotCommand(command="jobs", description="💼 Oxirgi ishlar"),
    BotCommand(command="job", description="💼 Ish kartasi: /job 12"),
    BotCommand(command="cancel", description="Tahrirni bekor qilish"),
    BotCommand(command="stats", description="Statistika"),
    BotCommand(command="quota", description="Kvotalar"),
    BotCommand(command="pause", description="To'xtatish"),
    BotCommand(command="resume", description="Davom ettirish"),
    BotCommand(command="settings", description="Sozlamalar"),
    BotCommand(command="profile", description="Profil"),
    BotCommand(command="export", description="CSV eksport"),
    BotCommand(command="test", description="Test kartasi"),
    BotCommand(command="llmtest", description="Gemini tekshiruvi"),
]


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.secrets.log_level)
    db_url = settings.secrets.database_url

    await run_migrations(db_url)
    engine = create_engine(db_url)
    sessionmaker = create_sessionmaker(engine)

    gemini = GeminiClient(
        settings.secrets.google_api_key.get_secret_value(),
        settings.config.gemini,
        usage_sink=make_db_sink(sessionmaker),
    )

    bot = Bot(
        settings.secrets.telegram_bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    notifier = Notifier(bot, settings.owner_chat_id)
    # Every notification also goes to `sent_messages` → the website shows the same feed as Telegram.
    feeds = {k: FeedSender(notifier, sessionmaker, k) for k in ("tender", "freelance", "email", "status")}

    tcfg = settings.config.tenders
    tender_service = TenderService(
        settings,
        sessionmaker,
        build_sources(tcfg) if tcfg.enabled else {},
        feeds["tender"],
        fx=UsdRate(sessionmaker),
        analyzer=TenderAnalyzer(gemini, settings, sessionmaker) if tcfg.analysis.enabled else None,
    )

    freelance_service = FreelanceService(
        settings, sessionmaker,
        build_connectors(settings, gemini=gemini, sessionmaker=sessionmaker, notify=feeds["email"].send_text),
        gemini, feeds["freelance"], fx=UsdRate(sessionmaker)
    )

    dp = Dispatcher(
        freelance_service=freelance_service,
        settings=settings,
        gemini=gemini,
        notifier=notifier,
        sessionmaker=sessionmaker,
        tender_service=tender_service,
    )
    dp.update.outer_middleware(OwnerOnlyMiddleware(settings.owner_chat_id))
    dp.include_router(router)
    dp.include_router(tenders_router)
    dp.include_router(freelance_router)

    scheduler = AsyncIOScheduler(timezone=settings.config.timezone)
    scheduler.add_job(
        flush_notifications,
        "interval",
        minutes=1,
        args=[sessionmaker, feeds["status"].send_text],
        id="status_notifications",
        max_instances=1,
        coalesce=True,
    )
    if freelance_service.connectors:
        intervals = {n: (settings.config.email_watch.poll_interval_min if n == "email"
                         else settings.config.platforms[n].poll_interval_min) for n in freelance_service.connectors}
        interval = min(intervals.values())
        fl_job = scheduler.add_job(
            freelance_service.run_cycle, "interval", minutes=interval,
            next_run_time=datetime.now(UTC) + timedelta(seconds=45),
            id="freelance_cycle", max_instances=1, coalesce=True,
        )
        freelance_service.next_run_at = lambda: fl_job.next_run_time
        fh, fm = map(int, settings.config.daily_summary_time.split(":"))
        scheduler.add_job(freelance_service.send_daily_summary, "cron", hour=fh, minute=fm,
                          id="freelance_daily_summary", max_instances=1, coalesce=True)

    if tcfg.enabled:
        hh, mm = map(int, tcfg.daily_report_time.split(":"))
        scan_job = scheduler.add_job(
            tender_service.run_scan,
            "interval",
            minutes=tcfg.poll_interval_min,
            next_run_time=datetime.now(UTC) + timedelta(seconds=20),
            id="tenders_scan",
            max_instances=1,
            coalesce=True,
        )
        tender_service.next_scan_at = lambda: scan_job.next_run_time
        scheduler.add_job(tender_service.run_reminders, "interval", minutes=10, id="tenders_reminders",
                          max_instances=1, coalesce=True)
        scheduler.add_job(tender_service.send_daily_report, "cron", hour=hh, minute=mm,
                          id="tenders_daily_report", max_instances=1, coalesce=True)
    if settings.config.phone.enabled:
        from app.phone.submitter import PhoneSubmitter

        phone_submitter = PhoneSubmitter(settings, sessionmaker, gemini, notifier)
        scheduler.add_job(phone_submitter.run_queue, "interval", minutes=1,
                          next_run_time=datetime.now(UTC) + timedelta(seconds=60),
                          id="phone_submit", max_instances=1, coalesce=True)
    scheduler.start()

    web_runner = None
    wcfg = settings.config.web
    if wcfg.enabled:
        await backfill(sessionmaker, tender_service.limit_usd)
        from app.web import server as web_server

        try:
            web_runner = await web_server.start(
                web_server.build_app(settings, sessionmaker, tenders=tender_service if tcfg.enabled else None,
                                     freelance=freelance_service, notify=notifier.send_text),
                wcfg.host, wcfg.port,
            )
        except OSError:
            log.exception("web.start_failed")  # the bot keeps working without the site

    try:
        await bot.set_my_commands(COMMANDS)
        await notifier.send(
            "🚀 <b>Agent ishga tushdi.</b>\n🏛 Tender tekshiruvi ~20 soniyadan, 💼 frilans qidiruvi ~45 "
            "soniyadan keyin boshlanadi. /start",
            reply_markup=main_menu(),
        )
        log.info("app.started", extra={"db": db_url.split("://")[0]})
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.shutdown(wait=False)
        if web_runner is not None:
            await web_runner.cleanup()
        await tender_service.aclose()
        await freelance_service.aclose()
        await bot.session.close()
        await engine.dispose()


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
