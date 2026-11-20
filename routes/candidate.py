"""Candidate API: resume review, compatibility, gaps, interview prep, roadmap."""

import json
import logging
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request, session

from agents.platform_agents import (
    CareerIntelligenceAgent,
    CompatibilityScoringAgent,
    InterviewStrategistAgent,
    JobDescriptionAnalyst,
    ResumeIntelligenceAgent,
    SkillGapCoachAgent,
    ai_mode,
    ai_status,
)
from auth_utils import role_required
from db import execute, fetch_all, fetch_one, new_id
from services.extraction import extract_upload
from services.scoring import normalize_job

logger = logging.getLogger(__name__)
candidate_bp = Blueprint("candidate_api", __name__)


def _json_body() -> Dict[str, Any]:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _resume_row(resume_id: str = "") -> Optional[Dict[str, Any]]:
    user_id = session["user_id"]
    if resume_id:
        return fetch_one("SELECT * FROM resumes WHERE id = ? AND owner_id = ?", (resume_id, user_id))
    return fetch_one(
        "SELECT * FROM resumes WHERE owner_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (user_id,),
    )


def _profile_of(row: Dict[str, Any]) -> Dict[str, Any]:
    try:
        parsed = json.loads(row.get("parsed") or "{}")
    except json.JSONDecodeError:
        parsed = {}
    if not parsed:
        # Fallback: parse on demand from stored text.
        parsed = ResumeIntelligenceAgent().run(row["text"], row["filename"])
        execute("UPDATE resumes SET parsed = ? WHERE id = ?", (json.dumps(parsed), row["id"]))
    parsed["raw_text"] = row.get("text", "") or parsed.get("raw_text", "")
    return parsed


def _derive_job_from_resume(profile: Dict[str, Any], title: str = "") -> Dict[str, Any]:
    skills = profile.get("technical_skills", [])[:8]
    return normalize_job({
        "title": title or "Target role",
        "description": "",
        "required_skills": skills,
        "preferred_skills": [],
        "min_years": 0,
        "competencies": profile.get("soft_skills", [])[:4],
    })


def _analyse_job(description: str, title: str = "") -> Dict[str, Any]:
    return JobDescriptionAnalyst().run(description, title)


