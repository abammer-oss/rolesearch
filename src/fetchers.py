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


# ── Arbeitnow ─────────────────────────────────────────────────────────────────────────────────

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


# ── Remotive ──────────────────────────────────────────────────────────────────────────────────

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


# ── Jobicy ────────────────────────────────────────────────────────────────────────────────────

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


# ── Adzuna (optional — requires API key) ──────────────────────────────────────────────────────

def fetch_adzuna(prefs: JobPreferences) -> list[JobPosting]:
    app_id = (os.getenv("ADZUNA_APP_ID") or "").strip()
    app_key = (os.getenv("ADZUNA_APP_KEY") or "").strip()
    if not app_id or not app_key:
        return []

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


# ── The Muse (free, no auth — confirmed working from GitHub Actions) ────────────────

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
        for level in _MUSE_LEVELS[:2]:
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


# ── Shared RSS helper ────────────────────────────────────────────────────────────────────────────────

def _fetch_rss(url: str, source_name: str) -> list[dict]:
    """Fetch and parse an RSS feed using feedparser (handles malformed XML gracefully)."""
    import re
    import feedparser
    try:
        r = _SESSION.get(url, timeout=15)
        r.raise_for_status()
    except Exception as exc:
        logger.warning("%s RSS failed (%s): %s", source_name, url, exc)
        return []
    # Strip characters invalid in XML 1.0 (e.g. bare & or stray control chars)
    content = re.sub(rb'[^\x09\x0A\x0D\x20-\x7E\x80-\xFFFD]', b'', r.content)
    # Replace unescaped & that aren't part of an entity reference
    content = re.sub(rb'&(?!(?:[a-zA-Z][a-zA-Z0-9]*|#[0-9]+|#x[0-9a-fA-F]+);)', b'&amp;', content)
    feed = feedparser.parse(content)
    if feed.bozo and not feed.entries:
        logger.warning("%s RSS parse error (%s): %s", source_name, url, feed.bozo_exception)
        return []
    items = []
    for entry in feed.entries:
        title    = entry.get("title", "")
        link     = entry.get("link", "")
        desc     = _strip_html(entry.get("summary", entry.get("description", "")))
        pub_date = entry.get("published", entry.get("updated", ""))
        guid     = entry.get("id", link)
        items.append({
            "title":    title,
            "link":     link,
            "desc":     desc,
            "pub_date": pub_date,
            "guid":     guid or link,
        })
    return items


def _rss_to_posting(item: dict, source: str, default_location: str = "Remote") -> JobPosting | None:
    """Convert a parsed RSS item dict to a JobPosting. Returns None if unusable."""
    guid = item["guid"]
    if not guid:
        return None
    jid = _make_id(source, guid)

    raw_title = item["title"]
    company = ""
    for sep in (" - ", " | ", " — "):
        if sep in raw_title:
            parts = raw_title.split(sep, 1)
            raw_title, company = parts[0].strip(), parts[1].strip()
            break

    return JobPosting(
        id=jid,
        title=raw_title,
        company=company,
        location=default_location,
        url=item["link"],
        description=item["desc"],
        salary=None,
        job_type="full-time",
        source=source,
        posted_at=item["pub_date"],
        tags=[],
        remote="remote" in default_location.lower(),
    )


# ── Philanthropy News Digest (RSS — may be Cloudflare-blocked from cloud IPs) ─────────

def fetch_pnd(prefs: JobPreferences) -> list[JobPosting]:
    items = _fetch_rss("https://philanthropynewsdigest.org/jobs/rss", "pnd")
    jobs: list[JobPosting] = []
    seen: set[str] = set()
    for item in items:
        posting = _rss_to_posting(item, "pnd", default_location="USA")
        if posting and posting.id not in seen:
            seen.add(posting.id)
            jobs.append(posting)
        if len(jobs) >= prefs.max_jobs_per_source:
            break
    logger.info("pnd: fetched %d jobs", len(jobs))
    return jobs


# ── Chronicle of Philanthropy Jobs (RSS — may be Cloudflare-blocked from cloud IPs) ────

def fetch_chronicle(prefs: JobPreferences) -> list[JobPosting]:
    items = _fetch_rss("https://jobs.philanthropy.com/rss/jobs/", "chronicle")
    if not items:
        items = _fetch_rss("https://jobs.philanthropy.com/feed/rss2", "chronicle")
    jobs: list[JobPosting] = []
    seen: set[str] = set()
    for item in items:
        posting = _rss_to_posting(item, "chronicle", default_location="USA")
        if posting and posting.id not in seen:
            seen.add(posting.id)
            jobs.append(posting)
        if len(jobs) >= prefs.max_jobs_per_source:
            break
    logger.info("chronicle: fetched %d jobs", len(jobs))
    return jobs


