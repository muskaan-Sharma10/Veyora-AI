import logging
from typing import List
from agents.legacy.base_agent import BaseAgent
from memory.candidate_pool import RankedCandidate, BiasFlag, BiasReport

logger = logging.getLogger(__name__)

# Comprehensive list of protected attribute keywords for quick pre-screening
PROTECTED_KEYWORDS = [
    "age", "young", "old", "senior citizen", "millennial", "gen z", "boomer",
    "gender", "male", "female", "man", "woman", "boy", "girl", "he ", "she ",
    "race", "racial", "white", "black", "asian", "hispanic", "latino", "latina",
    "african", "caucasian", "european", "middle eastern", "indian descent",
    "ethnicity", "ethnic", "nationality", "national origin", "immigrant", "foreign",
    "religion", "religious", "christian", "muslim", "jewish", "hindu", "buddhist",
    "disability", "disabled", "handicap", "impairment",
    "marital", "married", "single", "divorced", "spouse",
    "pregnant", "pregnancy", "maternity", "paternity",
    "sexual orientation", "gay", "lesbian", "lgbtq",
    "accent", "appearance", "looks", "attractive",
]


class BiasGuardAgent(BaseAgent):
    """
    Bias-Guard Reviewer: Audits the ranked candidate list to ensure no protected
    attributes influenced the ranking or rationales. Flags violations with explanation.
    """

    def __init__(self):
        super().__init__(
            name="Bias-Guard Reviewer",
            description="Audits rankings and rationales for protected attribute bias",
        )

    def run(self, ranked_candidates: List[RankedCandidate]) -> BiasReport:
        """
        Audit all ranked candidates for bias. Returns a BiasReport.
        Updates each RankedCandidate's bias_flags in place.
        """
        logger.info(f"[BiasGuardAgent] Auditing {len(ranked_candidates)} candidates")
        all_flags: List[BiasFlag] = []

        for candidate in ranked_candidates:
            flags = self._audit_candidate(candidate)
            candidate.bias_flags = flags
            all_flags.extend(flags)

        passed = len(all_flags) == 0

        if passed:
            overall = (
                f"Nothing was flagged across {len(ranked_candidates)} resumes. "
                "This check can miss things, so please review the notes yourself."
            )
        else:
            high_severity = sum(1 for f in all_flags if f.severity == "HIGH")
            overall = (
                f"{len(all_flags)} note(s) may need attention "
                f"across {len(ranked_candidates)} resumes ({high_severity} urgent). "
                "Please review them before deciding on next steps."
            )

        logger.info(f"[BiasGuardAgent] Audit complete. Passed: {passed}, Flags: {len(all_flags)}")
        return BiasReport(
            passed=passed,
            flags=all_flags,
            overall_assessment=overall,
            reviewed_candidates=len(ranked_candidates),
        )

    def _audit_candidate(self, candidate: RankedCandidate) -> List[BiasFlag]:
        """Audit a single candidate's rationale and justifications for bias."""
        flags: List[BiasFlag] = []

        # Compile all text to audit
        texts_to_audit = [candidate.rationale]
        for j in candidate.match.per_requirement_justification:
            texts_to_audit.append(j.evidence)
        combined_text = " ".join(texts_to_audit)

        # Quick keyword pre-screen
        combined_lower = combined_text.lower()
        keyword_hits = [kw for kw in PROTECTED_KEYWORDS if kw in combined_lower]

        # LLM semantic audit
        justification_texts = [f"- {j.requirement}: {j.evidence}" for j in candidate.match.per_requirement_justification[:5]]
        justification_block = "\n".join(justification_texts)

        prompt = f"""You are a bias auditor specializing in employment discrimination law. Review the following recruitment text.

Candidate Rank: #{candidate.rank}
Ranking Rationale: "{candidate.rationale}"
Score Justifications:
{justification_block}
Keyword Flags (pre-screened): {keyword_hits if keyword_hits else 'None'}

Determine if any text contains explicit or implicit reference to protected attributes:
- Age, gender, sex, race, ethnicity, religion, national origin
- Disability, marital status, pregnancy, sexual orientation
- Physical appearance, name-based assumptions about ethnicity/gender
- Socioeconomic background proxies

Return JSON:
{{
    "has_bias": false,
    "flags": [
        {{
            "text": "exact problematic phrase",
            "protected_attribute": "gender",
            "explanation": "This phrase implies gender preference which violates EEOC guidelines",
            "severity": "HIGH"
        }}
    ]
}}

If no genuine bias found, return: {{"has_bias": false, "flags": []}}
Only flag genuine bias. Severity levels: HIGH (direct reference), MEDIUM (implicit/indirect), LOW (borderline/contextual).
Do NOT flag normal professional language about skills, experience, or qualifications."""

        try:
            data = self.call_llm_json(prompt)
            if data.get("has_bias", False):
                for f in data.get("flags", []):
                    flags.append(
                        BiasFlag(
                            rationale_text=f.get("text", ""),
                            protected_attribute=f.get("protected_attribute", ""),
                            explanation=f.get("explanation", ""),
                            severity=f.get("severity", "MEDIUM"),
                        )
                    )
        except Exception as e:
            logger.error(f"[BiasGuardAgent] LLM audit failed for {candidate.profile.name}: {e}")
            raise RuntimeError(
                f"Bias review could not be completed for candidate #{candidate.rank}."
            ) from e

        if flags:
            logger.warning(f"[BiasGuardAgent] Flagged {len(flags)} issue(s) for candidate #{candidate.rank}: {candidate.profile.name}")

        return flags
