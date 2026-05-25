# rolesearch — Claude Instructions

## Project overview
AI-powered job search agent for Anthony Bammer. Autonomous runs every 6 hours via GitHub Actions.
Manual job evaluation via the ingest pipeline.

## Ingest a job URL

When Anthony gives you a job URL to evaluate, write `ingest-queue.json` to the repo root
and push it. This automatically triggers the "Ingest Job URL" GitHub Actions workflow.

### Queue file format
```json
{
  "queued_at": "<current UTC ISO timestamp>",
  "urls": ["<url1>", "<url2>"],
  "dry_run": false,
  "priority": "normal",
  "company_notes": ""
}
```

### Fields
- `queued_at` — always set to current UTC time (ensures a new commit even if same URL is re-submitted)
- `urls` — list of job URLs (up to 25)
- `dry_run` — set true if Anthony says "just score it" or "don't generate docs yet"
- `priority` — "high" if Anthony says "prioritize this" or "force high", otherwise "normal"
- `company_notes` — any context Anthony mentions (e.g. "warm intro from Sarah", "they focus on federal grants")

### What to tell Anthony after pushing
"Queued — the workflow will run in ~2 minutes. Results will appear in
`runs/YYYY-MM-DD-HHMM/` in the repo. The dashboard shows scores and links to drafts."

## Output location
After the workflow completes, results are at:
```
runs/YYYY-MM-DD-HHMM/
  dashboard.md           ← scores + links to all drafts
  high-fit/{company}/
    resume.md
    cover-letter.md
    jd-parsed.md
    score-rationale.md
  medium-fit/ ...
  low-fit/ ...

application-tracker.csv  ← cumulative log of all ingested jobs
```

## Repo layout
- `agent.py` — autonomous search orchestrator (do not modify for ingest)
- `src/ingester.py` — URL fetch + Claude JD parsing
- `src/matcher.py` — three-dimension scoring (Fit / Competitiveness / ROI)
- `src/generator.py` — tailored resume + cover letter generation
- `src/run_writer.py` — runs/ directory writer and tracker CSV
- `src/fetchers.py` — job board API clients (autonomous workflow only)
- `src/storage.py` — SQLite persistence (autonomous workflow only)
- `config/preferences.yaml` — job titles, deal-breakers, scoring prefs
- `config/resume.yaml` — Anthony's resume data
- `.github/workflows/job-search.yml` — autonomous 6-hour search
- `.github/workflows/ingest-job-url.yml` — manual URL ingest (push-triggered)
