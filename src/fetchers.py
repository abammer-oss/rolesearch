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


# ── Indeed RSS (broad nonprofit/executive coverage, no auth required) ────────

_INDEED_SEARCHES = [
    ("chief development officer nonprofit", "remote"),
    ("vice president philanthropy", "remote"),
    ("executive director nonprofit", "Atlanta, GA"),
    ("director of development nonprofit", "remote"),
    ("chief impact officer", "remote"),
    ("managing director nonprofit social impact", "remote"),
    ("director federal grants nonprofit", "remote"),
    ("VP strategic partnerships nonprofit", "remote"),
]


def fetch_indeed_rss(prefs: JobPreferences) -> list[JobPosting]:
    """Fetch from Indeed via public RSS — aggregates nonprofit executive listings."""
    import xml.etree.ElementTree as ET

    jobs: list[JobPosting] = []
    seen: set[str] = set()

    for query, location in _INDEED_SEARCHES[:5]:
        params = {"q": query, "l": location, "sort": "date", "fromage": "21"}
        try:
            r = _SESSION.get("https://www.indeed.com/rss", params=params, timeout=15)
            r.raise_for_status()
            root = ET.fromstring(r.content)
        except Exception as exc:
            logger.warning("Indeed RSS failed for '%s': %s", query, exc)
            continue

        channel = root.find("channel")
        if channel is None:
            continue

        for item in channel.findall("item"):
            title_el  = item.find("title")
            link_el   = item.find("link")
            desc_el   = item.find("description")
            pub_el    = item.find("pubDate")
            guid_el   = item.find("guid")

            raw_title = (title_el.text or "").strip() if title_el is not None else ""
            link      = (link_el.text or "").strip()  if link_el  is not None else ""
            desc      = _strip_html(desc_el.text or "") if desc_el is not None else ""
            pub_date  = (pub_el.text or "").strip()   if pub_el   is not None else ""
            guid      = (guid_el.text or link).strip() if guid_el is not None else link

            if not guid:
                continue
            jid = _make_id("indeed", guid)
            if jid in seen:
                continue
            seen.add(jid)

            # Indeed RSS title format: "Job Title - Company Name"
            if " - " in raw_title:
                parts    = raw_title.rsplit(" - ", 1)
                job_title = parts[0].strip()
                company   = parts[1].strip()
            else:
                job_title = raw_title
                company   = ""

            jobs.append(JobPosting(
                id=jid,
                title=job_title,
                company=company,
                location=location,
                url=link,
                description=desc,
                salary=None,
                job_type="full-time",
                source="indeed",
                posted_at=pub_date,
                tags=[],
                remote="remote" in location.lower(),
            ))
        time.sleep(0.5)

    logger.info("indeed: fetched %d jobs", len(jobs))
    return jobs[: prefs.max_jobs_per_source]


# ── Idealist.org (nonprofit / social-impact executive roles) ──────────────────

_IDEALIST_TERMS = [
    "chief development officer",
    "chief impact officer",
    "vice president philanthropy",
    "director of development",
    "managing director",
    "executive director",
    "director strategic partnerships",
    "managing partner nonprofit",
]


_IDEALIST_ENDPOINTS = [
    "https://www.idealist.org/api/listing/search",
    "https://www.idealist.org/api/v1/listing/search",
    "https://www.idealist.org/en/api/listing/search",
]


def fetch_idealist(prefs: JobPreferences) -> list[JobPosting]:
    """Fetch from Idealist.org — the premier nonprofit/social-impact job board."""
    jobs: list[JobPosting] = []
    seen: set[str] = set()

    for term in _IDEALIST_TERMS[:5]:
        params = {"type": "JOB", "q": term, "page": 0, "pageSize": 20}
        data = None
        for endpoint in _IDEALIST_ENDPOINTS:
            data = _get(endpoint, params)
            if data is not None:
                logger.info("idealist: connected via %s", endpoint)
                break
        if not data:
            logger.warning("idealist: all endpoints failed for term '%s'", term)
            continue

        logger.debug("idealist raw keys for '%s': %s", term, list(data.keys()) if isinstance(data, dict) else type(data).__name__)

        hits = data.get("hits") or data.get("results") or data.get("jobs") or []
        for j in hits:
            raw_id = str(j.get("id") or j.get("slug") or j.get("url") or "")
            if not raw_id:
                continue
            jid = _make_id("idealist", raw_id)
            if jid in seen:
                continue
            seen.add(jid)

            org = j.get("organization") or j.get("org") or {}
            if isinstance(org, str):
                org_name = org
            else:
                org_name = org.get("name") or org.get("title") or ""

            locations = j.get("locations") or j.get("location") or []
            if isinstance(locations, str):
                location = locations
            elif isinstance(locations, list) and locations:
                loc0 = locations[0]
                if isinstance(loc0, dict):
                    location = loc0.get("city") or loc0.get("name") or "Remote"
                else:
                    location = str(loc0)
            else:
                location = "Remote"

            sal_min = j.get("salaryMin") or j.get("salary_min") or j.get("compensationMin")
            sal_max = j.get("salaryMax") or j.get("salary_max") or j.get("compensationMax")
            salary = None
            if sal_min and sal_max:
                try:
                    salary = f"${float(sal_min):,.0f}–${float(sal_max):,.0f}/yr"
                except (TypeError, ValueError):
                    salary = f"{sal_min}–{sal_max}"
            elif sal_min:
                try:
                    salary = f"${float(sal_min):,.0f}+/yr"
                except (TypeError, ValueError):
                    salary = str(sal_min)

            raw_url = j.get("url") or j.get("applicationUrl") or ""
            if not raw_url and raw_id:
                raw_url = f"https://www.idealist.org/en/job/{raw_id}"

            desc = j.get("description") or j.get("body") or j.get("summary") or ""
            if "<" in desc:
                desc = _strip_html(desc)

            jobs.append(JobPosting(
                id=jid,
                title=j.get("name") or j.get("title") or "",
                company=org_name,
                location=location,
                url=raw_url,
                description=desc,
                salary=salary,
                job_type=j.get("jobType") or j.get("job_type") or "full-time",
                source="idealist",
                posted_at=j.get("publishedAt") or j.get("published_at") or j.get("updatedAt"),
                tags=j.get("skills") or j.get("tags") or [],
                remote=any(
                    "remote" in str(loc).lower()
                    for loc in (locations if isinstance(locations, list) else [locations])
                ),
            ))
        time.sleep(0.5)

    logger.info("idealist: fetched %d jobs", len(jobs))
    return jobs[: prefs.max_jobs_per_source]


