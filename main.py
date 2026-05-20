#!/usr/bin/env python3
"""
rolesearch — AI-powered job search agent

Usage:
  python main.py search                         Fetch fresh jobs, score, and display results
  python main.py list                           Show ranked matches from the database
  python main.py generate <id>                  Generate tailored resume + cover letter for a job
  python main.py ingest <url> [url2 ...]        Manually ingest URLs through full pipeline
  python main.py ingest --file urls.txt         Ingest URLs from a file (one per line)
  python main.py ingest --dry-run <url>         Parse + score only; no documents generated
  python main.py daemon                         Run continuously, refreshing every N hours
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

console = Console()

# ── Tier config ─────────────────────────────────────────────────────────────────────────────────

_TIERS = {
    1: ("APPLY NOW",          "bold green",  "🚀"),
    2: ("APPLY / OUTREACH",   "bold yellow", "⚡"),
    3: ("TRACK / CONSIDER",   "bold cyan",   "🔍"),
}

_REC_LABELS = {
    "apply_now":          "[bold green]🚀  APPLY NOW[/]",
    "apply_selectively":  "[bold yellow]⚡  APPLY SELECTIVELY[/]",
    "outreach_first":     "[bold blue]📨  OUTREACH FIRST[/]",
    "track_only":         "[cyan]👁  TRACK ONLY[/]",
    "skip":               "[red]✗  SKIP[/]",
    # legacy
    "apply":              "[bold green]✅  GO — Apply now[/]",
    "maybe":              "[yellow]⚠   MAYBE — Apply if interested[/]",
}

_REC_SHORT = {
    "apply_now":         "[bold green]🚀 APPLY NOW[/]",
    "apply_selectively": "[yellow]⚡ SELECTIVE[/]",
    "outreach_first":    "[bold blue]📨 OUTREACH[/]",
    "track_only":        "[cyan]👁 TRACK[/]",
    "skip":              "[red]✗ SKIP[/]",
    "apply":             "[bold green]✅ GO[/]",
    "maybe":             "[yellow]⚠ MAYBE[/]",
}


# ── Commands ─────────────────────────────────────────────────────────────────────────────────

def cmd_search() -> None:
    from agent import RoleSearchAgent
    from src.storage import get_top_matches

    agent = RoleSearchAgent()
    with console.status("[bold cyan]Running job search…", spinner="dots"):
        agent.refresh(console=None)

    rows = get_top_matches(30)
    _display_ranked(rows)


def cmd_list() -> None:
    from src.storage import get_top_matches, init_db
    init_db()
    rows = get_top_matches(30)
    if not rows:
        console.print(
            "[yellow]No matches yet. Run [bold]python main.py search[/] first.[/]"
        )
        return
    _display_ranked(rows)


def cmd_generate(job_id: str) -> None:
    from agent import RoleSearchAgent
    from src.storage import documents_exist, get_documents, init_db

    init_db()

    if documents_exist(job_id):
        docs = get_documents(job_id)
        if docs:
            _show_docs(docs)
        return

    agent = RoleSearchAgent()
    with console.status(
        f"[bold cyan]Generating documents for job {job_id[:8]}…", spinner="dots"
    ):
        success = agent.generate_for_job(job_id)

    if not success:
        console.print(
            f"[red]Job ID [bold]{job_id}[/] not found. Run a search first.[/]"
        )
        return

    docs = get_documents(job_id)
    if docs:
        _show_docs(docs)


def cmd_ci() -> None:
    """Run in GitHub Actions: search, score, open Issues, and export matches.json."""
    import os
    from agent import RoleSearchAgent
    from src.storage import (
        get_new_matches_for_notification, get_top_matches,
        mark_issue_created, init_db,
    )
    from src.notifier import notify_new_matches

    init_db()
    repo = os.getenv("GITHUB_REPO", "")
    if not repo:
        print("ERROR: GITHUB_REPO environment variable not set.")
        sys.exit(1)

    print("=" * 60)
    print("RoleSearch — GitHub Actions CI Run")
    print("=" * 60)

    agent = RoleSearchAgent()

    print("\n[1/3] Fetching and validating jobs…")
    agent.refresh(console=None)

    print("\n[2/3] Checking for new matches to report…")
    new_matches = get_new_matches_for_notification(limit=20)
    print(f"      Found {len(new_matches)} new match(es) needing notification.")

    # Always export full match catalogue so `generate` works locally without re-running search
    _export_matches_json(get_top_matches(50))

    # Export near-miss jobs (scored 50–59) for local review
    _export_near_miss_json()

    if not new_matches:
        print("\nNothing new to report. All done.")
        return

    print(f"\n[3/3] Opening GitHub Issues in {repo}…")
    created = notify_new_matches(new_matches)

    for m in new_matches:
        mark_issue_created(m["job_id"])

    print(f"\nDone — {created} issue(s) opened.")


def cmd_ingest(raw_args: list[str]) -> None:
    """
    Manually ingest one or more job URLs through the full evaluation + drafting pipeline.

    Usage:
      python main.py ingest <url> [url2 ...]
      python main.py ingest --file urls.txt
      python main.py ingest --stdin
      python main.py ingest --dry-run <url> [url2 ...]

    Options:
      --dry-run           Parse and score only; do not generate documents
      --priority high     Tag all jobs as high priority in dashboard/tracker
      --company-notes TEXT  Extra context appended to the JD before scoring
      --file PATH         Read URLs (one per line) from a file
      --stdin             Read URLs from stdin (one per line)
    """
    import anthropic
    from agent import load_preferences, load_resume, _make_client
    from src.ingester import ingest_batch
    from src.matcher import score_batch
    from src.generator import generate_documents
    from src.run_writer import make_run_dir, write_job_artifacts, write_dashboard, append_tracker
    from src.storage import init_db

    # ── Parse flags ──────────────────────────────────────────────────────────
    dry_run = "--dry-run" in raw_args
    priority_override = None
    company_notes = ""
    urls: list[str] = []

    i = 0
    args = [a for a in raw_args if a != "--dry-run"]
    while i < len(args):
        a = args[i]
        if a == "--priority" and i + 1 < len(args):
            priority_override = args[i + 1]
            i += 2
        elif a == "--company-notes" and i + 1 < len(args):
            company_notes = args[i + 1]
            i += 2
        elif a == "--file" and i + 1 < len(args):
            path = Path(args[i + 1])
            if not path.exists():
                console.print(f"[red]File not found: {path}[/]")
                sys.exit(1)
            urls.extend(u.strip() for u in path.read_text().splitlines() if u.strip())
            i += 2
        elif a == "--stdin":
            import sys as _sys
            urls.extend(u.strip() for u in _sys.stdin.read().splitlines() if u.strip())
            i += 1
        elif a.startswith("http"):
            urls.append(a)
            i += 1
        else:
            console.print(f"[yellow]Ignoring unrecognised argument: {a}[/]")
            i += 1

    if not urls:
        console.print("[red]No URLs provided. Usage: python main.py ingest <url> [url2 ...][/]")
        sys.exit(1)

    if len(urls) > 25:
        console.print(f"[yellow]Batch capped at 25 URLs. Truncating from {len(urls)}.[/]")
        urls = urls[:25]

    console.print(f"\n[bold cyan]Ingest run — {len(urls)} URL(s){'  [DRY RUN]' if dry_run else ''}[/]")
    if priority_override:
        console.print(f"  Priority override: [bold]{priority_override}[/]")
    if company_notes:
        console.print(f"  Company notes: [dim]{company_notes}[/]")

    # ── Setup ──────────────────────────────────────────────────────────────────
    init_db()
    resume = load_resume()
    prefs = load_preferences()
    client = _make_client()
    run_dir = make_run_dir()

    # ── Stage 1: Ingest & Parse ───────────────────────────────────────────────
    console.print("\n[1/3] Fetching and parsing job descriptions…")
    jobs, failures = ingest_batch(urls, client, company_notes=company_notes)

    console.print(f"      Parsed: [green]{len(jobs)}[/]  Failed: [{'red' if failures else 'dim'}]{len(failures)}[/]")
    for f in failures:
        console.print(f"      [red]✗[/] {f['url']}\n        [dim]{f['error']}[/]")

    if not jobs:
        console.print("[red]No jobs could be parsed. Exiting.[/]")
        sys.exit(1)

    # ── Stage 2: Score ─────────────────────────────────────────────────────────
    console.print("\n[2/3] Scoring with Claude…")
    matches = score_batch(resume, prefs, jobs, client)
    match_by_id = {m.job_id: m for m in matches}

    # Jobs Claude didn't return (shouldn't happen, but handle gracefully)
    from src.models import MatchResult as MR
    for job in jobs:
        if job.id not in match_by_id:
            match_by_id[job.id] = MR(
                job_id=job.id, score=0, reasoning="No score returned.",
                key_matches=[], gaps=[], recommendation="skip",
            )
    matches = list(match_by_id.values())

    # ── Stage 3: Generate documents (skip if dry-run) ──────────────────────────
    docs_map: dict[str, object] = {}
    if dry_run:
        console.print("\n[3/3] [dim]DRY RUN — skipping document generation[/]")
    else:
        console.print(f"\n[3/3] Generating resume + cover letter drafts…")
        for job in jobs:
            m = match_by_id[job.id]
            if m.recommendation == "skip" and priority_override != "high":
                console.print(f"      [dim]Skip (scored {m.score}): {job.title} @ {job.company}[/]")
                continue
            try:
                docs = generate_documents(resume, job, client)
                docs_map[job.id] = docs
                console.print(f"      [green]✓[/] {job.title} @ {job.company}  (score {m.score})")
            except Exception as exc:
                console.print(f"      [red]✗[/] {job.title} @ {job.company}: {exc}")

    # ── Write output ────────────────────────────────────────────────────────────
    for job in jobs:
        write_job_artifacts(
            run_dir, job, match_by_id[job.id],
            docs_map.get(job.id),
            priority_override=priority_override,
        )

    dashboard_path = write_dashboard(
        run_dir, jobs, matches, failures,
        dry_run=dry_run, priority_override=priority_override,
    )
    if not dry_run:
        append_tracker(jobs, matches, priority_override=priority_override)

    # ── Summary ─────────────────────────────────────────────────────────────────
    console.print()
    _display_ranked([
        {**vars(m), "title": j.title, "company": j.company,
         "location": j.location, "salary": j.salary, "url": j.url,
         "source": j.source, "remote": j.remote, "posted_at": j.posted_at,
         "key_matches": json.dumps(m.key_matches),
         "gaps": json.dumps(m.gaps),
         "resume_angles": json.dumps(m.resume_angles),
         "risks": json.dumps(m.risks),
         }
        for j, m in [(j, match_by_id[j.id]) for j in jobs]
        if match_by_id[j.id].recommendation != "skip" or priority_override == "high"
    ])

    console.print(f"\n[bold green]Run complete.[/]")
    console.print(f"  Dashboard: [bold]{dashboard_path}[/]")
    console.print(f"  Run folder: [bold]{run_dir}[/]")
    if not dry_run:
        from src.run_writer import TRACKER_CSV
        console.print(f"  Tracker: [bold]{TRACKER_CSV}[/]")


def _export_matches_json(rows: list[dict]) -> None:
    """Write results/matches.json so generate works locally without a local search."""
    from datetime import datetime, timezone
    from src.storage import get_job

    results_dir = Path(__file__).parent / "results"
    results_dir.mkdir(exist_ok=True)

    enriched = []
    for r in rows:
        job = get_job(r["job_id"])
        entry = dict(r)
        entry["description"] = job.description if job else ""
        entry["key_matches"] = json.loads(r["key_matches"]) if isinstance(r["key_matches"], str) else r["key_matches"]
        entry["gaps"]        = json.loads(r["gaps"])        if isinstance(r["gaps"], str)        else r["gaps"]
        enriched.append(entry)

    out = {
        "exported_at": datetime.now(tz=timezone.utc).isoformat(),
        "matches": enriched,
    }
    path = results_dir / "matches.json"
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"      Exported {len(enriched)} match(es) → results/matches.json")


def _export_near_miss_json() -> None:
    """Export jobs scored 50–59 (just below threshold) for manual review."""
    import sqlite3
    from src.storage import DB_PATH

    try:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """SELECT m.job_id, m.score, m.fit_score, m.competitiveness_score, m.roi_score,
                      m.reasoning, m.recommendation,
                      j.title, j.company, j.location, j.url, j.salary, j.source
               FROM matches m
               JOIN jobs j ON j.id = m.job_id
               WHERE m.score BETWEEN 50 AND 59
                 AND j.is_active = 1
               ORDER BY m.score DESC
               LIMIT 30"""
        ).fetchall()
        con.close()
    except Exception as exc:
        print(f"      near-miss export skipped: {exc}")
        return

    if not rows:
        print("      No near-miss jobs (50–59) to export.")
        return

    results_dir = Path(__file__).parent / "results"
    results_dir.mkdir(exist_ok=True)
    path = results_dir / "near_miss.json"
    path.write_text(
        json.dumps(
            {"exported_at": datetime.now(tz=timezone.utc).isoformat(),
             "near_miss": [dict(r) for r in rows]},
            indent=2, default=str,
        ),
        encoding="utf-8",
    )
    print(f"      Exported {len(rows)} near-miss job(s) (scored 50–59) → results/near_miss.json")


def cmd_daemon() -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from agent import RoleSearchAgent

    interval_hours = int(os.getenv("REFRESH_INTERVAL_HOURS", "6"))

    console.print(Panel(
        f"[bold cyan]rolesearch daemon started[/]\n"
        f"Auto-refresh every [bold]{interval_hours}h[/].  Press [bold]Ctrl+C[/] to stop.",
        border_style="cyan",
    ))

    agent = RoleSearchAgent()

    def run_refresh() -> None:
        console.rule("[dim]Scheduled refresh")
        agent.refresh(console=None)
        from src.storage import get_top_matches
        _display_ranked(get_top_matches(20))

    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(run_refresh, "interval", hours=interval_hours, id="refresh")

    console.print("[dim]Running initial search now…[/]")
    run_refresh()

    try:
        scheduler.start()
    except KeyboardInterrupt:
        console.print("\n[yellow]Daemon stopped.[/]")


# ── Display helpers ────────────────────────────────────────────────────────────────────────────

def _display_ranked(rows: list[dict]) -> None:
    if not rows:
        console.print("[yellow]No matches to display.[/]")
        return

    by_tier: dict[int, list[dict]] = {1: [], 2: [], 3: []}
    for r in rows:
        rank = r.get("priority_rank") or 3
        by_tier.setdefault(rank, []).append(r)

    console.print()
    _print_executive_table(rows)
    console.print()

    for tier in (1, 2, 3):
        tier_jobs = by_tier.get(tier, [])
        if not tier_jobs:
            continue
        label, color, icon = _TIERS[tier]
        console.print(Rule(f"[{color}]{icon}  TIER {tier} — {label}  ({len(tier_jobs)} {'job' if len(tier_jobs)==1 else 'jobs'})[/]"))
        console.print()
        for i, r in enumerate(tier_jobs, 1):
            _print_job_card(r, i)
        console.print()

    console.print(
        "[dim]Generate tailored resume + cover letter: "
        "[bold]python main.py generate <job-id>[/][/]"
    )
    console.print()


def _print_executive_table(rows: list[dict]) -> None:
    """Compact ranked summary table with three-dimension scores."""
    from src.validator import _parse_date

    table = Table(
        title="[bold]Ranked Job Matches",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
        expand=True,
    )
    table.add_column("Rank",     width=5,  justify="center")
    table.add_column("Fit",      width=5,  justify="center")
    table.add_column("Comp",     width=5,  justify="center")
    table.add_column("ROI",      width=5,  justify="center")
    table.add_column("Overall",  width=7,  justify="center")
    table.add_column("Decision", width=12, justify="center")
    table.add_column("Title + Company", style="bold")
    table.add_column("Location", style="green")
    table.add_column("Salary",   style="yellow")
    table.add_column("Age",      width=8,  justify="right", style="dim")
    table.add_column("ID",       width=10, style="dim")

    def _sc(s: int) -> str:
        if s == 0:
            return "[dim]—[/]"
        c = "green" if s >= 80 else "yellow" if s >= 65 else "red"
        return f"[{c}]{s}[/]"

    for i, r in enumerate(rows, 1):
        rank  = r.get("priority_rank") or 3
        icon  = _TIERS[rank][2]
        score = r["score"]
        fit   = r.get("fit_score") or 0
        comp  = r.get("competitiveness_score") or 0
        roi   = r.get("roi_score") or 0
        rec   = r.get("recommendation", "maybe")

        age_label = "—"
        if r.get("posted_at"):
            dt = _parse_date(r["posted_at"])
            if dt:
                d = (datetime.now(tz=timezone.utc) - dt).days
                age_label = f"{d}d ago"

        table.add_row(
            f"{icon} #{i}",
            _sc(fit),
            _sc(comp),
            _sc(roi),
            _sc(score),
            _REC_SHORT.get(rec, rec),
            f"{r['title']}\n[dim]{r['company']}[/]",
            r["location"],
            r.get("salary") or "—",
            age_label,
            r["job_id"][:8],
        )

    console.print(table)


def _print_job_card(r: dict, position: int) -> None:
    rank = r.get("priority_rank") or 3
    _, color, icon = _TIERS[rank]
    score = r["score"]
    fit   = r.get("fit_score") or 0
    comp  = r.get("competitiveness_score") or 0
    roi   = r.get("roi_score") or 0
    rec   = r.get("recommendation", "maybe")

    def _sc(label: str, s: int) -> str:
        if s == 0:
            return f"[dim]{label}: —[/]"
        c = "green" if s >= 80 else "yellow" if s >= 65 else "red"
        return f"[dim]{label}:[/] [{c} bold]{s}[/]"

    key_matches    = json.loads(r["key_matches"])    if isinstance(r["key_matches"], str)    else r["key_matches"]
    gaps           = json.loads(r["gaps"])           if isinstance(r["gaps"], str)           else r["gaps"]
    resume_angles  = json.loads(r.get("resume_angles") or "[]") if isinstance(r.get("resume_angles"), str) else (r.get("resume_angles") or [])
    risks          = json.loads(r.get("risks") or "[]")         if isinstance(r.get("risks"), str)         else (r.get("risks") or [])
    outreach       = r.get("outreach_strategy") or ""
    exec_summary   = r.get("executive_summary") or ""

    body = Text()

    # Recommendation + scores
    body.append(f"  {_REC_LABELS.get(rec, rec)}\n", style="")
    score_line = "  " + "   ".join([
        _sc("Fit", fit), _sc("Comp", comp), _sc("ROI", roi),
        f"[dim]Overall:[/] [{'green' if score >= 80 else 'yellow' if score >= 65 else 'red'} bold]{score}[/]",
    ]) + "\n\n"
    body.append(score_line, style="")

    # Meta
    meta_parts = [r["location"]]
    if r.get("salary"):
        meta_parts.append(r["salary"])
    if r.get("job_type"):
        meta_parts.append(r["job_type"])
    body.append("  " + "  |  ".join(meta_parts) + "\n\n", style="dim")

    # Executive summary
    if exec_summary:
        body.append("  EXECUTIVE SUMMARY\n", style="bold underline")
        for line in _wrap(exec_summary, 90):
            body.append(f"  {line}\n", style="")
        body.append("\n")

    # Resume angles
    if resume_angles:
        body.append("  LEAD WITH\n", style="bold green underline")
        for a in resume_angles[:3]:
            body.append(f"  • {a}\n", style="green")
        body.append("\n")

    # Key matches
    if key_matches:
        body.append("  KEY MATCHES  ", style="bold green")
        body.append("  ".join(f"[green]{m}[/]" for m in key_matches[:5]))
        body.append("\n")

    # Risks
    if risks:
        body.append("  RISKS / GAPS\n", style="bold yellow underline")
        for risk in risks[:3]:
            body.append(f"  ⚠ {risk}\n", style="yellow")
        body.append("\n")
    elif gaps:
        body.append("  GAPS  ", style="bold yellow")
        body.append("  ".join(f"[yellow]{g}[/]" for g in gaps[:3]))
        body.append("\n\n")

    # Outreach strategy
    if outreach and rec in ("outreach_first", "apply_selectively"):
        body.append("  OUTREACH\n", style="bold blue underline")
        for line in _wrap(outreach, 90):
            body.append(f"  {line}\n", style="blue")
        body.append("\n")

    body.append(f"\n  Apply:  {r.get('url', '—')}\n", style="dim")
    body.append(f"  Docs:   python main.py generate {r['job_id']}\n", style="dim")

    _border = {
        "apply_now": "green", "apply": "green",
        "apply_selectively": "yellow", "maybe": "yellow",
        "outreach_first": "blue",
        "track_only": "cyan",
    }.get(rec, "red")

    console.print(Panel(
        body,
        title=f"[{color}]#{position}  {r['title']}[/]  [dim]@[/]  [cyan bold]{r['company']}[/]",
        border_style=_border,
        padding=(0, 1),
    ))
    console.print()


def _wrap(text: str, width: int) -> list[str]:
    """Simple word-wrap."""
    words = text.split()
    lines, current = [], []
    length = 0
    for word in words:
        if length + len(word) + 1 > width and current:
            lines.append(" ".join(current))
            current, length = [word], len(word)
        else:
            current.append(word)
            length += len(word) + 1
    if current:
        lines.append(" ".join(current))
    return lines


def _show_docs(docs) -> None:
    from src.generator import OUTPUT_DIR
    safe_company = "".join(c if c.isalnum() or c in " _-" else "_" for c in docs.company).strip()
    safe_title = "".join(c if c.isalnum() or c in " _-" else "_" for c in docs.job_title).strip()
    folder = OUTPUT_DIR / f"{safe_company}_{safe_title}"

    console.print(Panel(
        f"[green]Documents ready for[/] [bold]{docs.job_title}[/] @ [cyan]{docs.company}[/]\n\n"
        f"[dim]Folder:[/]  {folder}\n\n"
        f"  • [bold]tailored_resume.md[/]\n"
        f"  • [bold]cover_letter.md[/]",
        title="[bold green]Documents Generated",
        border_style="green",
    ))
    console.print()
    console.rule("[bold]Cover Letter Preview")
    preview = docs.cover_letter[:900] + ("…" if len(docs.cover_letter) > 900 else "")
    console.print(preview)
    console.print()


# ── Entry point ────────────────────────────────────────────────────────────────────────────────

def main() -> None:
    args = sys.argv[1:]

    if not args or args[0] in ("help", "--help", "-h"):
        console.print(__doc__)
        return

    cmd = args[0]

    if cmd == "search":
        cmd_search()
    elif cmd == "list":
        cmd_list()
    elif cmd == "generate":
        if len(args) < 2:
            console.print("[red]Usage: python main.py generate <job-id>[/]")
            sys.exit(1)
        cmd_generate(args[1])
    elif cmd == "daemon":
        cmd_daemon()
    elif cmd == "ingest":
        cmd_ingest(args[1:])
    elif cmd == "ci":
        cmd_ci()
    else:
        console.print(f"[red]Unknown command: {cmd}[/]")
        console.print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
