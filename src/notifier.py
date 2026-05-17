"""Create GitHub Issues for new top job matches."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

_GH_API = "https://api.github.com"

_TIER_LABELS = {
    1: ("tier-1-apply-now",      "00aa00", "Priority 1 — Apply Now (composite ≥85)"),
    2: ("tier-2-apply-outreach", "ddaa00", "Priority 2 — Apply Selectively or Outreach First (75–84)"),
    3: ("tier-3-track",          "0075ca", "Priority 3 — Track Only (65–74)"),
}
_TIER_ICONS = {1: "🚀", 2: "⚡", 3: "🔍"}

_REC_DISPLAY = {
    "apply_now":          "🚀 APPLY NOW",
    "apply_selectively":  "⚡ APPLY SELECTIVELY",
    "outreach_first":     "📨 OUTREACH FIRST",
    "track_only":         "👁 TRACK ONLY",
    "skip":               "✗ SKIP",
    "apply":              "✅ GO",
    "maybe":              "⚠️ MAYBE",
}


def _headers() -> dict:
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise EnvironmentError("GITHUB_TOKEN not set — cannot create GitHub Issues")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _repo() -> str:
    repo = os.getenv("GITHUB_REPO")
    if not repo:
        raise EnvironmentError("GITHUB_REPO not set (expected format: owner/repo)")
    return repo


# ── Label setup ───────────────────────────────────────────────────────────────

def ensure_labels(repo: str) -> None:
    all_labels = [
        ("job-match", "1d76db", "AI-matched job from rolesearch"),
        *[(name, color, desc) for name, color, desc in _TIER_LABELS.values()],
    ]
    for name, color, desc in all_labels:
        try:
            requests.post(
                f"{_GH_API}/repos/{repo}/labels",
                headers=_headers(),
                json={"name": name, "color": color, "description": desc},
                timeout=10,
            )
        except Exception as exc:
            logger.debug("Label '%s' may already exist: %s", name, exc)


# ── Deduplication ─────────────────────────────────────────────────────────────

def issue_already_exists(repo: str, job_id: str) -> bool:
    try:
        resp = requests.get(
            f"{_GH_API}/repos/{repo}/issues",
            headers=_headers(),
            params={"state": "open", "labels": "job-match", "per_page": 100},
            timeout=10,
        )
        if resp.status_code != 200:
            return False
        for issue in resp.json():
            if job_id in issue.get("body", ""):
                return True
    except Exception as exc:
        logger.warning("Could not check existing issues: %s", exc)
    return False


# ── Issue body ────────────────────────────────────────────────────────────────

def _parse_list(value) -> list:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return []
    return value or []


def _build_body(match: dict, repo: str) -> str:
    rank     = match.get("priority_rank") or 3
    rec      = match.get("recommendation", "maybe")
    score    = match["score"]
    fit      = match.get("fit_score") or 0
    comp     = match.get("competitiveness_score") or 0
    roi      = match.get("roi_score") or 0
    icon     = _TIER_ICONS.get(rank, "🔍")
    decision = _REC_DISPLAY.get(rec, rec.upper())

    key_matches   = _parse_list(match["key_matches"])
    gaps          = _parse_list(match["gaps"])
    resume_angles = _parse_list(match.get("resume_angles"))
    risks         = _parse_list(match.get("risks"))
    outreach      = match.get("outreach_strategy") or ""
    exec_summary  = match.get("executive_summary") or ""

    matches_md = "\n".join(f"- {m}" for m in key_matches) or "- —"
    gaps_md    = "\n".join(f"- {g}" for g in gaps)        or "- None identified"

    age_line = ""
    if match.get("posted_at"):
        try:
            from src.validator import _parse_date
            dt = _parse_date(match["posted_at"])
            if dt:
                d = (datetime.now(tz=timezone.utc) - dt).days
                age_line = f" · Posted **{d} days ago**"
        except Exception:
            pass

    salary_md = f"**{match['salary']}**" if match.get("salary") else "_Not listed_"
    source    = match.get("source", "").title()
    job_id    = match["job_id"]

    tier_name = {1: "APPLY NOW", 2: "APPLY / OUTREACH", 3: "TRACK"}.get(rank, "")

    # Three-score table (hide sub-scores if this is a legacy single-score match)
    if fit or comp or roi:
        score_table = f"""\
