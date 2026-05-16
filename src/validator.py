"""Validate job posting freshness (age ≤ 21 days) and URL liveness."""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.parse import urlparse

import requests

from .models import JobPosting

logger = logging.getLogger(__name__)

MAX_AGE_DAYS = 21
_TIMEOUT = 8
_MAX_WORKERS = 12

_DEAD_RE = re.compile(
    r"(job\s+no\s+longer|position\s+has\s+been\s+filled|this\s+job\s+is\s+no\s+longer"
    r"|job\s+has\s+expired|no\s+longer\s+available|position\s+(is\s+)?closed"
    r"|this\s+position\s+has\s+been\s+filled|job\s+expired|applications?\s+closed"
    r"|listing\s+has\s+expired|role\s+has\s+been\s+filled|vacancy\s+closed"
    r"|this\s+role\s+is\s+no\s+longer|not\s+accepting\s+applications)",
    re.IGNORECASE,
)

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "Mozilla/5.0 (compatible; rolesearch-bot/1.0)"})


# ── Date parsing ──────────────────────────────────────────────────────────────

def _parse_date(posted_at: Optional[str]) -> Optional[datetime]:
    if not posted_at:
        return None

    # Unix timestamp (integer stored as string)
    try:
        ts = float(posted_at)
        if 1_000_000_000 < ts < 9_999_999_999:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        pass

    # ISO 8601 variants
    cleaned = posted_at.strip().rstrip("Z")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    # RFC 2822 — "Wed, 14 May 2026 12:00:00 +0000"
    try:
        return parsedate_to_datetime(posted_at)
    except Exception:
        pass

    return None


def days_old(job: JobPosting) -> Optional[int]:
    dt = _parse_date(job.posted_at)
    if dt is None:
        return None
    return (datetime.now(tz=timezone.utc) - dt).days


def within_age_limit(job: JobPosting) -> bool:
    age = days_old(job)
    return age is None or age <= MAX_AGE_DAYS


# ── URL liveness check ────────────────────────────────────────────────────────

def _is_homepage_redirect(original_url: str, final_url: str) -> bool:
    """Detect redirect to root or careers-listing page (job expired)."""
    try:
        orig = urlparse(original_url)
        dest = urlparse(final_url)
        if orig.netloc != dest.netloc:
            return False
        orig_depth = len([p for p in orig.path.split("/") if p])
        dest_depth = len([p for p in dest.path.split("/") if p])
        return dest_depth == 0 or (orig_depth >= 2 and dest_depth <= 1)
    except Exception:
        return False


def check_url_live(url: str) -> bool:
    """Return True if the URL is a live, active job posting."""
    if not url or not url.startswith("http"):
        return True  # Can't check; assume live

    try:
        # HEAD first — fast, avoids downloading body
        head = _SESSION.head(url, timeout=_TIMEOUT, allow_redirects=True)
        if head.status_code in (404, 410):
            return False
        if head.status_code == 405:
            # Server doesn't allow HEAD; fall through to GET
            pass
        elif head.status_code >= 400:
            return False
        elif _is_homepage_redirect(url, head.url):
            return False
        else:
            # HEAD succeeded — do a lightweight GET only to read body patterns
            pass

        # GET for body content analysis
        resp = _SESSION.get(url, timeout=_TIMEOUT, allow_redirects=True)
        if resp.status_code in (404, 410):
            return False
        if resp.status_code >= 400:
            return False
        if _is_homepage_redirect(url, resp.url):
            return False
        if _DEAD_RE.search(resp.text[:6000]):
            return False
        return True

    except requests.exceptions.Timeout:
        logger.debug("Liveness timeout: %s", url)
        return True  # Slow server — assume live
    except Exception as exc:
        logger.debug("Liveness error for %s: %s", url, exc)
        return True  # Network error — assume live


# ── Public entry point ────────────────────────────────────────────────────────

def filter_valid_jobs(
    jobs: list[JobPosting],
    check_liveness: bool = True,
) -> tuple[list[JobPosting], dict]:
    """
    Filter jobs by age (≤21 days) then by URL liveness.
    Returns (valid_jobs, stats_dict).
    """
    stats = {"total": len(jobs), "too_old": 0, "inactive": 0, "valid": 0}

    age_ok = []
    for job in jobs:
        if within_age_limit(job):
            age_ok.append(job)
        else:
            stats["too_old"] += 1
            age = days_old(job)
            logger.debug("Dropped (age %s days): %s @ %s", age, job.title, job.company)

    if not check_liveness:
        stats["valid"] = len(age_ok)
        return age_ok, stats

    live_jobs: list[JobPosting] = []
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = {pool.submit(check_url_live, job.url): job for job in age_ok}
        for future in as_completed(futures):
            job = futures[future]
            try:
                is_live = future.result()
            except Exception:
                is_live = True
            if is_live:
                live_jobs.append(job)
            else:
                stats["inactive"] += 1
                logger.debug("Dropped (inactive URL): %s @ %s", job.title, job.company)

    stats["valid"] = len(live_jobs)
    logger.info(
        "Validator: %d total → %d too old, %d inactive → %d valid",
        stats["total"], stats["too_old"], stats["inactive"], stats["valid"],
    )
    return live_jobs, stats
