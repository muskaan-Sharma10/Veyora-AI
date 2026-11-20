"""
Shared Memory: CandidatePool
=============================
Defines all data structures that flow through the recruitment pipeline.
The CandidatePool is the single shared state object passed between every agent stage.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional


# ─── Primitive Data Structures ──────────────────────────────────────────────

@dataclass
class CandidateProfile:
    """
    Output of Parser Agent.
    Structured extraction of a candidate's resume.
    """
    id: str
    name: str
    email: str
    phone: str
    skills: List[str]
    experience_years: float
    education: List[Dict[str, str]]       # [{degree, institution, year}]
    certifications: List[str]
    work_experience: List[Dict[str, str]] # [{title, company, duration, description}]
    summary: str
    raw_text: str


@dataclass
class RequirementJustification:
    """
    Per-requirement justification from Matcher Agent.
    Each named job requirement is evaluated individually.
    """
    requirement: str
    met: bool
    evidence: str              # Must reference specific items from the profile
    score_contribution: float  # Points this requirement contributes to total score


@dataclass
class MatchResult:
    """
    Output of Matcher Agent for one candidate.
    Score based solely on professional qualifications.
    """
    candidate_id: str
    score: float                                        # 0–100
    matched_requirements: List[str]
    missing_requirements: List[str]
    per_requirement_justification: List[RequirementJustification]
    overall_assessment: str


@dataclass
class BiasFlag:
    """
    A single bias violation detected by the Bias-Guard Reviewer.
    """
    rationale_text: str         # Exact problematic phrase
    protected_attribute: str    # e.g. "age", "gender", "race"
    explanation: str            # Why this is problematic
    severity: str               # "HIGH" | "MEDIUM" | "LOW"


@dataclass
class BiasReport:
    """
    Output of Bias-Guard Reviewer.
    Summarises all bias findings across all candidates.
    """
    passed: bool
    flags: List[BiasFlag]
    overall_assessment: str
    reviewed_candidates: int


@dataclass
class RankedCandidate:
    """
    Output of Ranker Agent.
    A candidate with their rank, rationale, and bias flags.
    """
    rank: int
    profile: CandidateProfile
    match: MatchResult
    rationale: str          # One-line professional rationale (Ranker Agent)
    bias_flags: List[BiasFlag]  # Filled in by Bias-Guard Agent
    guidance: Optional[Dict[str, object]] = None


@dataclass
class InterviewSlot:
    """
    Output of Scheduler Agent.
    One interview booking for one candidate.
    """
    candidate_name: str
    candidate_id: str
    slot_datetime: str      # "YYYY-MM-DD HH:MM"
    slot_date: str          # "YYYY-MM-DD"
    slot_time: str          # "HH:MM"
    interviewer: str
    interviewer_role: str
    mode: str               # "Video" | "On-site" | "Phone"
    duration_minutes: int
    meeting_link: Optional[str]
    rank: int               # Candidate rank
    score: float            # Candidate score


@dataclass
class PipelineStage:
    """
    Tracks the execution state of a single pipeline stage.
    Used by the Pipeline Coordinator for progress reporting.
    """
    name: str
    status: str             # "pending" | "running" | "complete" | "error"
    started_at: Optional[str]
    completed_at: Optional[str]
    message: str


# ─── Shared Candidate Pool (Central State) ──────────────────────────────────

@dataclass
class CandidatePool:
    """
    The shared state object that flows through the entire pipeline.

    Pipeline flow:
        Parser Agent     → populates: profiles
        Matcher Agent    → populates: matches
        Ranker Agent     → populates: ranked
        Bias-Guard Agent → populates: bias_report, updates ranked[*].bias_flags
        Scheduler Agent  → populates: schedule
    """
    job_id: str
    job_description: str

    # Populated progressively as pipeline runs
    profiles: List[CandidateProfile]  = field(default_factory=list)
    matches:  List[MatchResult]       = field(default_factory=list)
    ranked:   List[RankedCandidate]   = field(default_factory=list)
    bias_report: Optional[BiasReport] = None
    schedule: List[InterviewSlot]     = field(default_factory=list)
    stages:   List[PipelineStage]     = field(default_factory=list)

    def get_profile_by_id(self, candidate_id: str) -> Optional[CandidateProfile]:
        return next((p for p in self.profiles if p.id == candidate_id), None)

    def get_match_by_id(self, candidate_id: str) -> Optional[MatchResult]:
        return next((m for m in self.matches if m.candidate_id == candidate_id), None)

    def to_summary_dict(self) -> dict:
        """Return a lightweight summary suitable for logging."""
        return {
            "job_id": self.job_id,
            "profiles": len(self.profiles),
            "matches": len(self.matches),
            "ranked": len(self.ranked),
            "bias_passed": self.bias_report.passed if self.bias_report else None,
            "scheduled": len(self.schedule),
            "stages": [{"name": s.name, "status": s.status} for s in self.stages],
        }
