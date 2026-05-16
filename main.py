#!/usr/bin/env python3
"""
rolesearch — AI-powered job search agent

Usage:
  python main.py search          Run a fresh job search and score matches
  python main.py list            Show top matches from the database
  python main.py generate <id>   Generate resume + cover letter for a job ID
  python main.py daemon          Run continuously, refreshing every N hours
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich import box
from rich.panel import Panel
from rich.text import Text

load_dotenv()
logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

console = Console()


def cmd_search() -> None:
    from agent import RoleSearchAgent
    from src.storage import get_top_matches

    agent = RoleSearchAgent()
    with console.status("[bold cyan]Running job search…", spinner="dots"):
        matches = agent.refresh(console=None)

    if not matches:
        console.print("[yellow]No new matches found. Try running [bold]python main.py list[/] to see existing results.[/]")
        return

    _display_matches(get_top_matches(20))


def cmd_list() -> None:
    from src.storage import get_top_matches, init_db
    init_db()
    rows = get_top_matches(30)
    if not rows:
        console.print("[yellow]No matches yet. Run [bold]python main.py search[/] first.[/]")
        return
    _display_matches(rows)


def _display_matches(rows: list[dict]) -> None:
    if not rows:
        console.print("[yellow]No matches to display.[/]")
        return

    console.print()
    console.rule("[bold green]Top Job Matches")
    console.print()

    table = Table(
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
        expand=True,
    )
    table.add_column("#", style="dim", width=3)
    table.add_column("Score", width=7, justify="center")
    table.add_column("Title", style="bold")
    table.add_column("Company", style="cyan")
    table.add_column("Location", style="green")
    table.add_column("Salary", style="yellow")
    table.add_column("Rec.", width=7)
    table.add_column("ID", style="dim", width=10)

    rec_style = {"apply": "[bold green]apply[/]", "maybe": "[yellow]maybe[/]", "skip": "[red]skip[/]"}

    for i, r in enumerate(rows, 1):
        score = r["score"]
        score_color = "green" if score >= 75 else "yellow" if score >= 60 else "red"
        table.add_row(
            str(i),
            f"[{score_color}]{score}[/]",
            r["title"],
            r["company"],
            r["location"],
            r.get("salary") or "—",
            rec_style.get(r["recommendation"], r["recommendation"]),
            r["job_id"][:8],
        )

    console.print(table)
    console.print()

    # Show reasoning for top 3
    for r in rows[:3]:
        import json
        key_matches = json.loads(r["key_matches"]) if isinstance(r["key_matches"], str) else r["key_matches"]
        gaps = json.loads(r["gaps"]) if isinstance(r["gaps"], str) else r["gaps"]

        panel_text = Text()
        panel_text.append("Why it's a match:\n", style="bold green")
        for km in key_matches[:4]:
            panel_text.append(f"  ✓ {km}\n", style="green")
        if gaps:
            panel_text.append("\nGaps:\n", style="bold yellow")
            for g in gaps[:3]:
                panel_text.append(f"  △ {g}\n", style="yellow")
        panel_text.append(f"\n{r['reasoning'][:300]}", style="dim")

        console.print(Panel(
            panel_text,
            title=f"[bold]{r['title']}[/] @ [cyan]{r['company']}[/] — Score [green]{r['score']}[/]",
            subtitle=f"[dim]{r['url'] if 'url' in r else ''}[/]",
            border_style="green" if r["recommendation"] == "apply" else "yellow",
        ))

    console.print()
    console.print(
        "[dim]Run [bold]python main.py generate <job-id>[/] to generate tailored resume + cover letter.[/]"
    )
    console.print()


def cmd_generate(job_id: str) -> None:
    from agent import RoleSearchAgent
    from src.storage import documents_exist, get_documents, init_db

    init_db()

    if documents_exist(job_id):
        console.print(f"[yellow]Documents already exist for job [bold]{job_id}[/]. Retrieving…[/]")
        docs = get_documents(job_id)
        if docs:
            _show_docs(docs)
        return

    agent = RoleSearchAgent()
    with console.status(f"[bold cyan]Generating documents for job {job_id[:8]}…", spinner="dots"):
        success = agent.generate_for_job(job_id)

    if not success:
        console.print(f"[red]Job ID [bold]{job_id}[/] not found. Run a search first.[/]")
        return

    docs = get_documents(job_id)
    if docs:
        _show_docs(docs)


def _show_docs(docs) -> None:
    from src.generator import OUTPUT_DIR
    safe_company = "".join(c if c.isalnum() or c in " _-" else "_" for c in docs.company).strip()
    safe_title = "".join(c if c.isalnum() or c in " _-" else "_" for c in docs.job_title).strip()
    folder = OUTPUT_DIR / f"{safe_company}_{safe_title}"

    console.print()
    console.print(Panel(
        f"[green]Documents generated for[/] [bold]{docs.job_title}[/] @ [cyan]{docs.company}[/]\n\n"
        f"[dim]Output folder:[/]\n  {folder}\n\n"
        f"  • [bold]tailored_resume.md[/]\n"
        f"  • [bold]cover_letter.md[/]",
        title="[bold green]Documents Ready",
        border_style="green",
    ))
    console.print()

    console.rule("[bold]Cover Letter Preview")
    preview = docs.cover_letter[:800] + ("…" if len(docs.cover_letter) > 800 else "")
    console.print(preview)
    console.print()


def cmd_daemon() -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from agent import RoleSearchAgent

    interval_hours = int(os.getenv("REFRESH_INTERVAL_HOURS", "6"))

    console.print(Panel(
        f"[bold cyan]rolesearch daemon started[/]\n"
        f"Refreshing every [bold]{interval_hours}[/] hours.\n"
        f"Press [bold]Ctrl+C[/] to stop.",
        border_style="cyan",
    ))

    agent = RoleSearchAgent()

    def run_refresh():
        console.rule("[dim]Scheduled refresh")
        agent.refresh(console=None)
        from src.storage import get_top_matches
        rows = get_top_matches(10)
        _display_matches(rows)

    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(run_refresh, "interval", hours=interval_hours, id="refresh")

    console.print("[dim]Running initial search now…[/]")
    run_refresh()

    try:
        scheduler.start()
    except KeyboardInterrupt:
        console.print("\n[yellow]Daemon stopped.[/]")


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
    else:
        console.print(f"[red]Unknown command: {cmd}[/]")
        console.print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
