"""Job board API clients — each returns a list[JobPosting]."""

from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Optional

import requests

from .models import JobPosting, JobPreferences

logger = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "rolesearch-agent/1.0"})


def _get(url: str, params: dict | None = None, timeout: int = 15) -> dict | list | None:
    try:
        r = _SESSION.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        logger.warning("GET %s failed: %s", url, exc)
        return None


def _make_id(source: str, raw_id: str) -> str:
    return hashlib.md5(f"{source}:{raw_id}".encode()).hexdigest()


# ── Arbeitnow ─────────────────────────────────────────────────────────────────

def fetch_arbeitnow(prefs: JobPreferences) -> list[JobPosting]:
    jobs: list[JobPosting] = []
    page = 1
    while len(jobs) < prefs.max_jobs_per_source:
        data = _get("https://www.arbeitnow.com/api/job-board-api", {"page": page})
        if not data or not data.get("data"):
            break
        for j in data["data"]:
            if len(jobs) >= prefs.max_jobs_per_source:
                break
            jobs.append(JobPosting(
                id=_make_id("arbeitnow", j.get("slug", j.get("url", ""))),
                title=j.get("title", ""),
                company=j.get("company_name", ""),
                location=j.get("location", "Remote"),
                url=j.get("url", ""),
                description=j.get("description", ""),
                salary=None,
                job_type=", ".join(j.get("job_types", [])) or None,
                source="arbeitnow",
                posted_at=str(j.get("created_at", "")),
                tags=j.get("tags", []),
                remote=j.get("remote", False),
            ))
        if len(data["data"]) < 15:
            break
        page += 1
        time.sleep(0.5)
    logger.info("arbeitnow: fetched %d jobs", len(jobs))
    return jobs


# ── Remotive ──────────────────────────────────────────────────────────────────

def fetch_remotive(prefs: JobPreferences) -> list[JobPosting]:
    jobs: list[JobPosting] = []
    for title in prefs.job_titles[:3]:
        data = _get(
            "https://remotive.com/api/remote-jobs",
            {"search": title, "limit": prefs.max_jobs_per_source},
        )
        if not data:
            continue
        for j in data.get("jobs", []):
            jobs.append(JobPosting(
                id=_make_id("remotive", str(j.get("id", j.get("url", "")))),
                title=j.get("title", ""),
                company=j.get("company_name", ""),
                location=j.get("candidate_required_location", "Remote"),
                url=j.get("url", ""),
                description=j.get("description", ""),
                salary=j.get("salary") or None,
                job_type=j.get("job_type") or None,
                source="remotive",
                posted_at=j.get("publication_date") or None,
                tags=j.get("tags", []),
                remote=True,
            ))
        time.sleep(0.5)
    seen: set[str] = set()
    deduped = []
    for j in jobs:
        if j.id not in seen:
            seen.add(j.id)
            deduped.append(j)
    logger.info("remotive: fetched %d jobs", len(deduped))
    return deduped[: prefs.max_jobs_per_source]


# ── Jobicy ────────────────────────────────────────────────────────────────────

def fetch_jobicy(prefs: JobPreferences) -> list[JobPosting]:
    tags = prefs.keywords[:2] if prefs.keywords else []
    params: dict = {"count": min(prefs.max_jobs_per_source, 50)}
    if tags:
        params["tag"] = tags[0]
    data = _get("https://jobicy.com/api/v2/remote-jobs", params)
    if not data:
        return []
    jobs = []
    for j in data.get("jobs", []):
        salary = None
        lo = j.get("annualSalaryMin")
        hi = j.get("annualSalaryMax")
        currency = j.get("salaryCurrency", "USD")
        if lo and hi:
            salary = f"{currency} {lo:,}–{hi:,}/yr"
        elif lo:
            salary = f"{currency} {lo:,}+/yr"
        jobs.append(JobPosting(
            id=_make_id("jobicy", str(j.get("id", j.get("url", "")))),
            title=j.get("jobTitle", ""),
            company=j.get("companyName", ""),
            location=j.get("jobGeo", "Remote"),
            url=j.get("url", ""),
            description=j.get("jobDescription", j.get("jobExcerpt", "")),
            salary=salary,
            job_type=j.get("jobType") or None,
            source="jobicy",
            posted_at=j.get("pubDate") or None,
            tags=[],
            remote=True,
        ))
    logger.info("jobicy: fetched %d jobs", len(jobs))
    return jobs


# ── Adzuna (optional — requires API key) ──────────────────────────────────────

