"""Manual URL ingestion — fetch, parse, and return JobPosting objects."""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

import requests

from .models import JobPosting

logger = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
})

_PARSE_TOOL = {
    "name": "parse_job_description",
    "description": "Extract structured fields from raw job description text.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Exact job title as listed.",
            },
            "company": {
                "type": "string",
                "description": "Company or organization name.",
            },
            "location": {
                "type": "string",
                "description": "Location string (city, state, or Remote).",
            },
            "remote": {
                "type": "boolean",
                "description": "True if the role is remote or remote-eligible.",
            },
            "salary": {
                "type": "string",
                "description": "Compensation range or amount if listed, else empty string.",
            },
            "job_type": {
                "type": "string",
                "description": "Employment type: full-time, part-time, contract, etc.",
            },
            "posted_at": {
                "type": "string",
                "description": "Posting date if visible, else empty string.",
            },
            "seniority": {
                "type": "string",
                "description": "Seniority level: entry, mid, senior, director, VP, C-suite.",
            },
            "description": {
                "type": "string",
                "description": (
                    "Full cleaned job description body including responsibilities, "
                    "required qualifications, preferred qualifications, and any "
                    "compensation/benefits details. Preserve all relevant content."
                ),
            },
        },
        "required": ["title", "company", "location", "description"],
    },
}


def _fetch_url(url: str) -> tuple[str | None, str | None]:
    """Fetch a URL and return (cleaned_text, error_message)."""
    try:
        r = _SESSION.get(url, timeout=20, allow_redirects=True)
        r.raise_for_status()
    except Exception as exc:
        return None, str(exc)

    content_type = r.headers.get("content-type", "")
    if "text/html" not in content_type and "text/plain" not in content_type:
        return None, f"Unexpected content type: {content_type}"

    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
    except ImportError:
        # Fallback: strip HTML tags with regex if bs4 not installed
        text = re.sub(r"<[^>]+>", " ", r.text)
        text = re.sub(r"&nbsp;", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

    # Collapse excessive blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:10000], None


def _parse_with_claude(raw_text: str, source_url: str, client: Any) -> dict | None:
    """Use Claude tool-use to extract structured JD fields from raw text."""
    prompt = f"""\
Extract all job posting fields from the text below. If a field isn't present, use an empty string or false.

Source URL: {source_url}

---
{raw_text}
---

Call parse_job_description with the extracted fields.
"""
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",  # fast + cheap for extraction
            max_tokens=2000,
            tools=[_PARSE_TOOL],
            tool_choice={"type": "any"},
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        logger.error("Claude extraction failed for %s: %s", source_url, exc)
        return None

    for block in response.content:
        if block.type == "tool_use" and block.name == "parse_job_description":
            return block.input
    return None


def _make_id(url: str) -> str:
    return hashlib.md5(f"manual:{url}".encode()).hexdigest()


def ingest_url(
    url: str,
    client: Any,
    pasted_jd: str | None = None,
    company_notes: str = "",
) -> tuple[JobPosting | None, str | None]:
    """
    Fetch and parse a single job URL into a JobPosting.
    Falls back to pasted_jd if URL fetch fails.
    Returns (JobPosting, error_message). One of the two will be None.
    """
    raw_text, fetch_err = _fetch_url(url) if url else (None, "No URL provided")

    if not raw_text:
        if pasted_jd:
            raw_text = pasted_jd
            logger.info("URL fetch failed (%s) — using pasted JD text", fetch_err)
        else:
            return None, f"Fetch failed: {fetch_err}"

    if company_notes:
        raw_text = f"[Reviewer notes: {company_notes}]\n\n{raw_text}"

    fields = _parse_with_claude(raw_text, url or "manual-paste", client)
    if not fields:
        return None, "Claude could not extract job fields from the content"

    title = (fields.get("title") or "").strip()
    company = (fields.get("company") or "").strip()
    if not title or not company:
        return None, f"Could not extract title/company from {url or 'pasted text'}"

    return JobPosting(
        id=_make_id(url or raw_text[:200]),
        title=title,
        company=company,
        location=(fields.get("location") or "Unknown").strip(),
        url=url or "",
        description=(fields.get("description") or raw_text[:3000]).strip(),
        salary=(fields.get("salary") or None) or None,
        job_type=(fields.get("job_type") or "full-time").strip(),
        source="manual",
        posted_at=(fields.get("posted_at") or None) or None,
        tags=[],
        remote=bool(fields.get("remote", False)),
    ), None


def ingest_batch(
    urls: list[str],
    client: Any,
    pasted_jds: dict[str, str] | None = None,
    company_notes: str = "",
) -> tuple[list[JobPosting], list[dict]]:
    """
    Ingest a list of URLs. Returns (successful_postings, failures).
    failures is a list of {"url": ..., "error": ...} dicts.
    """
    pasted_jds = pasted_jds or {}
    postings: list[JobPosting] = []
    failures: list[dict] = []

    for url in urls:
        pasted = pasted_jds.get(url)
        job, err = ingest_url(url, client, pasted_jd=pasted, company_notes=company_notes)
        if job:
            postings.append(job)
            logger.info("Ingested: %s @ %s", job.title, job.company)
        else:
            failures.append({"url": url, "error": err})
            logger.warning("Ingest failed for %s: %s", url, err)

    return postings, failures
