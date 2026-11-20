import uuid
import logging
from typing import Dict, Callable, Optional
from datetime import datetime

from agents.legacy.parser_agent import ParserAgent
from agents.legacy.matcher_agent import MatcherAgent
from agents.legacy.ranker_agent import RankerAgent
from agents.legacy.bias_guard_agent import BiasGuardAgent
from agents.legacy.scheduler_agent import SchedulerAgent
from agents.legacy.career_coach_agent import CareerCoachAgent
from memory.candidate_pool import CandidatePool, PipelineStage

logger = logging.getLogger(__name__)


class PipelineCoordinator:
    """
    Pipeline Coordinator: Orchestrates the sequential execution of all agents.
    Maintains a shared CandidatePool that is passed through every stage.

    Pipeline:
        Parser → Matcher → Ranker → Bias-Guard → Scheduler
    """

    STAGES = [
        "Parsing Resumes",
        "Matching Candidates",
        "Ranking Candidates",
        "Preparing Interview Guidance",
        "Bias Review",
        "Scheduling Interviews",
    ]

    def __init__(self):
        logger.info("[Coordinator] Initializing pipeline agents...")
        self.parser = ParserAgent()
        self.matcher = MatcherAgent()
        self.ranker = RankerAgent()
        self.career_coach = CareerCoachAgent()
        self.bias_guard = BiasGuardAgent()
        self.scheduler = SchedulerAgent()
        logger.info("[Coordinator] All agents ready.")

    def run_pipeline(
        self,
        job_description: str,
        resume_texts: Dict[str, str],
        top_n: int = 5,
        progress_callback: Optional[Callable] = None,
    ) -> CandidatePool:
        """
        Execute the full pipeline sequentially.

        Args:
            job_description: Full text of the job description
            resume_texts: Dict of {filename: extracted_text}
            top_n: Number of top candidates to schedule (3-5)
            progress_callback: Callable(stage_name, status, message, pool)

        Returns:
            Completed CandidatePool with all results
        """
        pool = CandidatePool(
            job_id=str(uuid.uuid4()),
            job_description=job_description,
        )

        def emit(stage_name: str, status: str, message: str = ""):
            ts = datetime.now().isoformat()
            stage = PipelineStage(
                name=stage_name,
                status=status,
                started_at=ts if status == "running" else None,
                completed_at=ts if status in ("complete", "error") else None,
                message=message,
            )
            pool.stages.append(stage)
            logger.info(f"[Coordinator] Stage '{stage_name}': {status} — {message}")
            if progress_callback:
                try:
                    progress_callback(stage_name, status, message, pool)
                except Exception:
                    logger.exception("[Coordinator] Progress callback failed")

        # ── Stage 1: Parse ──────────────────────────────────────────────────
        emit("Parsing Resumes", "running", f"Parsing {len(resume_texts)} resume(s)...")
        parse_failures = 0
        for filename, text in resume_texts.items():
            if not text.strip():
                logger.warning(f"[Coordinator] Empty text for {filename}, skipping")
                parse_failures += 1
                continue
            try:
                profile = self.parser.run(text, filename)
                pool.profiles.append(profile)
            except Exception as e:
                logger.error(f"[Coordinator] Parser failed for {filename}: {e}")
                parse_failures += 1
        parse_message = f"Parsed {len(pool.profiles)} profile(s)"
        if parse_failures:
            parse_message += f"; {parse_failures} resume(s) could not be parsed"
        emit("Parsing Resumes", "complete", parse_message)

        if not pool.profiles:
            raise RuntimeError("No profiles could be parsed. Check resume files and API key.")

        # ── Stage 2: Match ──────────────────────────────────────────────────
        emit("Matching Candidates", "running", f"Scoring {len(pool.profiles)} candidate(s) against job requirements...")
        match_failures = 0
        for profile in pool.profiles:
            try:
                match_result = self.matcher.run(profile, job_description)
                pool.matches.append(match_result)
            except Exception as e:
                logger.error(f"[Coordinator] Matcher failed for {profile.name}: {e}")
                match_failures += 1
        match_message = f"Scored {len(pool.matches)} candidate(s)"
        if match_failures:
            match_message += f"; {match_failures} candidate(s) could not be evaluated"
        emit("Matching Candidates", "complete", match_message)
        if not pool.matches:
            raise RuntimeError("No candidates could be evaluated against the job description.")

        # ── Stage 3: Rank ───────────────────────────────────────────────────
        emit("Ranking Candidates", "running", "Ordering candidates and generating rationales...")
        try:
            pool.ranked = self.ranker.run(pool.profiles, pool.matches)
            emit("Ranking Candidates", "complete", f"Ranked {len(pool.ranked)} candidate(s)")
        except Exception as e:
            emit("Ranking Candidates", "error", str(e))
            raise

        # ── Stage 4: Interview and improvement guidance ───────────────────
        emit("Preparing Interview Guidance", "running", "Preparing role-specific interview questions and skill-gap notes...")
        try:
            for candidate in pool.ranked[:max(1, min(5, top_n))]:
                candidate.guidance = self.career_coach.run(
                    candidate.profile,
                    job_description,
                    candidate.match,
                )
            emit("Preparing Interview Guidance", "complete", "Interview and improvement suggestions are ready")
        except Exception as e:
            emit("Preparing Interview Guidance", "error", str(e))
            raise

        # ── Stage 5: Bias Guard ─────────────────────────────────────────────
        emit("Bias Review", "running", "Auditing rankings for protected attribute bias...")
        try:
            pool.bias_report = self.bias_guard.run(pool.ranked)
            status_msg = (
                "Automated checks found no potential flags; human review is still required"
                if pool.bias_report.passed
                else f"{len(pool.bias_report.flags)} potential flag(s) detected — human review required"
            )
            emit("Bias Review", "complete", status_msg)
        except Exception as e:
            emit("Bias Review", "error", str(e))
            raise

        # ── Stage 6: Schedule ───────────────────────────────────────────────
        emit("Scheduling Interviews", "running", f"Assigning interview slots to top {top_n} candidate(s)...")
        try:
            pool.schedule = self.scheduler.run(pool.ranked, top_n=top_n)
            emit("Scheduling Interviews", "complete", f"Scheduled {len(pool.schedule)} interview(s)")
        except Exception as e:
            emit("Scheduling Interviews", "error", str(e))
            raise

        logger.info(f"[Coordinator] Pipeline complete. Job ID: {pool.job_id}")
        return pool
