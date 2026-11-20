import logging
from typing import List
from agents.legacy.base_agent import BaseAgent
from memory.candidate_pool import CandidateProfile, MatchResult, RankedCandidate

logger = logging.getLogger(__name__)


class RankerAgent(BaseAgent):
    """
    Ranker Agent: Sorts matched candidates by score and writes a one-line rationale
    for each candidate based solely on professional qualifications.
    """

    def __init__(self):
        super().__init__(
            name="Ranker Agent",
            description="Orders candidates by score and generates per-candidate rationale",
        )

    def run(
        self,
        profiles: List[CandidateProfile],
        matches: List[MatchResult],
    ) -> List[RankedCandidate]:
        """
        Sort candidates by match score and generate a professional rationale for each.
        """
        logger.info(f"[RankerAgent] Ranking {len(matches)} candidates")

        # Build profile lookup
        profile_map = {p.id: p for p in profiles}

        # Sort by score descending
        sorted_matches = sorted(matches, key=lambda m: m.score, reverse=True)

        ranked_list: List[RankedCandidate] = []
        for rank_index, match in enumerate(sorted_matches, start=1):
            profile = profile_map.get(match.candidate_id)
            if not profile:
                continue

            rationale = self._generate_rationale(profile, match, rank_index)
            ranked_list.append(
                RankedCandidate(
                    rank=rank_index,
                    profile=profile,
                    match=match,
                    rationale=rationale,
                    bias_flags=[],
                )
            )
            logger.info(f"[RankerAgent] #{rank_index}: {profile.name} ({match.score:.1f}) — {rationale[:60]}...")

        return ranked_list

    def _generate_rationale(self, profile: CandidateProfile, match: MatchResult, rank: int) -> str:
        """Generate a concise, unbiased one-line rationale for a candidate's rank."""
        matched_str = ", ".join(match.matched_requirements[:3]) if match.matched_requirements else "some requirements"
        missing_str = ", ".join(match.missing_requirements[:2]) if match.missing_requirements else "none"

        prompt = f"""You are an HR professional writing a brief ranking justification for a candidate.

Candidate Details:
- Rank: #{rank} out of multiple candidates
- Match Score: {match.score:.1f}/100
- Years Experience: {profile.experience_years}
- Key Skills: {', '.join(profile.skills[:8])}
- Met Requirements: {matched_str}
- Missing Requirements: {missing_str}
- Overall Assessment: {match.overall_assessment[:200]}

Write exactly ONE professional sentence (20-30 words) explaining why this candidate received rank #{rank}.

STRICT RULES:
- Base the rationale ONLY on professional qualifications, skills, years of experience, and technical competencies
- Do NOT mention: name origin, gender, age, ethnicity, nationality, religion, appearance, or any personal characteristics
- Be specific: reference actual skills and experience metrics
- Do NOT start with the candidate's name
- Return ONLY the sentence, nothing else"""

        try:
            rationale = self.call_llm(prompt).strip().strip('"').strip("'").strip()
            # Remove any leading/trailing quotes or punctuation artifacts
            if rationale.startswith(("'", '"')):
                rationale = rationale[1:]
            if rationale.endswith(("'", '"')):
                rationale = rationale[:-1]
            return rationale
        except Exception as e:
            logger.error(f"[RankerAgent] Rationale generation failed: {e}")
            return f"Ranked #{rank} with score {match.score:.1f}/100 based on matching {len(match.matched_requirements)} key requirements."