# ── Indeed RSS (may be Cloudflare-blocked from cloud IPs) ──────────────────────────

_INDEED_SEARCHES = [
    ("chief development officer nonprofit", "remote"),
    ("vice president philanthropy", "remote"),
    ("executive director nonprofit", "Atlanta, GA"),
    ("director of development nonprofit", "remote"),
    ("chief impact officer", "remote"),
]


def fetch_indeed_rss(prefs: JobPreferences) -> list[JobPosting]:
    jobs: list[JobPosting] = []
    seen: set[str] = set()
    for query, location in _INDEED_SEARCHES:
        params = {"q": query, "l": location, "sort": "date", "fromage": "21"}
        items = _fetch_rss(
            f"https://www.indeed.com/rss?{'&'.join(f'{k}={v}' for k,v in params.items())}",
            "indeed",
        )
        for item in items:
            posting = _rss_to_posting(item, "indeed", default_location=location)
            if posting and posting.id not in seen:
                seen.add(posting.id)
                jobs.append(posting)
        time.sleep(0.5)
    logger.info("indeed: fetched %d jobs", len(jobs))
    return jobs[: prefs.max_jobs_per_source]


# ── Idealist.org (API endpoint unconfirmed) ──────────────────────────────────────────

_IDEALIST_TERMS = [
    "chief development officer",
    "chief impact officer",
    "vice president philanthropy",
    "director of development",
    "managing director",
    "executive director",
]

_IDEALIST_ENDPOINTS = [
    "https://www.idealist.org/api/listing/search",
    "https://www.idealist.org/api/v1/listing/search",
    "https://www.idealist.org/en/api/listing/search",
]


def fetch_idealist(prefs: JobPreferences) -> list[JobPosting]:
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
            org_name = org.get("name") or org.get("title") or "" if isinstance(org, dict) else org

            locations = j.get("locations") or j.get("location") or []
            if isinstance(locations, str):
                location = locations
            elif isinstance(locations, list) and locations:
                loc0 = locations[0]
                location = loc0.get("city") or loc0.get("name") or "Remote" if isinstance(loc0, dict) else str(loc0)
            else:
                location = "Remote"

            sal_min = j.get("salaryMin") or j.get("salary_min")
            sal_max = j.get("salaryMax") or j.get("salary_max")
            salary = None
            if sal_min and sal_max:
                try:
                    salary = f"${float(sal_min):,.0f}–${float(sal_max):,.0f}/yr"
                except (TypeError, ValueError):
                    pass

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
                job_type=j.get("jobType") or "full-time",
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


# ── USAJOBS (optional — requires USAJOBS_API_KEY + USAJOBS_USER_AGENT) ──────────────

def fetch_usajobs(prefs: JobPreferences) -> list[JobPosting]:
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
        "chief development officer",
    ]

    for kw in keywords[:3]:
        params = {"Keyword": kw, "ResultsPerPage": 25, "WhoMayApply": "public"}
        try:
            r = session.get("https://data.usajobs.gov/api/search", params=params, timeout=15)
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            logger.warning("USAJOBS request failed: %s", exc)
            continue

        for item in data.get("SearchResult", {}).get("SearchResultItems", []):
            pos = item.get("MatchedObjectDescriptor", {})
            raw_id = pos.get("PositionID", "")
            jid = _make_id("usajobs", raw_id)
            if jid in seen:
                continue
            seen.add(jid)

            pay = pos.get("PositionRemuneration", [{}])[0] if pos.get("PositionRemuneration") else {}
            sal_min = pay.get("MinimumRange")
            sal_max = pay.get("MaximumRange")
            sal_unit = pay.get("RateIntervalCode", "Per Year")
            salary = None
            if sal_min and sal_max:
                try:
                    salary = f"${float(sal_min):,.0f}–${float(sal_max):,.0f} {sal_unit}"
                except (TypeError, ValueError):
                    pass

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
                job_type="Full-Time",
                source="usajobs",
                posted_at=pos.get("PublicationStartDate"),
                tags=[j.get("Name", "") for j in pos.get("JobCategory", [])],
                remote=any("remote" in loc.get("LocationName", "").lower() for loc in locations),
            ))
        time.sleep(0.3)

    logger.info("usajobs: fetched %d jobs", len(jobs))
    return jobs[: prefs.max_jobs_per_source]


# ── JSearch on RapidAPI (aggregates Google Jobs / Indeed / LinkedIn) ────────────────

_JSEARCH_QUERIES = [
    "Chief Development Officer nonprofit",
    "Vice President Philanthropy",
    "Executive Director nonprofit social impact",
    "Director of Development nonprofit",
    "Chief Impact Officer",
    "Managing Director nonprofit",
    "VP Strategic Partnerships nonprofit",
    "Director Federal Grants nonprofit",
]


