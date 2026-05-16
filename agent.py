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
    save_documents,
    save_jobs,
    save_match,
)

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
        """Fetch fresh jobs, score them, generate docs for top matches."""
        _log = console.log if console else logger.info

        _log("[bold cyan]Fetching jobs from all sources…[/]" if console else "Fetching jobs…")
        jobs = fetch_all_jobs(self.prefs)
        new_count = save_jobs(jobs)
        _log(f"Found {len(jobs)} jobs total, {new_count} new." if not console else
             f"[green]Found {len(jobs)} jobs total, {new_count} new.[/]")

        unmatched = get_unmatched_jobs()
        if not unmatched:
            _log("All jobs already scored — nothing new to match.")
            return []

        _log(f"Scoring {len(unmatched)} unscored jobs with Claude…" if not console else
             f"[bold cyan]Scoring {len(unmatched)} unscored jobs with Claude…[/]")

        matches = match_jobs(self.resume, self.prefs, unmatched, self.client)

        for m in matches:
            save_match(m)

        # Also save "skip" results so we don't re-score them
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
        """Generate documents for a specific job by ID."""
        job = get_job(job_id)
        if not job:
            logger.error("Job %s not found in database.", job_id)
            return False
        docs = generate_documents(self.resume, job, self.client)
        save_documents(docs)
        return True
