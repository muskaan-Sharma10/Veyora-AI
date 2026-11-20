import logging
from typing import Dict

from agents.legacy.base_agent import BaseAgent
from memory.candidate_pool import CandidateProfile, MatchResult

logger = logging.getLogger(__name__)


class CareerCoachAgent(BaseAgent):
    """Creates practical, role-specific interview and improvement guidance."""

    def __init__(self):
        super().__init__(
            name="Career Coach",
            description="Suggests interview questions, skill practice, and resume improvements",
        )

    def run(
        self,
        profile: CandidateProfile,
        job_description: str,
        match: MatchResult,
    ) -> Dict[str, object]:
        prompt = f"""Use only the resume details and role description below. Give practical, respectful guidance.

ROLE DESCRIPTION:
{job_description}

RESUME SUMMARY:
{profile.summary}
Skills: {', '.join(profile.skills)}
Experience: {profile.experience_years} years
Missing or unclear role requirements: {', '.join(match.missing_requirements)}

Return JSON with exactly these fields:
{{
  "gap_summary": "One short, evidence-based summary of missing or unclear role skills",
  "learning_steps": ["Three practical steps to build or demonstrate relevant skills"],
  "resume_improvements": ["Three specific ways to improve the resume for this role"],
  "interview_questions": ["Five role-specific questions that check skills and experience"]
}}

Rules:
- Do not assume a skill is absent just because the resume is brief; say unclear when appropriate.
- Do not recommend buying a course or invent facts about the candidate.
- Keep suggestions specific, actionable, and based on role requirements.
- Do not mention protected or personal characteristics."""
        try:
            data = self.call_llm_json(prompt)
            return {
                "gap_summary": str(data.get("gap_summary", "")),
                "learning_steps": self._string_list(data.get("learning_steps")),
                "resume_improvements": self._string_list(data.get("resume_improvements")),
                "interview_questions": self._string_list(data.get("interview_questions")),
            }
        except Exception as exc:
            logger.exception("Could not prepare career guidance for profile %s", profile.id)
            raise RuntimeError("We couldn't prepare interview and improvement suggestions.") from exc

    @staticmethod
    def _string_list(value):
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if isinstance(item, str) and item.strip()]