def fetch_adzuna(prefs: JobPreferences) -> list[JobPosting]:
    app_id = os.getenv("ADZUNA_APP_ID")
    app_key = os.getenv("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        return []

    # Adzuna validates the Referer header against the domain registered with the app
    referrer = os.getenv("ADZUNA_REFERRER", "https://abammer.com")
    adzuna_session = requests.Session()
    adzuna_session.headers.update({
        "User-Agent": "rolesearch-agent/1.0",
        "Referer": referrer,
    })

    jobs: list[JobPosting] = []
    country = "us"
    for title in prefs.job_titles[:2]:
        params = {
            "app_id": app_id,
            "app_key": app_key,
            "what": title,
            "results_per_page": min(prefs.max_jobs_per_source, 50),
            "content-type": "application/json",
        }
        if prefs.salary_min:
            params["salary_min"] = prefs.salary_min
        for loc in prefs.locations[:1]:
            if loc.lower() != "remote":
                params["where"] = loc
        try:
            r = adzuna_session.get(
                f"https://api.adzuna.com/v1/api/jobs/{country}/search/1",
                params=params, timeout=15,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            logger.warning("Adzuna request failed: %s", exc)
            data = None
        if not data:
            continue
        for j in data.get("results", []):
            jobs.append(JobPosting(
                id=_make_id("adzuna", j.get("id", j.get("redirect_url", ""))),
                title=j.get("title", ""),
                company=j.get("company", {}).get("display_name", ""),
                location=j.get("location", {}).get("display_name", ""),
                url=j.get("redirect_url", ""),
                description=j.get("description", ""),
                salary=(
                    f"${j['salary_min']:,.0f}–${j['salary_max']:,.0f}/yr"
                    if j.get("salary_min") and j.get("salary_max")
                    else None
                ),
                job_type=j.get("contract_type") or None,
                source="adzuna",
                posted_at=j.get("created") or None,
                tags=j.get("category", {}).get("label", "").split(","),
                remote=False,
            ))
        time.sleep(0.3)

    seen: set[str] = set()
    deduped = [j for j in jobs if not (j.id in seen or seen.add(j.id))]  # type: ignore[func-returns-value]
    logger.info("adzuna: fetched %d jobs", len(deduped))
    return deduped[: prefs.max_jobs_per_source]


# ── The Muse (free, no auth — strong nonprofit/philanthropy coverage) ──────────

# Categories on The Muse that match Anthony's profile
_MUSE_CATEGORIES = [
    "Fundraising & Development",
    "Social Services",
    "Management & Operations",
    "Business Development",
    "Strategy",
    "Project & Program Management",
]

_MUSE_LEVELS = ["Senior Level", "Management", "Director", "Executive"]


def fetch_themuse(prefs: JobPreferences) -> list[JobPosting]:
    jobs: list[JobPosting] = []
    seen: set[str] = set()

    for category in _MUSE_CATEGORIES:
        for level in _MUSE_LEVELS[:2]:  # Senior + Management to keep request count low
            page = 0
            while len(jobs) < prefs.max_jobs_per_source:
                data = _get(
                    "https://www.themuse.com/api/public/jobs",
                    {"category": category, "level": level, "page": page, "descending": "true"},
                )
                if not data:
                    break
                results = data.get("results", [])
                if not results:
                    break
                for j in results:
                    jid = _make_id("themuse", str(j.get("id", "")))
                    if jid in seen:
                        continue
                    seen.add(jid)

                    locations = j.get("locations", [])
                    location = locations[0].get("name", "Remote") if locations else "Remote"
                    remote = not locations or any(
                        "remote" in loc.get("name", "").lower() for loc in locations
                    )

                    # The Muse returns HTML in contents — strip tags for description
                    raw_contents = j.get("contents", "") or ""
                    description = _strip_html(raw_contents) or j.get("name", "")

                    landing = j.get("refs", {}).get("landing_page", "")

                    jobs.append(JobPosting(
                        id=jid,
                        title=j.get("name", ""),
                        company=j.get("company", {}).get("name", ""),
                        location=location,
                        url=landing,
                        description=description,
                        salary=None,
                        job_type="full-time",
                        source="themuse",
                        posted_at=j.get("publication_date") or None,
                        tags=[c.get("name", "") for c in j.get("categories", [])],
                        remote=remote,
                    ))

                if page >= data.get("page_count", 1) - 1:
                    break
                page += 1
                time.sleep(0.3)

    logger.info("themuse: fetched %d jobs", len(jobs))
    return jobs[:prefs.max_jobs_per_source]


def _strip_html(html: str) -> str:
    """Remove HTML tags for clean plain-text description."""
    import re
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    return re.sub(r"\s+", " ", text).strip()


# ── Public entry point ────────────────────────────────────────────────────────

def fetch_all_jobs(prefs: JobPreferences) -> list[JobPosting]:
    """Fetch from all configured sources and deduplicate by ID."""
    all_jobs: list[JobPosting] = []
    for fetcher in (fetch_arbeitnow, fetch_remotive, fetch_jobicy, fetch_themuse, fetch_adzuna):
        try:
            all_jobs.extend(fetcher(prefs))
        except Exception as exc:
            logger.error("Fetcher %s crashed: %s", fetcher.__name__, exc)

    seen: set[str] = set()
    deduped: list[JobPosting] = []
    for j in all_jobs:
        if j.id not in seen:
            seen.add(j.id)
            deduped.append(j)
    logger.info("total unique jobs fetched: %d", len(deduped))
    return deduped