| Score | Value | Weight |
|---|---|---|
| **Fit** | {fit} / 100 | 40% — resume + lane alignment |
| **Competitiveness** | {comp} / 100 | 35% — screening probability |
| **Application ROI** | {roi} / 100 | 25% — effort vs. upside |
| **Composite** | **{score} / 100** | — |
"""
    else:
        score_table = f"**Score:** {score} / 100\n"

    resume_angles_md = (
        "\n".join(f"{i+1}. {a}" for i, a in enumerate(resume_angles))
        if resume_angles else "_Not available_"
    )
    risks_md = (
        "\n".join(f"- ⚠️ {r}" for r in risks)
        if risks else (
            "\n".join(f"- {g}" for g in gaps) or "- None identified"
        )
    )
    outreach_section = (
        f"\n### Outreach Strategy\n{outreach}\n"
        if outreach and rec in ("outreach_first", "apply_selectively")
        else ""
    )

    return f"""\
## {icon} Tier {rank} — {tier_name}

| Field | Detail |
|---|---|
| **Decision** | {decision} |
| **Location** | {match['location']}{age_line} |
| **Salary** | {salary_md} |
| **Source** | {source} |

---

### Scores

{score_table}

---

### Executive Summary
{exec_summary or '_Not available_'}

---

### Lead With — Top 3 Resume Angles
{resume_angles_md}

### Key Matches
{matches_md}

### Risks & Gaps
{risks_md}
{outreach_section}
---

### Next Steps

**Apply:** {match.get('url') or '_URL not available_'}

**Generate tailored resume + cover letter locally:**
```bash
python main.py generate {job_id}
```

**Job ID:** `{job_id}`

---
<sub>Generated by [rolesearch](https://github.com/{repo}) · AI-powered job search agent</sub>
"""


# ── Public entry point ────────────────────────────────────────────────────────

def create_issue(match: dict) -> str | None:
    repo  = _repo()
    rank  = match.get("priority_rank") or 3
    score = match["score"]
    icon  = _TIER_ICONS.get(rank, "🔍")
    rec   = match.get("recommendation", "maybe")
    decision = _REC_DISPLAY.get(rec, rec.upper())

    title = f"{icon} [{decision}] {match['title']} @ {match['company']} — Score {score}"
    body  = _build_body(match, repo)
    labels = ["job-match", _TIER_LABELS[min(rank, 3)][0]]

    try:
        resp = requests.post(
            f"{_GH_API}/repos/{repo}/issues",
            headers=_headers(),
            json={"title": title, "body": body, "labels": labels},
            timeout=15,
        )
        if resp.status_code == 201:
            url = resp.json().get("html_url", "")
            logger.info("Issue created: %s", url)
            return url
        logger.error("Issue creation failed %s: %s", resp.status_code, resp.text[:300])
    except Exception as exc:
        logger.error("Issue creation exception: %s", exc)
    return None


def notify_new_matches(matches: list[dict]) -> int:
    if not matches:
        return 0

    repo = _repo()
    try:
        ensure_labels(repo)
    except Exception as exc:
        logger.warning("Could not ensure labels: %s", exc)

    created = 0
    for match in matches:
        job_id = match["job_id"]
        if issue_already_exists(repo, job_id):
            logger.info("Issue already exists for %s — skipping", job_id[:8])
            continue
        url = create_issue(match)
        if url:
            created += 1
            print(f"  Issue opened: {url}")
    return created
