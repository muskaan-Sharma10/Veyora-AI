import logging
from agents.legacy.base_agent import BaseAgent
from memory.candidate_pool import CandidateProfile, MatchResult, RequirementJustification

logger = logging.getLogger(__name__)


class MatcherAgent(BaseAgent):
    """
    Matcher Agent: Scores each CandidateProfile against the Job Description requirements.
    Provides per-requirement justification with named evidence.
    """

    def __init__(self):
        super().__init__(
            name="Matcher Agent",
            description="Scores candidate profiles against job description requirements with justification",
        )

    def run(self, profile: CandidateProfile, job_description: str) -> MatchResult:
        """
        Score a candidate profile against the job description.
        Returns a MatchResult with score and per-requirement justification.
        """
        logger.info(f"[MatcherAgent] Matching: {profile.name}")

        profile_summary = f"""Candidate profile
Total Experience: {profile.experience_years} years
Skills: {', '.join(profile.skills)}
Certifications: {', '.join(profile.certifications) if profile.certifications else 'None'}
Education: {', '.join([f"{e.get('degree')} from {e.get('institution')}" for e in profile.education]) if profile.education else 'Not specified'}
Work History: {'; '.join([f"{w.get('title')} at {w.get('company')} ({w.get('duration')})" for w in profile.work_experience]) if profile.work_experience else 'Not specified'}
Summary: {profile.summary}"""

        prompt = f"""You are a senior technical recruiter evaluating a candidate for a job opening.

JOB DESCRIPTION:
--------------------
{job_description}
--------------------

CANDIDATE PROFILE:
--------------------
{profile_summary}
--------------------

Carefully evaluate this candidate against EVERY explicit requirement in the job description.
Return a JSON object:
{{
    "score": 78.5,
    "matched_requirements": ["5+ years Python experience", "Docker"],
    "missing_requirements": ["AWS certification"],
    "per_requirement_justification": [
        {{
            "requirement": "5+ years Python experience",
            "met": true,
            "evidence": "Candidate has 7 years of Python development experience as Senior Dev at TechCorp",
            "score_contribution": 20.0
        }},
        {{
            "requirement": "AWS cloud services",
            "met": false,
            "evidence": "AWS not mentioned in skills or work history",
            "score_contribution": 0.0
        }}
    ],
    "overall_assessment": "Strong candidate with solid Python background but missing AWS experience..."
}}

SCORING RULES:
- score is 0 to 100, based ONLY on professional qualifications
- Each requirement from the JD must be evaluated individually with named evidence
- score_contribution: how much this requirement adds to the total (all contributions sum roughly to score)
- Do NOT factor in personal attributes: age, gender, ethnicity, name, appearance, marital status, religion
- Be specific: reference exact skills, years, job titles, companies from the profile
- matched_requirements: only requirements that are genuinely met"""

        try:
            data = self.call_llm_json(prompt)

            justifications = []
            for j in data.get("per_requirement_justification", []):
                justifications.append(
                    RequirementJustification(
                        requirement=j.get("requirement", ""),
                        met=bool(j.get("met", False)),
                        evidence=j.get("evidence", ""),
                        score_contribution=float(j.get("score_contribution", 0)),
                    )
                )

            result = MatchResult(
                candidate_id=profile.id,
                score=min(100.0, max(0.0, float(data.get("score", 0)))),
                matched_requirements=data.get("matched_requirements", []),
                missing_requirements=data.get("missing_requirements", []),
                per_requirement_justification=justifications,
                overall_assessment=data.get("overall_assessment", ""),
            )
            logger.info(f"[MatcherAgent] {profile.name}: score={result.score:.1f}")
            return result

        except Exception as e:
            logger.error(f"[MatcherAgent] Match failed for {profile.name}: {e}")
            raise RuntimeError(f"Could not evaluate candidate profile '{profile.id}'.") from e
