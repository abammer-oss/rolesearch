"""Claude-powered job matching — three-dimension scoring against the candidate profile."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import anthropic

from .models import JobPosting, JobPreferences, MatchResult, Resume

logger = logging.getLogger(__name__)

MATCH_MODEL = os.getenv("MATCH_MODEL", "claude-sonnet-4-6")


# ── Tool schema ───────────────────────────────────────────────────────────────

_SCORE_TOOL = {
    "name": "score_jobs",
    "description": "Score and evaluate job postings against the candidate profile using three dimensions.",
    "input_schema": {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string"},

                        "fit_score": {
                            "type": "integer", "minimum": 0, "maximum": 100,
                            "description": (
                                "How well this role maps to the candidate's strongest lanes, "
                                "proof points, and target role types. See scoring rubric."
                            ),
                        },
                        "competitiveness_score": {
                            "type": "integer", "minimum": 0, "maximum": 100,
                            "description": (
                                "How likely the candidate is to clear screening and be seen "
                                "as a credible applicant. Penalise hard credential gates, "
                                "scale mismatches, and conventional-pedigree requirements."
                            ),
                        },
                        "roi_score": {
                            "type": "integer", "minimum": 0, "maximum": 100,
                            "description": (
                                "Whether this role is worth applying to this week: salary vs. "
                                "floor, application effort, mission/brand upside, outreach angle, "
                                "probability of advancing."
                            ),
                        },

                        "reasoning": {
                            "type": "string",
                            "description": "2–3 sentences explaining the composite assessment.",
                        },
                        "executive_summary": {
                            "type": "string",
                            "description": (
                                "2–3 sentences written FOR the candidate: what the org does, "
                                "what this role owns day-to-day, and a candid fit verdict "
                                "referencing his actual proof points."
                            ),
                        },
                        "key_matches": {
                            "type": "array", "items": {"type": "string"},
                            "description": "Top 5 specific skills/experiences from his resume that directly match.",
                        },
                        "gaps": {
                            "type": "array", "items": {"type": "string"},
                            "description": "Top 3–4 honest gaps between job requirements and his profile.",
                        },
                        "resume_angles": {
                            "type": "array", "items": {"type": "string"},
                            "description": "Top 3 specific proof points or resume framings to lead with for this role.",
                        },
                        "risks": {
                            "type": "array", "items": {"type": "string"},
                            "description": "Top 3 risks or screening flags to be aware of before applying.",
                        },
                        "outreach_strategy": {
                            "type": "string",
                            "description": (
                                "If outreach is worthwhile: who to contact, what angle to use, "
                                "and what to say. Empty string if direct apply is the right move."
                            ),
                        },
                        "recommendation": {
                            "type": "string",
                            "enum": ["apply_now", "apply_selectively", "outreach_first", "track_only", "skip"],
                            "description": (
                                "apply_now: composite ≥85, strong fit, apply this week. "
                                "apply_selectively: composite 80–84, solid fit, worth applying with tailored materials. "
                                "outreach_first: composite 75–79, good fit but competitive — warm intro before cold apply. "
                                "track_only: composite 65–74, mission fit but meaningful gaps — monitor for better version. "
                                "skip: composite <65, deal-breaker found, or not a genuine fit."
                            ),
                        },
                        "priority_rank": {
                            "type": "integer", "enum": [1, 2, 3],
                            "description": (
                                "1 = apply_now (composite ≥85). "
                                "2 = apply_selectively or outreach_first (composite 75–84). "
                                "3 = track_only or skip."
                            ),
                        },
                    },
                    "required": [
                        "job_id", "fit_score", "competitiveness_score", "roi_score",
                        "reasoning", "executive_summary", "key_matches", "gaps",
                        "resume_angles", "risks", "outreach_strategy",
                        "recommendation", "priority_rank",
                    ],
                },
            }
        },
        "required": ["results"],
    },
}


# ── Candidate profile snapshot ────────────────────────────────────────────────

_CANDIDATE_PROFILE = """\
## Candidate: Anthony Bammer

### Background
Social impact strategy and philanthropy advisory leader. 13+ years of leadership experience.
MBA (UGA Terry College of Business, 2019). CFMVA (Certified Financial Modeling & Valuation Analyst).
MS Exercise Physiology (Life University). Based in Atlanta, GA. Open to remote.
Salary floor: $150K preferred; $130K absolute minimum.

