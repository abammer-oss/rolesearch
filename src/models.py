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
    score: int = Field(ge=0, le=100)
    reasoning: str
    key_matches: list[str]
    gaps: list[str]
    recommendation: str  # "apply" | "maybe" | "skip"


class GeneratedDocuments(BaseModel):
    job_id: str
    job_title: str
    company: str
    tailored_resume: str
    cover_letter: str
    generated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
