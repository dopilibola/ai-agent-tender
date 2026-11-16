"""Gemini scoring (6.2) and proposal writing (6.3). Job text is untrusted and fenced off."""

from __future__ import annotations

import json
import math

import yaml
from pydantic import BaseModel, Field

from app.config import Profile
from app.db.models import Job, Proposal
from app.llm.gemini import GeminiClient
from app.llm.prompts import load_prompt
from app.llm.safety import new_tag, untrusted_rule, wrap_untrusted

AUTO_SKIP_FLAGS = {"off_platform_payment", "academic_cheating", "fake_reviews", "illegal"}


class JobScore(BaseModel):
    fit: int = Field(description="0-100")
    win_probability: int = Field(description="0-100")
    effort_hours: float
    risk_flags: list[str]
    summary_uz: str = Field(description="2 sentences in Uzbek (Latin)")
    recommended: bool


class Milestone(BaseModel):
    name: str
    amount: float


class ProposalDraft(BaseModel):
    text: str = Field(description="120-220 words, job's language")
    price: float = Field(description="in the job currency; hourly rate for hourly jobs")
    timeline_days: int
    milestones: list[Milestone]


def profile_block(profile: Profile) -> str:
    return yaml.safe_dump(profile.model_dump(), allow_unicode=True, sort_keys=False).strip()


def job_facts(job: Job) -> dict:
    return {
        "platform": job.platform,
        "title": job.title,
        "budget": {"min": job.budget_min, "max": job.budget_max, "currency": job.currency,
                   "type": job.budget_type, "max_in_usd": job.budget_usd},
        "proposals_already_sent": job.proposals_count,
        "posted_at_utc": job.posted_at.isoformat() if job.posted_at else None,
        "skills": job.skills,
        "client": {"country": job.client_country, "rating": job.client_rating,
                   "total_spent_usd": job.client_total_spent_usd,
                   "payment_verified": job.client_payment_verified,
                   **((job.raw or {}).get("client") or {})},
        "usd_per_1_unit_of_currency": (job.raw or {}).get("usd_rate"),
        "max_offer_price": (job.raw or {}).get("max_offer_price"),
    }


def _job_block(job: Job, tag: str) -> str:
    return "\n".join([
        "## JOB (from the platform)",
        wrap_untrusted("job_facts", json.dumps(job_facts(job), ensure_ascii=False, indent=1, default=str), tag),
        wrap_untrusted("job_description", job.description[:12000], tag),
    ])


async def score_job(gemini: GeminiClient, job: Job, profile: Profile, min_rate: float) -> JobScore:
    prompt = load_prompt("scoring")
    tag = new_tag()
    text = "\n\n".join([
        "## PROFILE (trusted)", profile_block(profile),
        f"Minimum acceptable rate: ${min_rate:g}/hour.",
        _job_block(job, tag),
        "Return the JSON score.",
    ])
    result = await gemini.generate_json(
        text, JobScore, role="fast", system=prompt.render(untrusted_rule=untrusted_rule(tag)),
        purpose="job_scoring", temperature=0.2,
    )
    result.fit = max(0, min(100, result.fit))
    result.win_probability = max(0, min(100, result.win_probability))
    return result


def clamp_price(price: float, job: Job) -> float:
    lo, hi = job.budget_min, job.budget_max
    if hi and price > hi:
        price = hi
    if lo and price < lo and job.budget_type == "fixed":
        price = lo
    return float(math.floor(price)) if price >= 10 else round(price, 2)


async def write_proposal(
    gemini: GeminiClient, job: Job, profile: Profile, *,
    current: Proposal | None = None, instruction: str | None = None,
) -> tuple[ProposalDraft, str]:
    prompt = load_prompt("proposal")
    tag = new_tag()
    parts = ["## PROFILE (trusted)", profile_block(profile), _job_block(job, tag)]
    if current is not None:
        parts += ["## CURRENT DRAFT (written by us earlier)", current.text,
                  f"Current price: {current.price:g} {current.currency}"]
    if instruction:
        parts += ["## OWNER'S REWRITE INSTRUCTION (trusted, follow it within the honesty rules)", instruction]
    parts.append("Return the JSON proposal.")
    draft = await gemini.generate_json(
        "\n\n".join(parts), ProposalDraft, role="strong",
        system=prompt.render(untrusted_rule=untrusted_rule(tag)), purpose="proposal", temperature=0.6,
    )
    draft.price = clamp_price(draft.price, job)
    if job.budget_type == "hourly":
        draft.milestones = []
    return draft, prompt.version