def _job_for_request(data: Dict[str, Any], profile: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Build the target job from an open role (job_id), a pasted JD, or the resume."""
    job_id = str(data.get("job_id") or "").strip()
    if job_id:
        row = fetch_one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        if not row:
            return None
        try:
            required = json.loads(row["required_skills"] or "[]")
            preferred = json.loads(row["preferred_skills"] or "[]")
        except json.JSONDecodeError:
            required, preferred = [], []
        return normalize_job({
            "title": row["title"],
            "description": row["description"],
            "required_skills": required,
            "preferred_skills": preferred,
            "min_years": row["min_years"],
            "seniority": row["seniority"],
        })
    description = str(data.get("job_description") or "").strip()
    if description:
        return _analyse_job(description, str(data.get("job_title") or "").strip())
    return _derive_job_from_resume(profile, str(data.get("target_role") or ""))


def _store_report(kind: str, resume_id: str, target_role: str, payload: Dict[str, Any]) -> None:
    execute(
        "INSERT INTO candidate_reports (id, user_id, resume_id, kind, target_role, payload) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (new_id(), session["user_id"], resume_id, kind, target_role[:160], json.dumps(payload)),
    )


def _career_payload(kind: str, report: Dict[str, Any], gaps: Dict[str, Any], job: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "kind": kind,
        "job": job,
        "score": report["score"],
        "grade": report["grade"],
        "breakdown": report["breakdown"],
        "matched_skills": report["matched_skills"],
        "partial_skills": report["partial_skills"],
        "missing_skills": report["missing_skills"],
        "reasons": report["reasons"],
        "methodology": report["methodology"],
        "rationale": report.get("rationale", ""),
        "gaps": gaps.get("gaps", []),
        "resume_review": gaps.get("resume_review", {}),
        "ai_mode": ai_mode(),
    }


# ─── Resume ────────────────────────────────────────────────────────────────

@candidate_bp.post("/api/candidate/resume")
@role_required("candidate")
def upload_resume():
    upload = request.files.get("resume")
    if not upload or not upload.filename:
        return jsonify({"error": "Choose a PDF, DOCX or TXT resume to upload."}), 400
    try:
        filename, text = extract_upload(upload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    profile = ResumeIntelligenceAgent().run(text, filename)
    resume_id = new_id()
    execute(
        "INSERT INTO resumes (id, owner_id, filename, text, parsed, source) VALUES (?, ?, ?, ?, ?, ?)",
        (resume_id, session["user_id"], filename, text, json.dumps(profile), "candidate_upload"),
    )
    return jsonify({
        "resume_id": resume_id,
        "filename": filename,
        "profile": profile,
        "ai_mode": ai_mode(),
    }), 201


@candidate_bp.get("/api/candidate/resume")
@role_required("candidate")
def latest_resume():
    row = _resume_row()
    if not row:
        return jsonify({"resume": None})
    return jsonify({
        "resume": {
            "id": row["id"],
            "filename": row["filename"],
            "created_at": row["created_at"],
            "profile": _profile_of(row),
        },
        "ai_mode": ai_mode(),
    })


@candidate_bp.get("/api/candidate/dashboard")
@role_required("candidate")
def dashboard():
    row = _resume_row()
    reports = fetch_all(
        "SELECT kind, target_role, created_at FROM candidate_reports WHERE user_id = ? "
        "ORDER BY created_at DESC LIMIT 5",
        (session["user_id"],),
    )
    roadmap = fetch_one(
        "SELECT id, target_role, progress FROM roadmaps WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
        (session["user_id"],),
    )
    return jsonify({
        "resume": {
            "id": row["id"], "filename": row["filename"], "created_at": row["created_at"],
            "profile": _profile_of(row),
        } if row else None,
        "recent_reports": reports,
        "roadmap": roadmap,
        "ai": ai_status(),
    })


# ─── Analyses ──────────────────────────────────────────────────────────────

@candidate_bp.get("/api/candidate/jobs")
@role_required("candidate")
def open_roles():
    """Open roles published by recruiters, with the skills each one requires."""
    roles = []
    for row in fetch_all("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 20"):
        try:
            required = json.loads(row["required_skills"] or "[]")
            preferred = json.loads(row["preferred_skills"] or "[]")
        except json.JSONDecodeError:
            required, preferred = [], []
        roles.append({
            "id": row["id"],
            "title": row["title"],
            "description": row["description"],
            "required_skills": required,
            "preferred_skills": preferred,
            "min_years": row["min_years"],
            "seniority": row["seniority"],
            "created_at": row["created_at"],
        })
    return jsonify({"jobs": roles})


@candidate_bp.post("/api/candidate/compatibility")
@role_required("candidate")
def compatibility():
    data = _json_body()
    row = _resume_row(str(data.get("resume_id") or ""))
    if not row:
        return jsonify({"error": "Upload your resume first."}), 400
    description = str(data.get("job_description") or "").strip()
    if not str(data.get("job_id") or "").strip() and len(description) < 30:
        return jsonify({"error": "Paste a job description (at least 30 characters) or pick an open role."}), 400

    profile = _profile_of(row)
    job = _job_for_request(data, profile)
    if job is None:
        return jsonify({"error": "That role is no longer available."}), 404
    report = CompatibilityScoringAgent().run(profile, job)
    gaps = SkillGapCoachAgent().run(profile, job, report)
    payload = _career_payload("compatibility", report, gaps, job)
    payload["profile"] = profile
    _store_report("compatibility", row["id"], job.get("title", ""), payload)
    return jsonify(payload)


@candidate_bp.post("/api/candidate/resume-review")
@role_required("candidate")
def resume_review():
    data = _json_body()
    row = _resume_row(str(data.get("resume_id") or ""))
    if not row:
        return jsonify({"error": "Upload your resume first."}), 400
    profile = _profile_of(row)
    job = _job_for_request(data, profile)
    if job is None:
        return jsonify({"error": "That role is no longer available."}), 404

    report = CompatibilityScoringAgent().run(profile, job)
    gaps = SkillGapCoachAgent().run(profile, job, report)
    payload = _career_payload("resume_review", report, gaps, job)
    payload["profile"] = profile
    _store_report("resume_review", row["id"], job.get("title", ""), payload)
    return jsonify(payload)


@candidate_bp.post("/api/candidate/skill-gaps")
@role_required("candidate")
def skill_gaps():
    data = _json_body()
    row = _resume_row(str(data.get("resume_id") or ""))
    if not row:
        return jsonify({"error": "Upload your resume first."}), 400
    description = str(data.get("job_description") or "").strip()
    profile = _profile_of(row)
    job = _job_for_request(data, profile)
    if job is None:
        return jsonify({"error": "That role is no longer available."}), 404
    report = CompatibilityScoringAgent().run(profile, job)
    gaps = SkillGapCoachAgent().run(profile, job, report)
    payload = _career_payload("skill_gaps", report, gaps, job)
    _store_report("skill_gaps", row["id"], job.get("title", ""), payload)
    return jsonify(payload)


@candidate_bp.post("/api/candidate/interview-prep")
@role_required("candidate")
def interview_prep():
    data = _json_body()
    row = _resume_row(str(data.get("resume_id") or ""))
    if not row:
        return jsonify({"error": "Upload your resume first."}), 400
    description = str(data.get("job_description") or "").strip()
    profile = _profile_of(row)
    job = _job_for_request(data, profile)
    if job is None:
        return jsonify({"error": "That role is no longer available."}), 404
    report = CompatibilityScoringAgent().run(profile, job)
    gaps = SkillGapCoachAgent().run(profile, job, report)
    plan = InterviewStrategistAgent().run(
        profile, job, report, gaps.get("gaps", []), for_candidate=True
    )
    payload = {
        "kind": "interview_prep",
        "job": job,
        "categories": plan["categories"],
        "critical_areas": plan["critical_areas"],
        "score": report["score"],
        "missing_skills": report["missing_skills"],
        "ai_mode": ai_mode(),
    }
    _store_report("interview_prep", row["id"], job.get("title", ""), payload)
    return jsonify(payload)


@candidate_bp.post("/api/candidate/practice")
@role_required("candidate")
def practice():
    data = _json_body()
    question = data.get("question")
    answer = str(data.get("answer") or "").strip()
    if not isinstance(question, dict) or not question.get("question"):
        return jsonify({"error": "Choose a question to practise first."}), 400
    if len(answer.split()) < 10:
        return jsonify({"error": "Write at least a few sentences (10+ words) so the feedback is useful."}), 400
    feedback = InterviewStrategistAgent().review_answer(question, answer)
    return jsonify({"feedback": feedback, "ai_mode": ai_mode()})


@candidate_bp.post("/api/candidate/roadmap")
@role_required("candidate")
def create_roadmap():
    data = _json_body()
    row = _resume_row(str(data.get("resume_id") or ""))
    if not row:
        return jsonify({"error": "Upload your resume first."}), 400
    description = str(data.get("job_description") or "").strip()
    profile = _profile_of(row)
    job = _job_for_request(data, profile)
    if job is None:
        return jsonify({"error": "That role is no longer available."}), 404
    report = CompatibilityScoringAgent().run(profile, job)
    gaps = SkillGapCoachAgent().run(profile, job, report)
    plan = CareerIntelligenceAgent().run(profile, job, gaps.get("gaps", []))

    roadmap_id = new_id()
    execute(
        "INSERT INTO roadmaps (id, user_id, target_role, plan, progress) VALUES (?, ?, ?, ?, ?)",
        (roadmap_id, session["user_id"], plan.get("target_role", "")[:160], json.dumps(plan), "{}"),
    )
    _store_report("roadmap", row["id"], plan.get("target_role", ""), {"plan": plan, "score": report["score"]})
    return jsonify({
        "roadmap_id": roadmap_id,
        "plan": plan,
        "progress": {},
        "score": report["score"],
        "ai_mode": ai_mode(),
    })


@candidate_bp.get("/api/candidate/roadmap")
@role_required("candidate")
def latest_roadmap():
    row = fetch_one(
        "SELECT * FROM roadmaps WHERE user_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (session["user_id"],),
    )
    if not row:
        return jsonify({"roadmap": None})
    return jsonify({
        "roadmap": {
            "id": row["id"],
            "target_role": row["target_role"],
            "plan": json.loads(row["plan"] or "{}"),
            "progress": json.loads(row["progress"] or "{}"),
            "created_at": row["created_at"],
        }
    })


@candidate_bp.post("/api/candidate/roadmap/progress")
@role_required("candidate")
def update_progress():
    data = _json_body()
    row = fetch_one(
        "SELECT * FROM roadmaps WHERE user_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (session["user_id"],),
    )
    if not row:
        return jsonify({"error": "Generate a roadmap first."}), 404
    progress = json.loads(row["progress"] or "{}")
    week = str(data.get("week") or "").strip()
    if not week.isdigit():
        return jsonify({"error": "Choose a valid week."}), 400
    progress[week] = bool(data.get("completed"))
    execute(
        "UPDATE roadmaps SET progress = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (json.dumps(progress), row["id"]),
    )
    return jsonify({"progress": progress})


@candidate_bp.get("/api/candidate/reports")
@role_required("candidate")
def reports():
    rows = fetch_all(
        "SELECT id, kind, target_role, created_at FROM candidate_reports "
        "WHERE user_id = ? ORDER BY created_at DESC LIMIT 30",
        (session["user_id"],),
    )
    return jsonify({"reports": rows})


@candidate_bp.get("/api/candidate/settings")
@role_required("candidate")
def settings():
    return jsonify({
        "ai": ai_status(),
        "counts": {
            "resumes": fetch_one(
                "SELECT COUNT(*) AS n FROM resumes WHERE owner_id = ?", (session["user_id"],)
            )["n"],
            "reports": fetch_one(
                "SELECT COUNT(*) AS n FROM candidate_reports WHERE user_id = ?", (session["user_id"],)
            )["n"],
        },
    })