# ── USAJOBS (federal / government roles — optional) ───────────────────────────

def fetch_usajobs(prefs: JobPreferences) -> list[JobPosting]:
    """
    Fetch from USAJOBS.gov — relevant for Anthony's government/federal-funding work.
    Requires USAJOBS_API_KEY and USAJOBS_USER_AGENT (your email) env vars.
    Register free at https://developer.usajobs.gov/
    """
    api_key = os.getenv("USAJOBS_API_KEY")
    user_agent = os.getenv("USAJOBS_USER_AGENT")
    if not api_key or not user_agent:
        return []

    session = requests.Session()
    session.headers.update({
        "Authorization-Key": api_key,
        "User-Agent": user_agent,
        "Host": "data.usajobs.gov",
    })

    jobs: list[JobPosting] = []
    seen: set[str] = set()
    keywords = [
        "nonprofit director",
        "community development director",
        "grants management director",
        "strategic partnerships director",
        "chief development officer",
    ]

    for kw in keywords[:3]:
        params = {
            "Keyword": kw,
            "ResultsPerPage": 25,
            "SalaryBucket": "130",  # $130K+ (nearest Adzuna-style bucket)
            "WhoMayApply": "public",
        }
        try:
            r = session.get(
                "https://data.usajobs.gov/api/search",
                params=params, timeout=15,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            logger.warning("USAJOBS request failed: %s", exc)
            continue

        items = (
            data.get("SearchResult", {})
                .get("SearchResultItems", [])
        )
        for item in items:
            pos = item.get("MatchedObjectDescriptor", {})
            raw_id = pos.get("PositionID", "")
            jid = _make_id("usajobs", raw_id)
            if jid in seen:
                continue
            seen.add(jid)

            # Salary
            pay = pos.get("PositionRemuneration", [{}])[0] if pos.get("PositionRemuneration") else {}
            sal_min = pay.get("MinimumRange")
            sal_max = pay.get("MaximumRange")
            sal_unit = pay.get("RateIntervalCode", "Per Year")
            salary = None
            if sal_min and sal_max:
                try:
                    salary = f"${float(sal_min):,.0f}–${float(sal_max):,.0f} {sal_unit}"
                except (TypeError, ValueError):
                    salary = f"{sal_min}–{sal_max} {sal_unit}"

            locations = pos.get("PositionLocation", [{}])
            loc_name = locations[0].get("LocationName", "USA") if locations else "USA"

            jobs.append(JobPosting(
                id=jid,
                title=pos.get("PositionTitle", ""),
                company=pos.get("OrganizationName", "US Government"),
                location=loc_name,
                url=pos.get("PositionURI", ""),
                description=pos.get("QualificationSummary", ""),
                salary=salary,
                job_type=pos.get("PositionScheduleType", [{}])[0].get("Name") if pos.get("PositionScheduleType") else "Full-Time",
                source="usajobs",
                posted_at=pos.get("PublicationStartDate"),
                tags=[j.get("Name", "") for j in pos.get("JobCategory", [])],
                remote=any(
                    "remote" in loc.get("LocationName", "").lower()
                    for loc in locations
                ),
            ))
        time.sleep(0.3)

    logger.info("usajobs: fetched %d jobs", len(jobs))
    return jobs[: prefs.max_jobs_per_source]


# ── Public entry point ────────────────────────────────────────────────────────

def fetch_all_jobs(prefs: JobPreferences) -> list[JobPosting]:
    """Fetch from all configured sources and deduplicate by ID."""
    all_jobs: list[JobPosting] = []
    for fetcher in (
        fetch_indeed_rss, # broad nonprofit/exec coverage via Indeed RSS — no auth
        fetch_idealist,   # nonprofit/social-impact dedicated board
        fetch_usajobs,    # federal/government roles (optional, requires API key)
        fetch_themuse,    # Fundraising & Development categories
        fetch_adzuna,     # broad coverage (requires API key)
        fetch_remotive,   # remote-first roles
        fetch_jobicy,     # remote jobs
        fetch_arbeitnow,  # broad job board
    ):
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
