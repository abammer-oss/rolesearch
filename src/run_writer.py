"""Write ingest-run output: /runs/ directory, dashboard.md, application-tracker.csv."""

from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import GeneratedDocuments, JobPosting, MatchResult

logger = logging.getLogger(__name__)

RUNS_DIR = Path(__file__).parent.parent / "runs"
TRACKER_CSV = Path(__file__).parent.parent / "application-tracker.csv"

_TRACKER_FIELDS = [
    "date", "source_url", "company", "role", "location", "salary",
    "fit_score", "comp_score", "roi_score", "composite_score", "tier",
    "recommendation", "top_fit_reason", "top_gap", "action", "notes",
]

_TIER_FROM_SCORE = {
    (85, 101): "high-fit",
    (70,  85): "medium-fit",
    (50,  70): "low-fit",
    (0,   50): "skip",
}

_TIER_LABEL = {
    "high-fit":   "🚀 HIGH FIT",
    "medium-fit": "⚡ MEDIUM FIT",
    "low-fit":    "🔍 LOW FIT",
    "skip":       "✗ SKIP",
}

_TIER_THRESHOLD = {
    "high-fit":   85,
    "medium-fit": 70,
    "low-fit":    50,
    "skip":       0,
}


def _tier(score: int, priority_override: str | None = None) -> str:
    if priority_override == "high":
        return "high-fit"
    for (lo, hi), label in _TIER_FROM_SCORE.items():
        if lo <= score < hi:
            return label
    return "skip"


def _safe_name(s: str) -> str:
    return "".join(c if c.isalnum() or c in " _-" else "_" for c in s).strip().replace(" ", "-")[:40]


def make_run_dir(label: str = "") -> Path:
    ts = datetime.now().strftime("%Y-%m-%d-%H%M")
    name = f"{ts}-{label}" if label else ts
    run_dir = RUNS_DIR / name
    run_dir.mkdir(parents=True, exist_ok=True)
    for subdir in ("high-fit", "medium-fit", "low-fit", "skip"):
        (run_dir / subdir).mkdir(exist_ok=True)
    return run_dir


def write_job_artifacts(
    run_dir: Path,
    job: JobPosting,
    match: MatchResult,
    docs: GeneratedDocuments | None,
    priority_override: str | None = None,
) -> Path:
    """Write per-job files. Returns the job folder path."""
    tier = _tier(match.score, priority_override)
    folder_name = f"{_safe_name(job.company)}-{_safe_name(job.title)}"
    job_dir = run_dir / tier / folder_name
    job_dir.mkdir(parents=True, exist_ok=True)

    # jd-parsed.md
    jd_lines = [
        f"# {job.title} @ {job.company}",
        "",
        f"**Location:** {job.location}",
        f"**Salary:** {job.salary or 'Not listed'}",
        f"**Type:** {job.job_type or 'N/A'}",
        f"**Posted:** {job.posted_at or 'N/A'}",
        f"**Source URL:** {job.url or 'manual paste'}",
        "",
        "## Job Description",
        "",
        job.description,
    ]
    (job_dir / "jd-parsed.md").write_text("\n".join(jd_lines), encoding="utf-8")

    # score-rationale.md
    key_matches = match.key_matches if isinstance(match.key_matches, list) else []
    gaps = match.gaps if isinstance(match.gaps, list) else []
    resume_angles = match.resume_angles if isinstance(match.resume_angles, list) else []
    risks = match.risks if isinstance(match.risks, list) else []

    rationale_lines = [
        f"# Score Rationale — {job.title} @ {job.company}",
        "",
        f"**Tier:** {_TIER_LABEL[tier]}",
        f"**Composite Score:** {match.score} / 100",
        f"**Fit:** {match.fit_score}  |  **Competitiveness:** {match.competitiveness_score}  |  **ROI:** {match.roi_score}",
        f"**Recommendation:** {match.recommendation}",
        "",
        "## Executive Summary",
        match.executive_summary or "_Not available_",
        "",
        "## Top Fit Reasons",
        *(([f"- {m}" for m in key_matches]) or ["- None identified"]),
        "",
        "## Gaps & Risks",
        *(([f"- {r}" for r in (risks or gaps)]) or ["- None identified"]),
        "",
        "## Resume Angles",
        *(([f"{i+1}. {a}" for i, a in enumerate(resume_angles)]) or ["_Not available_"]),
        "",
        "## Full Reasoning",
        match.reasoning or "_Not available_",
    ]
    if match.outreach_strategy:
        rationale_lines += ["", "## Outreach Strategy", match.outreach_strategy]
    (job_dir / "score-rationale.md").write_text("\n".join(rationale_lines), encoding="utf-8")

    # resume.md and cover-letter.md (if docs generated)
    if docs:
        (job_dir / "resume.md").write_text(docs.tailored_resume, encoding="utf-8")
        (job_dir / "cover-letter.md").write_text(docs.cover_letter, encoding="utf-8")

    return job_dir


