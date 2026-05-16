"""Main orchestrator — ties together fetching, matching, and generation."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import anthropic
import yaml

from src.fetchers import fetch_all_jobs
from src.generator import generate_documents
from src.matcher import match_jobs
from src.models import JobPosting, JobPreferences, Resume
from src.storage import (
    documents_exist,
    get_job,
    get_unmatched_jobs,
    init_db,
    jobs_needing_liveness_recheck,
    mark_job_liveness,
    save_documents,
    save_jobs,
    save_match,
)
from src.validator import check_url_live, filter_valid_jobs

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent / "config"


def load_resume() -> Resume:
    with open(CONFIG_DIR / "resume.yaml", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return Resume(**data)


def load_preferences() -> JobPreferences:
    with open(CONFIG_DIR / "preferences.yaml", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return JobPreferences(**data)


def _make_client() -> anthropic.Anthropic:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key."
        )
    return anthropic.Anthropic(api_key=api_key)


class RoleSearchAgent:
    def __init__(self) -> None:
        init_db()
        self.resume = load_resume()
        self.prefs = load_preferences()
        self.client = _make_client()

    def refresh(self, console=None) -> list[dict]:
        """Fetch fresh jobs, validate, score, and generate docs for top matches."""
        _log = console.log if console else logger.info

        _log("[bold cyan]Fetching jobs from all sources…[/]" if console else "Fetching jobs…")
        raw_jobs = fetch_all_jobs(self.prefs)

        _log(
            f"[cyan]Validating {len(raw_jobs)} jobs (age + liveness check)…[/]"
            if console else f"Validating {len(raw_jobs)} jobs…"
        )
        jobs, stats = filter_valid_jobs(raw_jobs, check_liveness=True)
        dropped_msg = (
            f"  Dropped: {stats['too_old']} too old (>21 days), "
            f"{stats['inactive']} inactive URLs"
        )
        _log(f"[dim]{dropped_msg}[/]" if console else dropped_msg)

        new_count = save_jobs(jobs)
        _log(
            f"[green]Found {len(jobs)} valid jobs, {new_count} new.[/]"
            if console else f"Found {len(jobs)} valid jobs, {new_count} new."
        )

        stale = jobs_needing_liveness_recheck(stale_hours=12)
        if stale:
            _log(
                f"[dim]Re-checking liveness of {len(stale)} stored jobs…[/]"
                if console else f"Re-checking {len(stale)} stored jobs…"
            )
            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=12) as pool:
                futures = {pool.submit(check_url_live, j.url): j for j in stale}
                for fut in as_completed(futures):
                    job = futures[fut]
                    try:
                        live = fut.result()
                    except Exception:
                        live = True
                    mark_job_liveness(job.id, live)

        unmatched = get_unmatched_jobs()
        if not unmatched:
            _log("All jobs already scored — nothing new to match.")
            return []

        _log(f"Scoring {len(unmatched)} unscored jobs with Claude…" if not console else
             f"[bold cyan]Scoring {len(unmatched)} unscored jobs with Claude…[/]")

        matches = match_jobs(self.resume, self.prefs, unmatched, self.client)

        for m in matches:
            save_match(m)

        matched_ids = {m.job_id for m in matches}
        from src.models import MatchResult
        for job in unmatched:
            if job.id not in matched_ids:
                save_match(MatchResult(
                    job_id=job.id,
                    score=0,
                    reasoning="Below threshold or filtered out.",
                    key_matches=[],
                    gaps=[],
                    recommendation="skip",
                ))

        top = matches[: self.prefs.auto_generate_top_n]
        if top:
            _log(f"Generating tailored resume + cover letter for top {len(top)} matches…" if not console
                 else f"[bold cyan]Generating documents for top {len(top)} matches…[/]")
        for m in top:
            if documents_exist(m.job_id):
                continue
            job = get_job(m.job_id)
            if not job:
                continue
            try:
                docs = generate_documents(self.resume, job, self.client)
                save_documents(docs)
                _log(f"  ✓ {job.title} @ {job.company}" if not console else
                     f"  [green]✓[/] {job.title} @ {job.company}")
            except Exception as exc:
                logger.error("Document generation failed for %s: %s", m.job_id, exc)

        return matches

    def generate_for_job(self, job_id: str) -> bool:
        """Generate documents for a specific job by ID.

        Falls back to results/matches.json if the job isn't in the local DB,
        so the command works after pulling the repo without running a local search.
        """
        job = get_job(job_id)

        if job is None:
            job = _job_from_matches_json(job_id)
            if job is None:
                logger.error(
                    "Job %s not found in local DB or results/matches.json.\n"
                    "Run 'python main.py search' first, or pull the latest repo.",
                    job_id,
                )
                return False

        docs = generate_documents(self.resume, job, self.client)
        save_documents(docs)
        return True


def _job_from_matches_json(job_id: str) -> "JobPosting | None":
    """Load a JobPosting from the committed results/matches.json file."""
    import json as _json
    matches_path = Path(__file__).parent / "results" / "matches.json"
    if not matches_path.exists():
        return None
    try:
        data = _json.loads(matches_path.read_text(encoding="utf-8"))
        for m in data.get("matches", []):
            if m.get("job_id") == job_id:
                return JobPosting(
                    id=m["job_id"],
                    title=m["title"],
                    company=m["company"],
                    location=m["location"],
                    url=m.get("url", ""),
                    description=m.get("description", ""),
                    salary=m.get("salary"),
                    job_type=m.get("job_type"),
                    source=m.get("source", ""),
                    posted_at=m.get("posted_at"),
                    tags=[],
                    remote=bool(m.get("remote", False)),
                )
    except Exception as exc:
        logger.warning("Could not read matches.json: %s", exc)
    return None