### Hard Proof Points (use these to evaluate real competitiveness)
- **$50M+ in funding initiatives supported** (federal, state, private) across clients 2019–2025
- **$500K competitive FTA federal grant secured** as Executive Director of G1VE Atlanta
- **$100M financial model and capital roadmap** built for deep-tech/space propulsion startup
- **$8.9M blended capital architecture** for 52-bed recovery housing facility (IRR/NPV modeled)
- **$8M rural wastewater infrastructure** — 100% funding secured via USDA + CDBG blended approach
- **0 → 159 patients** cardiac rehabilitation program launched from scratch (Ochsner Rush Health)
- **COO** at Peachtree Surgical: 30% productivity improvement, $75K annual savings
- **Nonprofit Founder + Executive Director** (G1VE Atlanta, 2019–2023): transit + community development
- **Grant Reviewer**, National CASA/GAL Association — evaluates national program proposals
- **Board + advisory leadership**: United Way VIP (Board Leadership Development), GA Giving Leadership Council

### Strongest Lanes (these earn high Fit Scores)
1. Federal funding strategy, grant strategy, capital stack design
2. Philanthropy strategy, social impact strategy, funder engagement
3. Blended finance, capital roadmap advisory
4. Strategic partnerships — public-private, government, multi-agency
5. Nonprofit executive leadership (CDO, VP Development, Executive Director) at growth-stage orgs
6. Healthcare operations / program development / clinical program launch
7. Organizational strategy and operational excellence (COO-level)
8. Community development, transit infrastructure, behavioral health

### Weaker Lanes (these reduce Fit Score and especially Competitiveness Score)
- Major gifts or principal gifts portfolio ownership — no direct evidence of managing donor portfolios
- Pure donor acquisition / individual giving campaigns
- CPA / controller / accounting close / treasury / audit — no credential, not his work
- Investment banking, private equity, M&A transaction work — no pedigree
- MBB or top-tier strategy consulting (McKinsey/BCG/Bain) — no credential
- Managing large teams (20+ direct reports) — no evidence at this scale
- Pure grant writing execution only (he does strategy; not an execution-only grant writer)
- Commission-based or quota-carrying sales roles
- Roles requiring deep domain expertise in: food banking, DAF operations, pharma, Medicaid billing, investment management

### Important calibration notes
- "Fundraising" is NOT automatically a match. Distinguish:
  - Institutional funding / grant strategy → HIGH fit
  - Funder engagement / philanthropy strategy → HIGH fit
  - Major gifts / donor portfolio management → LOW competitiveness
  - Development operations / CRM management → LOW fit
  - Corporate partnerships / BD → MEDIUM fit
- "Finance" is NOT automatically a match. Distinguish:
  - Financial modeling / capital roadmap → HIGH fit (CFMVA certified, proven)
  - CPA / accounting close / controller / treasury → PENALISE — he does not do this
  - IRR/NPV/pro forma analysis → HIGH fit
  - Investment management → LOW fit
- For senior national executive roles: allow high Fit Score if the lane matches,
  but reduce Competitiveness Score if the role requires leading a much larger
  organization than he has directly operated.
- Do NOT overvalue keyword overlap. Score based on genuine evidence of capability.
- Treat 80–84 composite as strong/selective apply range, not weak — he has a hybrid profile
  that competes well in cross-sector and growth-stage contexts.
"""


# ── Prompt builder ────────────────────────────────────────────────────────────

def _prefs_summary(prefs: JobPreferences) -> str:
    parts = [f"Target titles: {', '.join(prefs.job_titles[:8])}"]
    if prefs.deal_breakers:
        parts.append(f"Auto-disqualifiers: {', '.join(prefs.deal_breakers)}")
    return "\n".join(parts)


def _build_prompt(prefs: JobPreferences, batch: list[JobPosting]) -> str:
    jobs_text = "\n\n".join(
        f"[JOB {i + 1}]\nID: {j.id}\nTitle: {j.title}\nCompany: {j.company}\n"
        f"Location: {j.location}\nType: {j.job_type or 'N/A'}\nSalary: {j.salary or 'Not listed'}\n"
        f"Tags: {', '.join(j.tags) if j.tags else 'N/A'}\n"
        f"Description:\n{j.description[:800]}"
        for i, j in enumerate(batch)
    )

    return f"""\
You are a precise career advisor evaluating job postings for a specific candidate.
Score every job using the THREE-DIMENSION framework below. Be calibrated — do not inflate.

{_CANDIDATE_PROFILE}

## Search Preferences
{_prefs_summary(prefs)}

---

## THREE-DIMENSION SCORING FRAMEWORK

### Dimension 1 — FIT SCORE (0–100)
Measures alignment between the job's core responsibilities and the candidate's proven lanes.

High Fit (80–100): Job directly calls for federal funding strategy, philanthropy/social impact
strategy, capital stack design, strategic partnerships, nonprofit executive leadership,
healthcare program development, or operational strategy — and his proof points are direct evidence.

Medium Fit (60–79): Job is adjacent to his lanes but requires significant domain translation,
or his evidence is indirect (e.g., nonprofit strategy → corporate social impact).

