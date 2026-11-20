"""
Recruiter review pipeline.
==========================
Runs the seven agents in a background thread with stage reporting, and
persists each candidate's analysis so shortlisting and comparison survive a
page reload.
"""

import json
import logging
from typing import Any, Dict, List

from agents.platform_agents import (
    CompatibilityScoringAgent,
    InterviewStrategistAgent,
    JobDescriptionAnalyst,
    RecruitmentSchedulingAgent,
    ResumeIntelligenceAgent,
    ScreeningDecisionAgent,
    SkillGapCoachAgent,
    ai_mode,
)
from db import connect, new_id
from services.jobstore import add_stage, fail_review, finish_review
from services.scoring import normalize_job

logger = logging.getLogger(__name__)


def _insert_application(connection, application: Dict[str, Any]) -> None:
    connection.execute(
        """INSERT INTO applications (id, job_id, recruiter_id, resume_id, review_id, name, email,
                phone, filename, score, status, analysis)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            application["id"],
            application["job_id"],
            application["recruiter_id"],
            application["resume_id"],
            application["review_id"],
            application["name"],
            application["email"],
            application["phone"],
            application["filename"],
            application["score"],
            application["status"],
            json.dumps(application["analysis"]),
        ),
    )


def run_review(
    db_path: str,
    review_id: str,
    owner_id: int,
    job_id: str,
    job: Dict[str, Any],
    resumes: List[Dict[str, str]],
) -> None:
    """Execute the full review and record every stage. Safe to call from a thread."""
    connection = None
    try:
        connection = connect(db_path)
        mode = ai_mode()

        add_stage(
            review_id, "Validate inputs", "complete",
            f"{len(resumes)} resume(s) and a job description validated.",
        )

        # 2. Job description analysis
        add_stage(review_id, "Analyse job description", "running", "Extracting requirements…")
        analyst = JobDescriptionAnalyst()
        analysed_job = analyst.run(job.get("description", ""), job.get("title", ""))
        # Explicit recruiter input always wins over extracted values.
        if job.get("required_skills"):
            analysed_job["required_skills"] = job["required_skills"]
        if job.get("preferred_skills"):
            analysed_job["preferred_skills"] = [
                skill for skill in job["preferred_skills"]
                if skill not in analysed_job["required_skills"]
            ]
        if job.get("min_years"):
            analysed_job["min_years"] = float(job["min_years"])
        if job.get("title"):
            analysed_job["title"] = job["title"]
        analysed_job = normalize_job(analysed_job)
        connection.execute(
            "UPDATE jobs SET title = ?, required_skills = ?, preferred_skills = ?, min_years = ? WHERE id = ?",
            (
                analysed_job["title"],
                json.dumps(analysed_job["required_skills"]),
                json.dumps(analysed_job["preferred_skills"]),
                analysed_job["min_years"],
                job_id,
            ),
        )
        connection.commit()
        add_stage(
            review_id, "Analyse job description", "complete",
            f"{len(analysed_job['required_skills'])} required and "
            f"{len(analysed_job['preferred_skills'])} preferred skills identified.",
        )

        # 3. Resume parsing
        parser = ResumeIntelligenceAgent()
        profiles: List[Dict[str, Any]] = []
        add_stage(review_id, "Parse resumes", "running", f"Reading {len(resumes)} resume(s)…")
        for resume in resumes:
            try:
                profile = parser.run(resume["text"], resume["filename"])
                profile["filename"] = resume["filename"]
                profiles.append({"profile": profile, "resume": resume})
                add_stage(
                    review_id, "Parse resumes", "progress",
                    f"Parsed {resume['filename']} — {profile.get('name') or 'candidate'} "
                    f"({len(profile['technical_skills'])} skills).",
                    filename=resume["filename"],
                )
            except Exception as exc:
                logger.exception("Resume parsing failed for %s", resume["filename"])
                add_stage(
                    review_id, "Parse resumes", "warning",
                    f"{resume['filename']}: {exc}",
                    filename=resume["filename"],
                )
        if not profiles:
            raise RuntimeError("None of the uploaded resumes could be parsed.")
        add_stage(review_id, "Parse resumes", "complete", f"{len(profiles)} resume(s) parsed.")

        # 4. Compatibility scoring
        scorer = CompatibilityScoringAgent()
        add_stage(review_id, "Score compatibility", "running", "Applying the weighted rubric…")
        scored = []
        for entry in profiles:
            report = scorer.run(entry["profile"], analysed_job)
            scored.append({**entry, "report": report})
            add_stage(
                review_id, "Score compatibility", "progress",
                f"{entry['profile'].get('name') or entry['resume']['filename']}: {report['score']}/100 "
                f"({report['grade']}).",
                filename=entry["resume"]["filename"],
            )
        add_stage(review_id, "Score compatibility", "complete", "Compatibility scores calculated.")

        # 5. Skill gaps and resume coaching
        coach = SkillGapCoachAgent()
        add_stage(review_id, "Analyse skill gaps", "running", "Building gap analysis…")
        for entry in scored:
            entry["gaps"] = coach.run(entry["profile"], analysed_job, entry["report"])
        add_stage(
            review_id, "Analyse skill gaps", "complete",
            f"Gap analysis ready for {len(scored)} candidate(s).",
        )

        # 6. Interview plan
        strategist = InterviewStrategistAgent()
        add_stage(review_id, "Generate interview questions", "running", "Preparing interview plans…")
        for entry in scored:
            entry["questions"] = strategist.run(
                entry["profile"], analysed_job, entry["report"], entry["gaps"].get("gaps", [])
            )
        add_stage(
            review_id, "Generate interview questions", "complete",
            "Interview questions and critical areas are ready.",
        )

        # 7. Screening decision — the agent applies the shortlist rule itself
        decider = ScreeningDecisionAgent()
        threshold = decider.threshold()
        add_stage(
            review_id, "AI screening decision", "running",
            f"Applying the {threshold:.0f}/100 shortlist rule…",
        )
        counts = {"shortlisted": 0, "rejected": 0}
        for entry in scored:
            decision = decider.run(
                entry["profile"], analysed_job, entry["report"], entry["gaps"].get("gaps", [])
            )
            entry["decision"] = decision
            counts[decision["decision"]] = counts.get(decision["decision"], 0) + 1
            add_stage(
                review_id, "AI screening decision", "progress",
                f"{entry['profile'].get('name') or entry['resume']['filename']}: "
                f"{decision['decision']} ({decision['score']:g}/100 vs {threshold:g}).",
                filename=entry["resume"]["filename"],
            )
        add_stage(
            review_id, "AI screening decision", "complete",
            f"{counts['shortlisted']} shortlisted and {counts['rejected']} rejected "
            f"using the {threshold:.0f}/100 rule.",
        )

        # 8. Persist candidate records (status comes from the screening decision)
        candidates = []
        for entry in scored:
            application_id = new_id()
            profile = entry["profile"]
            analysis = {
                "profile": profile,
                "compatibility": entry["report"],
                "gaps": entry["gaps"],
                "questions": entry["questions"],
                "screening": entry["decision"],
                "ai_mode": mode,
                "filename": entry["resume"]["filename"],
            }
            application = {
                "id": application_id,
                "job_id": job_id,
                "recruiter_id": owner_id,
                "resume_id": entry["resume"].get("resume_id", ""),
                "review_id": review_id,
                "name": profile.get("name") or entry["resume"]["filename"],
                "email": profile.get("email") or "",
                "phone": profile.get("phone") or "",
                "filename": entry["resume"]["filename"],
                "score": entry["report"]["score"],
                "status": entry["decision"]["decision"],
                "analysis": analysis,
            }
            _insert_application(connection, application)
            candidates.append({
                "application_id": application_id,
                "name": application["name"],
                "email": application["email"],
                "phone": application["phone"],
                "filename": application["filename"],
                "score": entry["report"]["score"],
                "grade": entry["report"]["grade"],
                "status": entry["decision"]["decision"],
                **analysis,
            })
        connection.commit()

        candidates.sort(key=lambda item: item["score"], reverse=True)
        for index, candidate in enumerate(candidates, start=1):
            candidate["rank"] = index

        # 9. Scheduling agent reserves an interview slot for every shortlisted candidate
        shortlisted = [candidate for candidate in candidates if candidate["status"] == "shortlisted"]
        scheduled = []
        add_stage(
            review_id, "Schedule interviews", "running",
            "Reserving interview slots for shortlisted candidates…",
        )
        if shortlisted:
            busy = [
                dict(row) for row in connection.execute(
                    "SELECT date, start_time, end_time FROM interview_slots "
                    "WHERE recruiter_id = ? AND status != 'cancelled'",
                    (owner_id,),
                ).fetchall()
            ]
            for slot in RecruitmentSchedulingAgent().auto_schedule(shortlisted, analysed_job, busy):
                slot_id = new_id()
                connection.execute(
                    """INSERT INTO interview_slots (id, recruiter_id, job_id, application_id,
                            candidate_name, candidate_email, date, start_time, end_time, timezone,
                            interviewer, mode, location, status, instructions)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        slot_id, owner_id, job_id, slot["application_id"], slot["candidate_name"],
                        slot["candidate_email"], slot["date"], slot["start_time"], slot["end_time"],
                        slot["timezone"], slot["interviewer"], slot["mode"], "", "reserved",
                        slot["instructions"],
                    ),
                )
                scheduled.append({**slot, "slot_id": slot_id})
            connection.commit()
        add_stage(
            review_id, "Schedule interviews", "complete",
            f"{len(scheduled)} interview slot(s) reserved for shortlisted candidates."
            if scheduled else "No shortlisted candidates, so no interviews were scheduled.",
        )

        add_stage(review_id, "Save results", "running", "Saving the review…")
        result = {
            "review_id": review_id,
            "job": analysed_job,
            "job_id": job_id,
            "candidates": candidates,
            "screening": {
                "threshold": threshold,
                "rule": (
                    f"Candidates scoring {threshold:.0f}/100 or above are shortlisted; "
                    f"everyone below {threshold:.0f} is rejected automatically."
                ),
                "shortlisted": counts["shortlisted"],
                "rejected": counts["rejected"],
            },
            "scheduled_interviews": scheduled,
            "ai_mode": mode,
        }
        connection.execute(
            "UPDATE reviews SET status = 'complete', result = ?, ai_mode = ? WHERE id = ?",
            (json.dumps(result), mode, review_id),
        )
        connection.commit()
        add_stage(
            review_id, "Save results", "complete",
            f"Saved {len(candidates)} candidate record(s) and {len(scheduled)} interview slot(s).",
        )
        finish_review(review_id, result)
    except Exception as exc:
        logger.exception("Review %s failed", review_id)
        message = str(exc) or "The review could not be completed."
        add_stage(review_id, "Review failed", "error", message)
        fail_review(review_id, message)
        if connection is not None:
            try:
                connection.execute(
                    "UPDATE reviews SET status = 'error', error = ? WHERE id = ?", (message, review_id)
                )
                connection.commit()
            except Exception:  # pragma: no cover
                logger.exception("Could not persist review failure")
    finally:
        if connection is not None:
            connection.close()