def fetch_jsearch(prefs: JobPreferences) -> list[JobPosting]:
    """
    JSearch on RapidAPI — aggregates Google Jobs, Indeed, LinkedIn, Glassdoor.
    Designed for cloud/server access; no IP blocking. Free tier: 200 req/day.
    Register at rapidapi.com and subscribe to JSearch (free), then add
    RAPIDAPI_KEY as a GitHub Actions secret.
    """
    api_key = os.getenv("RAPIDAPI_KEY")
    if not api_key:
        print("      jsearch: RAPIDAPI_KEY not set — skipping (add secret at github.com/abammer-oss/rolesearch/settings/secrets/actions)")
        return []
    print("      jsearch: RAPIDAPI_KEY found, querying JSearch API…")

    headers = {
        "X-RapidAPI-Key": api_key,
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
    }

    jobs: list[JobPosting] = []
    seen: set[str] = set()

    for query in _JSEARCH_QUERIES[:5]:  # 5 queries × ~10 results = ~50 jobs
        params = {
            "query": query,
            "page": "1",
            "num_pages": "1",
            "date_posted": "month",
        }
        try:
            r = requests.get(
                "https://jsearch.p.rapidapi.com/search",
                headers=headers,
                params=params,
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            print(f"      jsearch: query '{query}' failed — {exc}")
            continue

        for j in data.get("data", []):
            raw_id = j.get("job_id", "")
            if not raw_id:
                continue
            jid = _make_id("jsearch", raw_id)
            if jid in seen:
                continue
            seen.add(jid)

            city  = j.get("job_city", "") or ""
            state = j.get("job_state", "") or ""
            location = ", ".join(p for p in [city, state] if p) or "USA"

            sal_min    = j.get("job_min_salary")
            sal_max    = j.get("job_max_salary")
            sal_period = j.get("job_salary_period") or "YEAR"
            salary = None
            if sal_min and sal_max:
                try:
                    suffix = "/yr" if "YEAR" in sal_period.upper() else f"/{sal_period.lower()}"
                    salary = f"${float(sal_min):,.0f}–${float(sal_max):,.0f}{suffix}"
                except (TypeError, ValueError):
                    pass
            elif sal_min:
                try:
                    salary = f"${float(sal_min):,.0f}+/yr"
                except (TypeError, ValueError):
                    pass

            jobs.append(JobPosting(
                id=jid,
                title=j.get("job_title", ""),
                company=j.get("employer_name", ""),
                location=location,
                url=j.get("job_apply_link") or j.get("job_google_link") or "",
                description=j.get("job_description", ""),
                salary=salary,
                job_type=(j.get("job_employment_type") or "FULLTIME").replace("_", " ").title(),
                source="jsearch",
                posted_at=j.get("job_posted_at_datetime_utc"),
                tags=j.get("job_required_skills") or [],
                remote=bool(j.get("job_is_remote")),
            ))
        time.sleep(0.3)

    print(f"      jsearch: {len(jobs)} jobs fetched")
    return jobs[: prefs.max_jobs_per_source]


# ── Public entry point ────────────────────────────────────────────────────────────────────────────

def fetch_all_jobs(prefs: JobPreferences) -> list[JobPosting]:
    """Fetch from all configured sources and deduplicate by ID."""
    all_jobs: list[JobPosting] = []
    fetchers = (
        fetch_jsearch,    # Google Jobs aggregator via RapidAPI — cloud-safe, requires RAPIDAPI_KEY
        fetch_themuse,    # Fundraising & Development categories — confirmed working
        fetch_remotive,   # remote-first roles — confirmed working
        fetch_pnd,        # Philanthropy News Digest RSS — accessible from GH Actions
        fetch_indeed_rss, # Indeed RSS (may be Cloudflare-blocked)
        fetch_idealist,   # Idealist.org (API endpoint unconfirmed)
        fetch_usajobs,    # federal/government roles — requires USAJOBS_API_KEY
        fetch_adzuna,     # broad coverage — requires ADZUNA_APP_ID/KEY
        fetch_jobicy,     # remote jobs
        fetch_arbeitnow,  # broad job board
    )
    for fetcher in fetchers:
        try:
            batch = fetcher(prefs)
            all_jobs.extend(batch)
        except Exception as exc:
            print(f"      {fetcher.__name__}: ERROR — {exc}")

    seen: set[str] = set()
    deduped: list[JobPosting] = []
    for j in all_jobs:
        if j.id not in seen:
            seen.add(j.id)
            deduped.append(j)
    print(f"      Total unique jobs fetched: {len(deduped)}")
    return deduped
