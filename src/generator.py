"""Generate tailored resume and cover letter for a specific job posting."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import anthropic

from .models import GeneratedDocuments, JobPosting, Resume

logger = logging.getLogger(__name__)

GENERATE_MODEL = os.getenv("GENERATE_MODEL", "claude-sonnet-4-6")
OUTPUT_DIR = Path(__file__).parent.parent / "output"


def _resume_to_text(resume: Resume) -> str:
    lines = [
        f"# {resume.personal.name}",
        f"{resume.personal.email} | {resume.personal.phone or ''} | {resume.personal.location or ''}",
    ]
    if resume.personal.linkedin:
        lines.append(f"LinkedIn: {resume.personal.linkedin}")
    if resume.personal.github:
        lines.append(f"GitHub: {resume.personal.github}")
    lines += ["", "## Summary", resume.summary, "", "## Skills"]
    for cat, skills in resume.skills.items():
        lines.append(f"**{cat.title()}:** {', '.join(skills)}")
    lines += ["", "## Experience"]
    for exp in resume.experience:
        lines.append(
            f"\n### {exp.title} — {exp.company}"
            f"  ({exp.start_date} – {exp.end_date or 'Present'})"
        )
        if exp.location:
            lines.append(f"*{exp.location}*")
        for h in exp.highlights:
            lines.append(f"- {h}")
    lines += ["", "## Education"]
    for edu in resume.education:
        lines.append(f"**{edu.degree}** — {edu.institution} ({edu.year or ''})")
        if edu.gpa:
            lines.append(f"GPA: {edu.gpa}" + (f" | {edu.honors}" if edu.honors else ""))
    if resume.projects:
        lines += ["", "## Projects"]
        for p in resume.projects:
            url = f" — {p.url}" if p.url else ""
            lines.append(f"**{p.name}**{url}")
            lines.append(p.description)
            if p.technologies:
                lines.append(f"*Tech: {', '.join(p.technologies)}*")
    if resume.certifications:
        lines += ["", "## Certifications"]
        for c in resume.certifications:
            lines.append(f"- {c}")
    return "\n".join(lines)


_RESUME_PROMPT = """\
You are a professional resume writer. Your task is to tailor the candidate's resume
specifically for the job posting below — without fabricating any experience or skills.

Rules:
1. Reorder and reword bullet points to emphasize the most relevant experience FIRST.
2. Adjust the summary to mirror the language of the job description.
3. Highlight skills and technologies mentioned in the job description.
4. Keep ALL factual information accurate — do not add fake experience.
5. Output clean Markdown formatted as a professional resume.
6. Do not add a "References" section.

## Original Resume
{resume}

## Job Posting
Title: {title}
Company: {company}
Location: {location}
Description:
{description}

Output ONLY the tailored resume in Markdown. No preamble, no commentary.
"""

_COVER_LETTER_PROMPT = """\
You are an expert cover letter writer. Write a compelling, personalized cover letter
for the candidate applying to the role below.

Requirements:
- 3–4 paragraphs, no longer than 400 words
- Opening: express genuine enthusiasm for THIS company/role specifically
- Body: connect 2–3 of the candidate's specific achievements to the job's needs
- Closing: clear call to action, professional sign-off
- Tone: confident, professional, conversational — not stiff or generic
- Do NOT start with "I am writing to apply for…"
- Do NOT use buzzwords like "synergy", "leverage", "passionate" (overused)

## Candidate Resume
{resume}

## Job Posting
Title: {title}
Company: {company}
Location: {location}
Description:
{description}

Output ONLY the cover letter in Markdown. No preamble, no commentary.
"""


def generate_documents(
    resume: Resume,
    job: JobPosting,
    client: anthropic.Anthropic,
) -> GeneratedDocuments:
    resume_text = _resume_to_text(resume)
    desc = job.description[:3000]

    tailored_resume = _call_claude(
        client,
        _RESUME_PROMPT.format(
            resume=resume_text,
            title=job.title,
            company=job.company,
            location=job.location,
            description=desc,
        ),
    )
    cover_letter = _call_claude(
        client,
        _COVER_LETTER_PROMPT.format(
            resume=resume_text,
            title=job.title,
            company=job.company,
            location=job.location,
            description=desc,
        ),
    )
    docs = GeneratedDocuments(
        job_id=job.id,
        job_title=job.title,
        company=job.company,
        tailored_resume=tailored_resume,
        cover_letter=cover_letter,
    )
    _write_to_disk(docs)
    return docs


def _call_claude(client: anthropic.Anthropic, prompt: str) -> str:
    response = client.messages.create(
        model=GENERATE_MODEL,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


def _write_to_disk(docs: GeneratedDocuments) -> None:
    safe_company = "".join(c if c.isalnum() or c in " _-" else "_" for c in docs.company).strip()
    safe_title = "".join(c if c.isalnum() or c in " _-" else "_" for c in docs.job_title).strip()
    folder = OUTPUT_DIR / f"{safe_company}_{safe_title}"
    folder.mkdir(parents=True, exist_ok=True)

    (folder / "tailored_resume.md").write_text(docs.tailored_resume, encoding="utf-8")
    (folder / "cover_letter.md").write_text(docs.cover_letter, encoding="utf-8")

    match_header = f"# Match: {docs.job_title} @ {docs.company}\n\nJob ID: `{docs.job_id}`\n"
    (folder / "job_info.md").write_text(match_header, encoding="utf-8")

    logger.info("Documents written to %s", folder)