Low Fit (0–59): Job is primarily in his weak lanes: major gifts portfolio, CPA/accounting,
investment banking, pure grant writing execution, large team management, or requires
specific domain expertise he lacks.

Penalise: roles where he only matches the mission but not the core function.

### Dimension 2 — COMPETITIVENESS SCORE (0–100)
Measures how likely he is to clear screening and be viewed as a credible finalist.

Hard gates that REDUCE this score (−10 to −25 each — calibrate by how explicitly required):
- Direct major gifts or principal gifts portfolio ownership explicitly required (−15 to −20)
  NOTE: If the role is CDO/VP Development at a growth-stage or mid-size nonprofit and major gifts
  is listed but not the *sole* focus, apply only a modest penalty (−10). His philanthropy strategy,
  funder engagement, and federal funding track record are directly transferable to many CDO roles.
- CPA, CFA, controller, or accounting credential explicitly required (−20)
- Investment banking / private equity / M&A pedigree explicitly required (−20)
- MBB / top-tier consulting credential explicitly required (−15)
- 15+ years at-level executive experience *explicitly* required (−10 to −15)
- Specific industry domain required with no transferable path: food banking operations,
  DAF administration, pharma clinical, Medicaid billing (−15 to −20)
- Managing teams of 20+ direct reports explicitly required (−10)

Positive signals that INCREASE this score:
- Cross-sector / hybrid profiles explicitly valued
- Growth-stage, turnaround, or capacity-building org (his consulting/advisory model fits well)
- Federal funding, philanthropy strategy, or capital advisory is the primary function
- Mission-driven org in healthcare, community development, behavioral health, or social impact
- Atlanta-based or remote (reduces geographic friction)
- CFMVA or financial modeling explicitly valued
- Role is newly created or org is expanding (less entrenched incumbent preference)

### Dimension 3 — APPLICATION ROI SCORE (0–100)
Measures whether applying this week is worth the time investment.

Increase ROI when:
- Salary ≥$150K (or unknown but role-type typically pays this)
- Quick apply / standard resume+cover letter (vs. long essay application)
- Mission/brand upside is significant (e.g., well-known foundation, national org)
- Warm intro or direct outreach is possible
- His tailored materials from existing jobs can be adapted quickly
- Strong upside: title advancement, visibility, or network value

Decrease ROI when:
- Salary explicitly below $130K or clearly below his floor
- Long, multi-stage application process (essays, references, assessments)
- Highly competitive role with low competitiveness score
- Role requires significant upskilling before interview readiness
- Location is a hard requirement in a city he's not targeting

---

## RECOMMENDATION MAPPING
apply_now:        composite ≥85 — apply this week, high priority
apply_selectively: composite 80–84 — worth applying with tailored materials
outreach_first:   composite 75–79 — warm intro or LinkedIn message before cold apply
track_only:       composite 65–74 — monitor; apply only if better version of role appears
skip:             composite <65, deal-breaker present, or not a genuine fit

Composite formula: fit_score × 0.40 + competitiveness_score × 0.35 + roi_score × 0.25
(You do not need to compute this — just provide the three sub-scores and recommendation.)

---

## DEAL-BREAKERS (auto-skip)
Any role containing: {', '.join(prefs.deal_breakers)}
→ recommendation="skip", all scores ≤20

---

## JOBS TO EVALUATE

{jobs_text}

---

Call score_jobs with a result for EVERY job above. Be honest and specific.
Reference his actual proof points (the dollar figures, the program launches, the COO role)
when explaining fit — do not use generic language.
"""


# ── Scoring logic ─────────────────────────────────────────────────────────────

def _composite(fit: int, comp: int, roi: int) -> int:
    return round(fit * 0.40 + comp * 0.35 + roi * 0.25)


def score_batch(
    resume: Resume,
    prefs: JobPreferences,
    jobs: list[JobPosting],
    client: anthropic.Anthropic,
) -> list[MatchResult]:
    prompt = _build_prompt(prefs, jobs)
    try:
        response = client.messages.create(
            model=MATCH_MODEL,
            max_tokens=6000,
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
                    fit   = int(r.get("fit_score", 0))
                    comp  = int(r.get("competitiveness_score", 0))
                    roi   = int(r.get("roi_score", 0))
                    r["score"] = _composite(fit, comp, roi)
                    results.append(MatchResult(**r))
                except Exception as e:
                    logger.warning("Could not parse match result %s: %s", r.get("job_id"), e)
            return results
    return []


def match_jobs(
    resume: Resume,
    prefs: JobPreferences,
    jobs: list[JobPosting],
    client: anthropic.Anthropic,
    batch_size: int = 5,
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
    filtered.sort(key=lambda r: (r.priority_rank, -r.score))
    logger.info(
        "matched %d / %d jobs above score threshold %d",
        len(filtered), len(all_results), prefs.min_match_score,
    )
    return filtered
