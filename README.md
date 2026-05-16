# rolesearch — AI-Powered Job Search Agent

An autonomous agent that searches multiple job boards, scores each posting against
your resume using Claude AI, and generates a tailored resume + cover letter for
your top matches. Runs 24/7 or on-demand.

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure your API key
cp .env.example .env
# Edit .env and set ANTHROPIC_API_KEY

# 3. Fill in your resume and preferences
nano config/resume.yaml
nano config/preferences.yaml

# 4. Run a search
python main.py search
```

## Commands

| Command | Description |
|---|---|
| `python main.py search` | Fetch fresh jobs, score them, generate docs for top matches |
| `python main.py list` | Display top matches from previous runs |
| `python main.py generate <job-id>` | Generate tailored resume + cover letter for a specific job |
| `python main.py daemon` | Run continuously, auto-refreshing every N hours (default: 6) |

## Configuration

### `config/resume.yaml`
Your complete resume in structured YAML. The agent uses this to:
- Match jobs to your actual skills and experience
- Generate tailored resumes that reorder and reword your bullets to fit each role
- Write cover letters grounded in your real achievements

### `config/preferences.yaml`
Your job search requirements:
- **job_titles** — roles you're open to
- **locations** — where you'll work (include `Remote` for remote roles)
- **salary_min** — minimum annual salary (0 = no filter)
- **keywords** — skills that should be present in good matches
- **deal_breakers** — keywords that auto-disqualify a posting
- **min_match_score** — threshold (0–100) to include a job in results (default: 65)
- **auto_generate_top_n** — how many top matches get auto-generated documents (default: 5)

### `.env`
```
ANTHROPIC_API_KEY=sk-ant-...

# Optional — add Adzuna for mainstream job boards
ADZUNA_APP_ID=
ADZUNA_APP_KEY=

# Tune models
MATCH_MODEL=claude-haiku-4-5-20251001   # fast, cheap — for scoring batches
GENERATE_MODEL=claude-sonnet-4-6         # higher quality — for writing docs

# Daemon refresh interval
REFRESH_INTERVAL_HOURS=6
```

## Job Sources

| Source | Type | Auth required |
|---|---|---|
| [Arbeitnow](https://www.arbeitnow.com) | Tech / global | None |
| [Remotive](https://remotive.com) | Remote tech | None |
| [Jobicy](https://jobicy.com) | Remote all-types | None |
| [Adzuna](https://developer.adzuna.com) | Mainstream (US/UK/AU/…) | Free API key |

Add your `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` to `.env` to unlock mainstream job boards.

## Output

Generated documents are saved to `output/<Company>_<JobTitle>/`:
```
output/
  Acme_Corp_Senior_Software_Engineer/
    tailored_resume.md    ← Resume rewritten for this role
    cover_letter.md       ← Personalized cover letter
    job_info.md           ← Job metadata
```

## 24/7 Daemon Mode

```bash
# Run in background with nohup
nohup python main.py daemon &> logs/daemon.log &

# Or with systemd — create /etc/systemd/system/rolesearch.service
```

The daemon refreshes on the configured interval, deduplicates against previous
runs (stored in `rolesearch.db`), and generates documents only for new top matches.

## How Matching Works

1. **Fetch** — pulls jobs from all configured sources, deduplicates by URL hash
2. **Score** — Claude evaluates each job (batches of 8) against your resume and preferences:
   - Score 0–100 based on skill alignment + role fit
   - `apply` (≥75), `maybe` (50–74), `skip` (<50 or deal-breaker)
   - Returns key matches, gaps, and reasoning
3. **Filter** — only jobs above `min_match_score` are shown
4. **Generate** — for the top N matches, Claude writes a tailored resume and cover letter
5. **Persist** — all results stored in SQLite; subsequent runs only process new jobs
