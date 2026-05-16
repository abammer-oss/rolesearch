"""SQLite persistence for jobs, match results, and generated documents."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from .models import GeneratedDocuments, JobPosting, MatchResult

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "rolesearch.db"


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    with _conn() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id          TEXT PRIMARY KEY,
                title       TEXT,
                company     TEXT,
                location    TEXT,
                url         TEXT,
                description TEXT,
                salary      TEXT,
                job_type    TEXT,
                source      TEXT,
                posted_at   TEXT,
                tags        TEXT,
                remote      INTEGER,
                fetched_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS matches (
                job_id          TEXT PRIMARY KEY,
                score           INTEGER,
                reasoning       TEXT,
                key_matches     TEXT,
                gaps            TEXT,
                recommendation  TEXT,
                matched_at      TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS documents (
                job_id          TEXT PRIMARY KEY,
                job_title       TEXT,
                company         TEXT,
                tailored_resume TEXT,
                cover_letter    TEXT,
                generated_at    TEXT
            );
        """)


def known_job_ids() -> set[str]:
    with _conn() as con:
        rows = con.execute("SELECT id FROM jobs").fetchall()
    return {r["id"] for r in rows}


def save_jobs(jobs: list[JobPosting]) -> int:
    """Insert new jobs; skip already-known ones. Returns count inserted."""
    existing = known_job_ids()
    new = [j for j in jobs if j.id not in existing]
    if not new:
        return 0
    with _conn() as con:
        con.executemany(
            """INSERT OR IGNORE INTO jobs
               (id, title, company, location, url, description,
                salary, job_type, source, posted_at, tags, remote)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    j.id, j.title, j.company, j.location, j.url, j.description,
                    j.salary, j.job_type, j.source, j.posted_at,
                    json.dumps(j.tags), int(j.remote),
                )
                for j in new
            ],
        )
    logger.info("saved %d new jobs to DB", len(new))
    return len(new)


def save_match(m: MatchResult) -> None:
    with _conn() as con:
        con.execute(
            """INSERT OR REPLACE INTO matches
               (job_id, score, reasoning, key_matches, gaps, recommendation)
               VALUES (?,?,?,?,?,?)""",
            (
                m.job_id, m.score, m.reasoning,
                json.dumps(m.key_matches), json.dumps(m.gaps),
                m.recommendation,
            ),
        )


def save_documents(docs: GeneratedDocuments) -> None:
    with _conn() as con:
        con.execute(
            """INSERT OR REPLACE INTO documents
               (job_id, job_title, company, tailored_resume, cover_letter, generated_at)
               VALUES (?,?,?,?,?,?)""",
            (
                docs.job_id, docs.job_title, docs.company,
                docs.tailored_resume, docs.cover_letter, docs.generated_at,
            ),
        )


def get_top_matches(limit: int = 20) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            """SELECT m.job_id, m.score, m.reasoning, m.key_matches, m.gaps,
                      m.recommendation, m.matched_at,
                      j.title, j.company, j.location, j.url, j.salary,
                      j.job_type, j.source, j.remote
               FROM matches m
               JOIN jobs j ON j.id = m.job_id
               WHERE m.recommendation != 'skip'
               ORDER BY m.score DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_unmatched_jobs() -> list[JobPosting]:
    with _conn() as con:
        rows = con.execute(
            """SELECT * FROM jobs
               WHERE id NOT IN (SELECT job_id FROM matches)""",
        ).fetchall()
    return [
        JobPosting(
            id=r["id"], title=r["title"], company=r["company"],
            location=r["location"], url=r["url"], description=r["description"],
            salary=r["salary"], job_type=r["job_type"], source=r["source"],
            posted_at=r["posted_at"], tags=json.loads(r["tags"] or "[]"),
            remote=bool(r["remote"]),
        )
        for r in rows
    ]


def get_job(job_id: str) -> JobPosting | None:
    with _conn() as con:
        r = con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not r:
        return None
    return JobPosting(
        id=r["id"], title=r["title"], company=r["company"],
        location=r["location"], url=r["url"], description=r["description"],
        salary=r["salary"], job_type=r["job_type"], source=r["source"],
        posted_at=r["posted_at"], tags=json.loads(r["tags"] or "[]"),
        remote=bool(r["remote"]),
    )


def documents_exist(job_id: str) -> bool:
    with _conn() as con:
        r = con.execute(
            "SELECT 1 FROM documents WHERE job_id = ?", (job_id,)
        ).fetchone()
    return r is not None


def get_documents(job_id: str) -> GeneratedDocuments | None:
    with _conn() as con:
        r = con.execute(
            "SELECT * FROM documents WHERE job_id = ?", (job_id,)
        ).fetchone()
    if not r:
        return None
    return GeneratedDocuments(
        job_id=r["job_id"], job_title=r["job_title"], company=r["company"],
        tailored_resume=r["tailored_resume"], cover_letter=r["cover_letter"],
        generated_at=r["generated_at"],
    )
