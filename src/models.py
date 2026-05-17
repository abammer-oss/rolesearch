from __future__ import annotations

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class PersonalInfo(BaseModel):
    name: str
    email: str
    phone: Optional[str] = None
    location: Optional[str] = None
    linkedin: Optional[str] = None
    github: Optional[str] = None
    website: Optional[str] = None


class Experience(BaseModel):
    title: str
    company: str
    location: Optional[str] = None
    start_date: str
    end_date: Optional[str] = "Present"
    highlights: list[str] = []


class Education(BaseModel):
    degree: str
    institution: str
    year: Optional[str] = None
    gpa: Optional[str] = None
    honors: Optional[str] = None


class Project(BaseModel):
    name: str
    description: str
    technologies: list[str] = []
    url: Optional[str] = None


class Resume(BaseModel):
    personal: PersonalInfo
    summary: str
    skills: dict[str, list[str]]
    experience: list[Experience]
    education: list[Education]
    projects: list[Project] = []
    certifications: list[str] = []


class JobPreferences(BaseModel):
    job_titles: list[str]
    locations: list[str] = ["Remote"]
    job_types: list[str] = ["full-time"]
    industries: list[str] = []
    salary_min: int = 0
    keywords: list[str] = []
    deal_breakers: list[str] = []
    excluded_companies: list[str] = []
    min_match_score: int = 65
    auto_generate_top_n: int = 5
    max_jobs_per_source: int = 50


class JobPosting(BaseModel):
    id: str
    title: str
    company: str
    location: str
    url: str
    description: str
    salary: Optional[str] = None
    job_type: Optional[str] = None
    source: str
    posted_at: Optional[str] = None
    tags: list[str] = []
    remote: bool = False


class MatchResult(BaseModel):
    job_id: str

    # Three-dimension scores (new)
    fit_score: int = Field(ge=0, le=100, default=0)
    competitiveness_score: int = Field(ge=0, le=100, default=0)
    roi_score: int = Field(ge=0, le=100, default=0)

    # Composite score: fit*0.40 + competitiveness*0.35 + roi*0.25
    # Populated by score_batch(); falls back to legacy single score for old DB rows.
    score: int = Field(ge=0, le=100, default=0)

    reasoning: str
    key_matches: list[str]
    gaps: list[str]

    # New recommendation values: apply_now | apply_selectively | outreach_first | track_only | skip
    # Legacy values "apply" / "maybe" are still accepted by display/storage code.
    recommendation: str

    executive_summary: str = ""
    priority_rank: int = 3          # 1=Apply Now, 2=Apply Selectively/Outreach, 3=Track/Skip

    resume_angles: list[str] = []   # top 3 resume angles to emphasise
    risks: list[str] = []           # top 3 risks / gaps to flag
    outreach_strategy: str = ""     # suggested outreach if applicable


class GeneratedDocuments(BaseModel):
    job_id: str
    job_title: str
    company: str
    tailored_resume: str
    cover_letter: str
    generated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
