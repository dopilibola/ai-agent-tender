"""Hard filters applied BEFORE any LLM call (free). Returns a reason in Uzbek, or None if the job passes."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.config import AppConfig, PlatformConfig, Profile
from app.connectors.base import JobListing
from app.tenders.timeutil import as_utc


def hard_filter(
    job: JobListing, cfg: AppConfig, profile: Profile, platform: PlatformConfig | None, now: datetime
) -> str | None:
    f = cfg.filters
    if job.posted_at and as_utc(job.posted_at) < now - timedelta(hours=f.max_job_age_hours):
        return f"{f.max_job_age_hours} soatdan eski"
    if job.proposals_count is not None and job.proposals_count > f.max_proposals:
        return f"takliflar ko'p ({job.proposals_count} > {f.max_proposals})"
    if job.language and job.language.lower() not in {x.lower() for x in f.languages}:
        return f"til: {job.language}"
    if job.budget_type == "hourly":
        if job.budget_max and job.usd_rate:
            rate = job.budget_max * job.usd_rate
            if rate < profile.min_rate_usd_per_hour:
                return f"soatbay stavka past (${rate:.0f}/soat)"
        return None
    usd = job.budget_usd
    if usd is not None:
        if usd < f.budget_min_usd:
            return f"byudjet kichik (${usd:.0f})"
        if usd > f.budget_max_usd:
            return f"byudjet katta (${usd:.0f})"
        if platform and platform.max_project_budget_usd and usd > platform.max_project_budget_usd:
            return f"tarif limiti: ${platform.max_project_budget_usd:.0f} gacha"
    return None