def write_dashboard(
    run_dir: Path,
    jobs: list[JobPosting],
    matches: list[MatchResult],
    failures: list[dict],
    dry_run: bool = False,
    priority_override: str | None = None,
) -> Path:
    """Write dashboard.md summarising all processed jobs."""
    match_by_id = {m.job_id: m for m in matches}
    by_tier: dict[str, list[tuple[JobPosting, MatchResult]]] = {
        "high-fit": [], "medium-fit": [], "low-fit": [], "skip": [],
    }
    for job in jobs:
        m = match_by_id.get(job.id)
        if not m:
            continue
        t = _tier(m.score, priority_override)
        by_tier[t].append((job, m))

    tier_counts = {t: len(v) for t, v in by_tier.items()}
    total = sum(tier_counts.values())
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "# Job Application Run Dashboard",
        "",
        f"**Run:** {run_dir.name}  |  **Generated:** {now}  |  {'DRY RUN — no documents generated' if dry_run else 'Documents generated'}",
        "",
        "## Summary",
        "",
        f"| Tier | Count |",
        f"|------|-------|",
        *[f"| {_TIER_LABEL[t]} | {tier_counts[t]} |" for t in ("high-fit", "medium-fit", "low-fit", "skip")],
        f"| **Total** | **{total}** |",
    ]

    if failures:
        lines += [
            "",
            "## ⚠ Extraction Failures",
            "",
            "| URL | Error |",
            "|-----|-------|",
            *[f"| {f['url']} | {f['error']} |" for f in failures],
        ]

    for tier in ("high-fit", "medium-fit", "low-fit", "skip"):
        jobs_in_tier = by_tier[tier]
        if not jobs_in_tier:
            continue
        lines += [
            "",
            f"## {_TIER_LABEL[tier]} ({len(jobs_in_tier)})",
            "",
            "| Score | Fit | Comp | ROI | Company | Role | Location | Salary | Top Fit Reason | Top Gap | Resume | Cover Letter |",
            "|-------|-----|------|-----|---------|------|----------|--------|----------------|---------|--------|--------------|",
        ]
        for job, m in sorted(jobs_in_tier, key=lambda x: -x[1].score):
            key_matches = m.key_matches if isinstance(m.key_matches, list) else []
            gaps = (m.risks or m.gaps) if isinstance(m.risks, list) else []
            top_fit = key_matches[0] if key_matches else "—"
            top_gap = gaps[0] if gaps else "—"
            folder_name = f"{_safe_name(job.company)}-{_safe_name(job.title)}"
            if dry_run:
                resume_link = "_dry run_"
                cl_link = "_dry run_"
            else:
                resume_link = f"[resume](./{tier}/{folder_name}/resume.md)"
                cl_link = f"[cover letter](./{tier}/{folder_name}/cover-letter.md)"

            lines.append(
                f"| {m.score} | {m.fit_score} | {m.competitiveness_score} | {m.roi_score} "
                f"| {job.company} | [{job.title}]({job.url or '#'}) "
                f"| {job.location} | {job.salary or '—'} "
                f"| {top_fit[:60]} | {top_gap[:60]} "
                f"| {resume_link} | {cl_link} |"
            )

    path = run_dir / "dashboard.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def append_tracker(
    jobs: list[JobPosting],
    matches: list[MatchResult],
    priority_override: str | None = None,
    action: str = "pending",
) -> None:
    """Append processed jobs to application-tracker.csv."""
    write_header = not TRACKER_CSV.exists()
    match_by_id = {m.job_id: m for m in matches}
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    with TRACKER_CSV.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_TRACKER_FIELDS)
        if write_header:
            writer.writeheader()
        for job in jobs:
            m = match_by_id.get(job.id)
            if not m:
                continue
            key_matches = m.key_matches if isinstance(m.key_matches, list) else []
            gaps = (m.risks or m.gaps) if isinstance(m.risks, list) else []
            writer.writerow({
                "date": now,
                "source_url": job.url,
                "company": job.company,
                "role": job.title,
                "location": job.location,
                "salary": job.salary or "",
                "fit_score": m.fit_score,
                "comp_score": m.competitiveness_score,
                "roi_score": m.roi_score,
                "composite_score": m.score,
                "tier": _tier(m.score, priority_override),
                "recommendation": m.recommendation,
                "top_fit_reason": key_matches[0] if key_matches else "",
                "top_gap": gaps[0] if gaps else "",
                "action": action,
                "notes": "",
            })
    logger.info("Appended %d rows to %s", len(jobs), TRACKER_CSV)
