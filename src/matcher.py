"""Claude-powered job matching — scores and filters jobs against the resume."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import anthropic

from .models import JobPosting, JobPreferences, MatchResult, Resume

logger = logging.getLogger(__name__)

MATCH_MODEL = os.getenv("MATCH_MODEL", "claude-haiku-4-5-20251001")

_SCORE_TOOL = {
    "name": "score_jobs",
    "description": "Score and evaluate a list of job postings against a resume.",
    "input_schema": {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "job_id":         {"type": "string"},
                        "score":          {"type": "integer", "minimum": 0, "maximum": 100},
                        "reasoning":      {"type": "string"},
                        "key_matches":    {"type": "array", "items": {"type": "string"}},
                        "gaps":           {"type": "array", "items": {"type": "string"}},
                        "recommendation": {"type": "string", "enum": ["apply", "maybe", "skip"]},
                    },
                    "required": ["job_id", "score", "reasoning", "key_matches", "gaps", "recommendation"],
                },
            }
        },
        "required": ["results"],
    },
}


def _resume_summary(resume: Resume) -> str:
    skills_flat = ", ".join(
        skill for skills in resume.skills.values() for skill in skills
    )
    recent = resume.experience[:3]
    exp_lines = "\n".join(
        f"- {e.title} @ {e.company} ({e.start_date}–{e.end_date or 'Present'}): "
        + "; ".join(e.highlights[:2])
        for e in recent
    )
    return (
        f"Name: {resume.personal.name}\n"
        f"Summary: {resume.summary}\n"
        f"Skills: {skills_flat}\n"
        f"Recent experience:\n{exp_lines}\n"
        f"Education: {resume.education[0].degree} from {resume.education[0].institution}"
        if resume.education
        else ""
    )


def _prefs_summary(prefs: JobPreferences) -> str:
    parts = [f"Desired titles: {', '.join(prefs.job_titles)}"]
    if prefs.locations:
        parts.append(f"Locations: {', '.join(prefs.locations)}")
    if prefs.salary_min:
        parts.append(f"Minimum salary: ${prefs.salary_min:,}/yr")
    if prefs.keywords:
        parts.append(f"Must-have keywords: {', '.join(prefs.keywords)}")
    if prefs.deal_breakers:
        parts.append(f"Deal-breakers: {', '.join(prefs.deal_breakers)}")
    return "\n".join(parts)


def _build_prompt(resume: Resume, prefs: JobPreferences, batch: list[JobPosting]) -> str:
    jobs_text = "\n\n".join(
        f"[JOB {i + 1}]\nID: {j.id}\nTitle: {j.title}\nCompany: {j.company}\n"
        f"Location: {j.location}\nType: {j.job_type or 'N/A'}\nSalary: {j.salary or 'N/A'}\n"
        f"Tags: {', '.join(j.tags) if j.tags else 'N/A'}\n"
        f"Description (first 600 chars):\n{j.description[:600]}"
        for i, j in enumerate(batch)
    )
    return f"""You are an expert career advisor. Evaluate each job posting against the candidate's resume and preferences.

## Candidate Resume
{_resume_summary(resume)}

## Candidate Preferences
{_prefs_summary(prefs)}

## Job Postings to Evaluate
{jobs_text}

For each job, call the score_jobs tool with:
- score 0–100 (how well the job fits the candidate's skills AND preferences)
- key_matches: specific skills/experience that align
- gaps: skills/requirements the candidate may lack
- recommendation: "apply" (≥75), "maybe" (50–74), "skip" (<50 or deal-breaker present)

Be strict: a job with a deal-breaker keyword automatically gets recommendation "skip" and score ≤20.
A job that doesn't match preferred titles or requires skills the candidate lacks should score lower.
"""


def score_batch(
    resume: Resume,
    prefs: JobPreferences,
    jobs: list[JobPosting],
    client: anthropic.Anthropic,
) -> list[MatchResult]:
    prompt = _build_prompt(resume, prefs, jobs)
    try:
        response = client.messages.create(
            model=MATCH_MODEL,
            max_tokens=4096,
            tools=[_SCORE_TOOL],
            tool_choice={"type": "any"},
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        logger.error("Claude matching API error: %s", exc)
        return []

    for block in response.content:
        if block.type == "tool_use" and block.name == "score_jobs":
            raw: list[dict[str, Any]] = block.input.get("results", [])
            results = []
            for r in raw:
                try:
                    results.append(MatchResult(**r))
                except Exception as e:
                    logger.warning("Could not parse match result %s: %s", r, e)
            return results
    return []


def match_jobs(
    resume: Resume,
    prefs: JobPreferences,
    jobs: list[JobPosting],
    client: anthropic.Anthropic,
    batch_size: int = 8,
) -> list[MatchResult]:
    """Score all jobs in batches. Returns matches above min_match_score threshold."""
    all_results: list[MatchResult] = []
    for i in range(0, len(jobs), batch_size):
        batch = jobs[i : i + batch_size]
        logger.info("Scoring batch %d–%d of %d jobs…", i + 1, i + len(batch), len(jobs))
        results = score_batch(resume, prefs, batch, client)
        all_results.extend(results)

    filtered = [
        r for r in all_results
        if r.score >= prefs.min_match_score and r.recommendation != "skip"
    ]
    filtered.sort(key=lambda r: r.score, reverse=True)
    logger.info(
        "matched %d / %d jobs above score threshold %d",
        len(filtered), len(all_results), prefs.min_match_score,
    )
    return filtered
