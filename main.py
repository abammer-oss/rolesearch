#!/usr/bin/env python3
"""
rolesearch — AI-powered job search agent

Usage:
  python main.py search          Fetch fresh jobs, validate, score, and display results
  python main.py list            Show ranked matches from the database
  python main.py generate <id>   Generate tailored resume + cover letter for a job
  python main.py daemon          Run continuously, refreshing every N hours (default: 6)
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

    if not new_matches:
        print("\nNothing new to report. All done.")
        return

    print(f"\n[3/3] Opening GitHub Issues in {repo}…")
    created = notify_new_matches(new_matches)

    for m in new_matches:
        mark_issue_created(m["job_id"])

    print(f"\nDone — {created} issue(s) opened.")


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
    elif cmd == "ci":
        cmd_ci()
    else:
        console.print(f"[red]Unknown command: {cmd}[/]")
        console.print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
