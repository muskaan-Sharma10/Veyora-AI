"""Recruiter API: jobs, review pipeline, shortlisting, interviews and email."""

import json
import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional

from flask import Blueprint, Response, current_app, jsonify, request, session, stream_with_context

from agents.platform_agents import (
    JobDescriptionAnalyst,
    RecruitmentSchedulingAgent,
    ScreeningDecisionAgent,
    ai_mode,
    ai_status,
)
from auth_utils import role_required
from db import execute, fetch_all, fetch_one, new_id
from services.emailer import deliver, email_configured, log_email, previously_sent
from services.extraction import extract_upload
from services.jobstore import REVIEWS, add_stage, create_review, get_review
from services.review_pipeline import run_review
from services.skills import dedupe_skills

logger = logging.getLogger(__name__)
recruiter_bp = Blueprint("recruiter_api", __name__)

MAX_RESUMES = 20
APPLICATION_STATUSES = {"under_review", "shortlisted", "rejected", "interview"}
SLOT_STATUSES = {"available", "reserved", "confirmed", "cancelled"}
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _json_body() -> Dict[str, Any]:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _as_skill_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return dedupe_skills([str(item) for item in value if str(item).strip()])
    if isinstance(value, str):
        return dedupe_skills([item for item in re.split(r"[,;\n]", value) if item.strip()])
    return []


def _load_job(job_id: str, owner_id: int) -> Optional[Dict[str, Any]]:
    job = fetch_one("SELECT * FROM jobs WHERE id = ? AND owner_id = ?", (job_id, owner_id))
    if not job:
        return None
    job["required_skills"] = json.loads(job["required_skills"] or "[]")
    job["preferred_skills"] = json.loads(job["preferred_skills"] or "[]")
    return job


def _job_payload(job: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": job["id"],
        "title": job["title"],
        "description": job["description"],
        "required_skills": job["required_skills"],
        "preferred_skills": job["preferred_skills"],
        "min_years": job["min_years"],
        "seniority": job.get("seniority", ""),
        "created_at": job.get("created_at", ""),
    }


# ─── Dashboard ─────────────────────────────────────────────────────────────

@recruiter_bp.get("/api/dashboard")
@role_required("recruiter")
def dashboard():
    owner_id = session["user_id"]
    jobs = fetch_all("SELECT * FROM jobs WHERE owner_id = ? ORDER BY created_at DESC LIMIT 6", (owner_id,))
    for job in jobs:
        job["required_skills"] = json.loads(job["required_skills"] or "[]")
        job["preferred_skills"] = json.loads(job["preferred_skills"] or "[]")
    counts = {
        "jobs": fetch_one("SELECT COUNT(*) AS n FROM jobs WHERE owner_id = ?", (owner_id,))["n"],
        "applications": fetch_one("SELECT COUNT(*) AS n FROM applications WHERE recruiter_id = ?", (owner_id,))["n"],
        "shortlisted": fetch_one(
            "SELECT COUNT(*) AS n FROM applications WHERE recruiter_id = ? AND status = 'shortlisted'",
            (owner_id,),
        )["n"],
        "interviews": fetch_one(
            "SELECT COUNT(*) AS n FROM interview_slots WHERE recruiter_id = ? AND status != 'cancelled'",
            (owner_id,),
        )["n"],
    }
    upcoming = fetch_all(
        "SELECT * FROM interview_slots WHERE recruiter_id = ? AND status != 'cancelled' "
        "ORDER BY date, start_time LIMIT 5",
        (owner_id,),
    )
    open_reviews = [
        {"id": key, "job_id": value["job_id"], "status": value["status"]}
        for key, value in REVIEWS.items()
        if value.get("owner_id") == owner_id and value["status"] == "running"
    ]
    recent_decisions = []
    for row in fetch_all(
        "SELECT a.id, a.name, a.score, a.status, a.created_at, a.analysis, j.title AS job_title "
        "FROM applications a LEFT JOIN jobs j ON j.id = a.job_id "
        "WHERE a.recruiter_id = ? ORDER BY a.created_at DESC, a.rowid DESC LIMIT 6",
        (owner_id,),
    ):
        try:
            analysis = json.loads(row["analysis"] or "{}")
        except json.JSONDecodeError:
            analysis = {}
        screening = analysis.get("screening") or {}
        recent_decisions.append({
            "application_id": row["id"],
            "name": row["name"],
            "score": row["score"],
            "status": row["status"],
            "decision": screening.get("decision", row["status"]),
            "threshold": screening.get("threshold"),
            "reason": screening.get("summary", ""),
            "job_title": row["job_title"] or "",
            "created_at": row["created_at"],
        })
    return jsonify({
        "counts": counts,
        "jobs": [_job_payload(job) for job in jobs],
        "upcoming_interviews": upcoming,
        "running_reviews": open_reviews,
        "recent_decisions": recent_decisions,
        "screening_threshold": ScreeningDecisionAgent().threshold(),
        "ai": ai_status(),
        "email_configured": email_configured(),
    })


# ─── Jobs ──────────────────────────────────────────────────────────────────

@recruiter_bp.get("/api/jobs")
@role_required("recruiter")
def list_jobs():
    owner_id = session["user_id"]
    jobs = fetch_all("SELECT * FROM jobs WHERE owner_id = ? ORDER BY created_at DESC", (owner_id,))
    payload = []
    for job in jobs:
        job["required_skills"] = json.loads(job["required_skills"] or "[]")
        job["preferred_skills"] = json.loads(job["preferred_skills"] or "[]")
        item = _job_payload(job)
        item["application_count"] = fetch_one(
            "SELECT COUNT(*) AS n FROM applications WHERE job_id = ?", (job["id"],)
        )["n"]
        payload.append(item)
    return jsonify({"jobs": payload})


@recruiter_bp.post("/api/jobs")
@role_required("recruiter")
def create_job():
    data = _json_body()
    title = str(data.get("title") or "").strip()
    description = str(data.get("description") or "").strip()
    if not title:
        return jsonify({"error": "Add a job title."}), 400
    if len(description) < 30:
        return jsonify({"error": "Add a job description of at least 30 characters."}), 400

    job_id = new_id()
    execute(
        """INSERT INTO jobs (id, owner_id, title, description, required_skills, preferred_skills, min_years, seniority)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            job_id,
            session["user_id"],
            title[:160],
            description,
            json.dumps(_as_skill_list(data.get("required_skills"))),
            json.dumps(_as_skill_list(data.get("preferred_skills"))),
            float(data.get("min_years") or 0),
            str(data.get("seniority") or "")[:40],
        ),
    )
    job = _load_job(job_id, session["user_id"])
    return jsonify({"job": _job_payload(job)}), 201


@recruiter_bp.patch("/api/jobs/<job_id>")
@role_required("recruiter")
def update_job(job_id: str):
    job = _load_job(job_id, session["user_id"])
    if not job:
        return jsonify({"error": "Job not found."}), 404
    data = _json_body()
    title = str(data.get("title", job["title"])).strip() or job["title"]
    description = str(data.get("description", job["description"])).strip() or job["description"]
    execute(
        """UPDATE jobs SET title = ?, description = ?, required_skills = ?, preferred_skills = ?,
                min_years = ?, seniority = ? WHERE id = ?""",
        (
            title[:160],
            description,
            json.dumps(_as_skill_list(data["required_skills"]) if "required_skills" in data else job["required_skills"]),
            json.dumps(_as_skill_list(data["preferred_skills"]) if "preferred_skills" in data else job["preferred_skills"]),
            float(data.get("min_years", job["min_years"]) or 0),
            str(data.get("seniority", job.get("seniority", "")))[:40],
            job_id,
        ),
    )
    return jsonify({"job": _job_payload(_load_job(job_id, session["user_id"]))})


@recruiter_bp.delete("/api/jobs/<job_id>")
@role_required("recruiter")
def delete_job(job_id: str):
    job = _load_job(job_id, session["user_id"])
    if not job:
        return jsonify({"error": "Job not found."}), 404
    applications = fetch_one("SELECT COUNT(*) AS n FROM applications WHERE job_id = ?", (job_id,))["n"]
    if applications:
        return jsonify({
            "error": f"This job has {applications} candidate record(s). Keep it for your audit trail."
        }), 409
    execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    return jsonify({"message": "Job deleted."})


@recruiter_bp.post("/api/jobs/analyze")
@role_required("recruiter")
def analyze_job():
    data = _json_body()
    description = str(data.get("description") or "").strip()
    if len(description) < 30:
        return jsonify({"error": "Add a job description of at least 30 characters."}), 400
    job = JobDescriptionAnalyst().run(description, str(data.get("title") or "").strip())
    return jsonify({"job": job, "ai_mode": ai_mode()})


# ─── Reviews ───────────────────────────────────────────────────────────────

@recruiter_bp.post("/api/reviews/start")
@role_required("recruiter")
def start_review():
    owner_id = session["user_id"]
    job_id = str(request.form.get("job_id") or "").strip()
    title = str(request.form.get("job_title") or request.form.get("title") or "").strip()
    description = str(request.form.get("job_description") or request.form.get("description") or "").strip()
    required = _as_skill_list(request.form.get("required_skills"))
    preferred = _as_skill_list(request.form.get("preferred_skills"))
    min_years_raw = request.form.get("min_years")
    min_years = 0.0
    if min_years_raw not in (None, ""):
        try:
            min_years = max(0.0, float(min_years_raw))
        except (TypeError, ValueError):
            return jsonify({"error": "Minimum experience must be a number."}), 400

    if job_id:
        job = _load_job(job_id, owner_id)
        if not job:
            return jsonify({"error": "Selected job was not found."}), 404
        description = description or job["description"]
        title = title or job["title"]
        required = required or job["required_skills"]
        preferred = preferred or job["preferred_skills"]
        min_years = min_years or float(job["min_years"] or 0)
    else:
        if not description:
            return jsonify({"error": "Add a job description or select an existing job."}), 400
        if len(description) < 30:
            return jsonify({"error": "The job description is too short to analyse (30 characters minimum)."}), 400
        job_id = new_id()
        execute(
            """INSERT INTO jobs (id, owner_id, title, description, required_skills, preferred_skills, min_years, seniority)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job_id, owner_id, title[:160] or "",
                description, json.dumps(required), json.dumps(preferred), min_years, "",
            ),
        )
        job = _load_job(job_id, owner_id)

    uploads = [item for item in request.files.getlist("resumes") if item and item.filename]
    if not uploads:
        return jsonify({"error": "Upload at least one resume (PDF, DOCX or TXT)."}), 400
    if len(uploads) > MAX_RESUMES:
        return jsonify({"error": f"Upload no more than {MAX_RESUMES} resumes at a time."}), 400

    resumes = []
    errors = []
    used_names = set()
    for upload in uploads:
        try:
            filename, text = extract_upload(upload)
        except ValueError as exc:
            errors.append(f"{upload.filename or 'file'}: {exc}")
            continue
        if filename in used_names:
            stem, dot, extension = filename.rpartition(".")
            counter = 2
            while f"{stem}_{counter}{dot}{extension}" in used_names:
                counter += 1
            filename = f"{stem}_{counter}{dot}{extension}"
        used_names.add(filename)
        resume_id = new_id()
        execute(
            "INSERT INTO resumes (id, owner_id, filename, text, parsed, source) VALUES (?, ?, ?, ?, ?, ?)",
            (resume_id, owner_id, filename, text, "{}", "recruiter_review"),
        )
        resumes.append({"filename": filename, "text": text, "resume_id": resume_id})

    if errors:
        return jsonify({"error": "Some resumes could not be read.", "details": errors}), 400
    if not resumes:
        return jsonify({"error": "No readable resumes were found in the upload."}), 400

    review_id = create_review(owner_id, job_id, ai_mode())
    review = get_review(review_id)
    if review is not None:
        review["payload"] = {
            "owner_id": owner_id,
            "job_id": job_id,
            "job": {
                "title": title or job["title"],
                "description": description or job["description"],
                "required_skills": required,
                "preferred_skills": preferred,
                "min_years": min_years,
            },
            "resumes": resumes,
        }
    execute(
        "INSERT INTO reviews (id, owner_id, job_id, status, ai_mode) VALUES (?, ?, ?, 'running', ?)",
        (review_id, owner_id, job_id, ai_mode()),
    )

    thread = threading.Thread(
        target=run_review,
        args=(
            current_app.config["DATABASE"], review_id, owner_id, job_id,
            {
                "title": title or job["title"],
                "description": description or job["description"],
                "required_skills": required,
                "preferred_skills": preferred,
                "min_years": min_years,
            },
            resumes,
        ),
        daemon=True,
    )
    thread.start()
    logger.info("Started review %s for %s resume(s)", review_id, len(resumes))
    return jsonify({"review_id": review_id, "job_id": job_id, "ai_mode": ai_mode()}), 202


def _owned_review(review_id: str) -> Optional[Dict[str, Any]]:
    review = get_review(review_id)
    if not review or review.get("owner_id") != session["user_id"]:
        return None
    return review


@recruiter_bp.get("/api/reviews/<review_id>")
@role_required("recruiter")
def review_status(review_id: str):
    review = _owned_review(review_id)
    if not review:
        return jsonify({"error": "Review not found."}), 404
    return jsonify({
        "review_id": review_id,
        "status": review["status"],
        "stages": review["stages"],
        "result": review.get("result"),
        "error": review.get("error", ""),
        "ai_mode": review.get("ai_mode", "local"),
    })


@recruiter_bp.get("/api/reviews/<review_id>/stream")
@role_required("recruiter")
def review_stream(review_id: str):
    review = _owned_review(review_id)
    if not review:
        return jsonify({"error": "Review not found."}), 404

    def generate():
        sent = 0
        started = time.time()
        while time.time() - started < 420:
            current = get_review(review_id)
            if not current:
                yield f"data: {json.dumps({'type': 'error', 'error': 'Review state expired.'})}\n\n"
                return
            stages = current["stages"]
            for stage in stages[sent:]:
                yield f"data: {json.dumps({'type': 'stage', **stage})}\n\n"
                sent += 1
            if current["status"] == "complete":
                yield f"data: {json.dumps({'type': 'complete', 'result': current.get('result')})}\n\n"
                return
            if current["status"] == "error":
                yield f"data: {json.dumps({'type': 'error', 'error': current.get('error')})}\n\n"
                return
            time.sleep(0.4)
        yield f"data: {json.dumps({'type': 'error', 'error': 'The review timed out.'})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@recruiter_bp.post("/api/reviews/<review_id>/retry")
@role_required("recruiter")
def retry_review(review_id: str):
    review = _owned_review(review_id)
    if not review:
        return jsonify({"error": "Review not found."}), 404
    payload = review.get("payload")
    if not payload:
        return jsonify({"error": "This review can no longer be retried. Start a new review."}), 400

    review["status"] = "running"
    review["stages"] = []
    review["result"] = None
    review["error"] = ""
    execute("UPDATE reviews SET status = 'running', error = '' WHERE id = ?", (review_id,))

    thread = threading.Thread(
        target=run_review,
        args=(
            current_app.config["DATABASE"], review_id, payload["owner_id"],
            payload["job_id"], payload["job"], payload["resumes"],
        ),
        daemon=True,
    )
    thread.start()
    return jsonify({"review_id": review_id, "status": "running"}), 202


# ─── Applications / shortlisting / comparison ──────────────────────────────

def _application_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    analysis = json.loads(row.get("analysis") or "{}")
    compatibility = analysis.get("compatibility", {})
    return {
        "screening": analysis.get("screening", {}),
        "id": row["id"],
        "job_id": row["job_id"],
        "name": row["name"],
        "email": row["email"],
        "phone": row["phone"],
        "filename": row["filename"],
        "score": row["score"],
        "status": row["status"],
        "created_at": row["created_at"],
        "grade": compatibility.get("grade", ""),
        "matched_skills": compatibility.get("matched_skills", []),
        "partial_skills": compatibility.get("partial_skills", []),
        "missing_skills": compatibility.get("missing_skills", []),
        "analysis": analysis,
    }


@recruiter_bp.get("/api/jobs/<job_id>/applications")
@role_required("recruiter")
def job_applications(job_id: str):
    if not _load_job(job_id, session["user_id"]):
        return jsonify({"error": "Job not found."}), 404
    rows = fetch_all(
        "SELECT * FROM applications WHERE job_id = ? AND recruiter_id = ? ORDER BY score DESC",
        (job_id, session["user_id"]),
    )
    return jsonify({"applications": [_application_payload(row) for row in rows]})


@recruiter_bp.get("/api/applications/<application_id>")
@role_required("recruiter")
def application_detail(application_id: str):
    row = fetch_one(
        "SELECT * FROM applications WHERE id = ? AND recruiter_id = ?",
        (application_id, session["user_id"]),
    )
    if not row:
        return jsonify({"error": "Candidate record not found."}), 404
    return jsonify({"application": _application_payload(row)})


@recruiter_bp.post("/api/applications/<application_id>/status")
@role_required("recruiter")
def set_application_status(application_id: str):
    row = fetch_one(
        "SELECT * FROM applications WHERE id = ? AND recruiter_id = ?",
        (application_id, session["user_id"]),
    )
    if not row:
        return jsonify({"error": "Candidate record not found."}), 404
    data = _json_body()
    status = str(data.get("status") or "").strip()
    if status not in APPLICATION_STATUSES:
        return jsonify({"error": "Choose a valid status."}), 400
    execute(
        "UPDATE applications SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (status, application_id),
    )
    return jsonify({"application": _application_payload(fetch_one("SELECT * FROM applications WHERE id = ?", (application_id,)))})


@recruiter_bp.get("/api/jobs/<job_id>/comparison")
@role_required("recruiter")
def compare_candidates(job_id: str):
    if not _load_job(job_id, session["user_id"]):
        return jsonify({"error": "Job not found."}), 404
    rows = fetch_all(
        "SELECT * FROM applications WHERE job_id = ? AND recruiter_id = ? ORDER BY score DESC",
        (job_id, session["user_id"]),
    )
    if not rows:
        return jsonify({"error": "Review candidates for this job before comparing them."}), 404

    requested = [item for item in (request.args.get("ids") or "").split(",") if item.strip()]
    if requested:
        rows = [row for row in rows if row["id"] in requested] or rows

    candidates = []
    for row in rows:
        payload = _application_payload(row)
        compatibility = payload["analysis"].get("compatibility", {})
        profile = payload["analysis"].get("profile", {})
        candidates.append({
            "id": row["id"],
            "name": row["name"],
            "score": row["score"],
            "grade": compatibility.get("grade", ""),
            "status": row["status"],
            "breakdown": compatibility.get("breakdown", []),
            "matched_skills": compatibility.get("matched_skills", []),
            "partial_skills": compatibility.get("partial_skills", []),
            "missing_skills": compatibility.get("missing_skills", []),
            "experience_years": profile.get("total_years", 0),
            "education": profile.get("education", []),
            "certifications": profile.get("certifications", []),
            "projects": profile.get("projects", []),
        })

    def best(key: str) -> Any:
        ordered = sorted(candidates, key=lambda item: item[key], reverse=True)
        return ordered[0]["id"] if ordered else None

    return jsonify({
        "job": _job_payload(_load_job(job_id, session["user_id"])),
        "candidates": candidates,
        "best": {"score": best("score"), "experience_years": best("experience_years")},
    })


# ─── Interview slots ───────────────────────────────────────────────────────

def _slot_conflict(
    recruiter_id: int, date_value: str, start: str, end: str,
    exclude_id: str = "", candidate_email: str = "",
) -> Optional[Dict[str, Any]]:
    rows = fetch_all(
        "SELECT * FROM interview_slots WHERE recruiter_id = ? AND date = ? AND status != 'cancelled'",
        (recruiter_id, date_value),
    )
    for row in rows:
        if row["id"] == exclude_id:
            continue
        if start < row["end_time"] and end > row["start_time"]:
            return row
    if candidate_email:
        bookings = fetch_all(
            "SELECT * FROM interview_slots WHERE recruiter_id = ? AND date = ? AND status != 'cancelled' "
            "AND LOWER(candidate_email) = ?",
            (recruiter_id, date_value, candidate_email.lower()),
        )
        for row in bookings:
            if row["id"] != exclude_id and start < row["end_time"] and end > row["start_time"]:
                return row
    return None


def _validate_slot_times(date_value: str, start: str, end: str) -> Optional[str]:
    try:
        from datetime import datetime

        datetime.strptime(date_value, "%Y-%m-%d")
    except (TypeError, ValueError):
        return "Choose a valid date (YYYY-MM-DD)."
    if not TIME_RE.match(start) or not TIME_RE.match(end):
        return "Times must use 24-hour HH:MM format."
    if start >= end:
        return "The end time must be after the start time."
    return None


@recruiter_bp.get("/api/interview-slots")
@role_required("recruiter")
def list_slots():
    owner_id = session["user_id"]
    args = []
    sql = "SELECT * FROM interview_slots WHERE recruiter_id = ?"
    if request.args.get("from"):
        sql += " AND date >= ?"
        args.append(request.args["from"])
    if request.args.get("to"):
        sql += " AND date <= ?"
        args.append(request.args["to"])
    sql += " ORDER BY date, start_time"
    slots = fetch_all(sql, tuple([owner_id, *args]))
    return jsonify({"slots": slots})


@recruiter_bp.post("/api/interview-slots")
@role_required("recruiter")
def create_slot():
    data = _json_body()
    date_value = str(data.get("date") or "").strip()
    start = str(data.get("start_time") or "").strip()
    end = str(data.get("end_time") or "").strip()
    error = _validate_slot_times(date_value, start, end)
    if error:
        return jsonify({"error": error}), 400

    candidate_email = str(data.get("candidate_email") or "").strip()
    conflict = _slot_conflict(session["user_id"], date_value, start, end, candidate_email=candidate_email)
    if conflict:
        return jsonify({
            "error": f"This overlaps an existing {conflict['status']} slot "
                     f"({conflict['start_time']}–{conflict['end_time']})."
        }), 409

    slot_id = new_id()
    status = "reserved" if data.get("application_id") or candidate_email else "available"
    execute(
        """INSERT INTO interview_slots (id, recruiter_id, job_id, application_id, candidate_name,
                candidate_email, date, start_time, end_time, timezone, interviewer, mode, location,
                status, instructions)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            slot_id, session["user_id"], str(data.get("job_id") or ""),
            str(data.get("application_id") or ""), str(data.get("candidate_name") or ""),
            candidate_email, date_value, start, end,
            str(data.get("timezone") or "UTC")[:60], str(data.get("interviewer") or "")[:120],
            str(data.get("mode") or "Video")[:40], str(data.get("location") or "")[:300],
            status, str(data.get("instructions") or "")[:1000],
        ),
    )
    return jsonify({"slot": fetch_one("SELECT * FROM interview_slots WHERE id = ?", (slot_id,))}), 201


@recruiter_bp.patch("/api/interview-slots/<slot_id>")
@role_required("recruiter")
def update_slot(slot_id: str):
    slot = fetch_one(
        "SELECT * FROM interview_slots WHERE id = ? AND recruiter_id = ?",
        (slot_id, session["user_id"]),
    )
    if not slot:
        return jsonify({"error": "Interview slot not found."}), 404
    data = _json_body()
    date_value = str(data.get("date", slot["date"])).strip()
    start = str(data.get("start_time", slot["start_time"])).strip()
    end = str(data.get("end_time", slot["end_time"])).strip()
    status = str(data.get("status", slot["status"])).strip()
    if status not in SLOT_STATUSES:
        return jsonify({"error": "Choose a valid slot status."}), 400
    error = _validate_slot_times(date_value, start, end)
    if error:
        return jsonify({"error": error}), 400

    if status != "cancelled":
        conflict = _slot_conflict(
            session["user_id"], date_value, start, end, exclude_id=slot_id,
            candidate_email=str(data.get("candidate_email", slot["candidate_email"]) or ""),
        )
        if conflict:
            return jsonify({"error": "This slot would overlap another booking. Prevented to avoid double-booking."}), 409

    execute(
        """UPDATE interview_slots SET date = ?, start_time = ?, end_time = ?, timezone = ?,
                interviewer = ?, mode = ?, location = ?, status = ?, instructions = ?,
                candidate_name = ?, candidate_email = ?, application_id = ?,
                updated_at = CURRENT_TIMESTAMP
           WHERE id = ?""",
        (
            date_value, start, end,
            str(data.get("timezone", slot["timezone"]))[:60],
            str(data.get("interviewer", slot["interviewer"]))[:120],
            str(data.get("mode", slot["mode"]))[:40],
            str(data.get("location", slot["location"]))[:300],
            status, str(data.get("instructions", slot["instructions"]))[:1000],
            str(data.get("candidate_name", slot["candidate_name"]))[:120],
            str(data.get("candidate_email", slot["candidate_email"]))[:160],
            str(data.get("application_id", slot["application_id"])),
            slot_id,
        ),
    )
    return jsonify({"slot": fetch_one("SELECT * FROM interview_slots WHERE id = ?", (slot_id,))})


@recruiter_bp.delete("/api/interview-slots/<slot_id>")
@role_required("recruiter")
def cancel_slot(slot_id: str):
    slot = fetch_one(
        "SELECT * FROM interview_slots WHERE id = ? AND recruiter_id = ?",
        (slot_id, session["user_id"]),
    )
    if not slot:
        return jsonify({"error": "Interview slot not found."}), 404
    execute(
        "UPDATE interview_slots SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (slot_id,),
    )
    return jsonify({"slot": fetch_one("SELECT * FROM interview_slots WHERE id = ?", (slot_id,))})


@recruiter_bp.post("/api/interview-slots/<slot_id>/assign")
@role_required("recruiter")
def assign_slot(slot_id: str):
    slot = fetch_one(
        "SELECT * FROM interview_slots WHERE id = ? AND recruiter_id = ?",
        (slot_id, session["user_id"]),
    )
    if not slot:
        return jsonify({"error": "Interview slot not found."}), 404
    if slot["status"] == "cancelled":
        return jsonify({"error": "This slot was cancelled. Create a new slot first."}), 400

    data = _json_body()
    application_id = str(data.get("application_id") or "").strip()
    name = str(data.get("candidate_name") or "").strip()
    email = str(data.get("candidate_email") or "").strip()
    job_id = str(data.get("job_id") or slot["job_id"] or "")

    if application_id:
        application = fetch_one(
            "SELECT * FROM applications WHERE id = ? AND recruiter_id = ?",
            (application_id, session["user_id"]),
        )
        if not application:
            return jsonify({"error": "Candidate record not found."}), 404
        name = name or application["name"]
        email = email or application["email"]
        job_id = job_id or application["job_id"]

    if not name:
        return jsonify({"error": "Choose a candidate to assign this slot."}), 400
    if email and not EMAIL_RE.match(email):
        return jsonify({"error": "Enter a valid candidate email address."}), 400

    conflict = _slot_conflict(
        session["user_id"], slot["date"], slot["start_time"], slot["end_time"],
        exclude_id=slot_id, candidate_email=email,
    )
    if conflict:
        return jsonify({
            "error": f"{name} or this time is already booked ({conflict['date']} "
                     f"{conflict['start_time']}–{conflict['end_time']})."
        }), 409

    execute(
        """UPDATE interview_slots SET application_id = ?, candidate_name = ?, candidate_email = ?,
                job_id = ?, status = 'reserved', updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
        (application_id, name[:120], email[:160], job_id, slot_id),
    )
    return jsonify({"slot": fetch_one("SELECT * FROM interview_slots WHERE id = ?", (slot_id,))})


@recruiter_bp.post("/api/interview-slots/<slot_id>/confirm")
@role_required("recruiter")
def confirm_slot(slot_id: str):
    slot = fetch_one(
        "SELECT * FROM interview_slots WHERE id = ? AND recruiter_id = ?",
        (slot_id, session["user_id"]),
    )
    if not slot:
        return jsonify({"error": "Interview slot not found."}), 404
    if not slot["candidate_name"]:
        return jsonify({"error": "Assign a candidate before confirming the slot."}), 400
    execute(
        "UPDATE interview_slots SET status = 'confirmed', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (slot_id,),
    )
    return jsonify({"slot": fetch_one("SELECT * FROM interview_slots WHERE id = ?", (slot_id,))})


# ─── Email automation ──────────────────────────────────────────────────────

def _slot_context(slot_id: str) -> Optional[Dict[str, Any]]:
    slot = fetch_one(
        "SELECT * FROM interview_slots WHERE id = ? AND recruiter_id = ?",
        (slot_id, session["user_id"]),
    )
    if not slot:
        return None
    application = None
    if slot["application_id"]:
        application = fetch_one("SELECT * FROM applications WHERE id = ?", (slot["application_id"],))
    job = None
    job_id = slot["job_id"] or (application["job_id"] if application else "")
    if job_id:
        job_row = fetch_one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        if job_row:
            job = _job_payload(job_row)
    candidate = {
        "name": slot["candidate_name"] or (application["name"] if application else ""),
        "email": slot["candidate_email"] or (application["email"] if application else ""),
    }
    return {"slot": slot, "application": application, "job": job or {"title": "the role"}, "candidate": candidate}


@recruiter_bp.post("/api/interview-slots/<slot_id>/email-preview")
@role_required("recruiter")
def email_preview(slot_id: str):
    context = _slot_context(slot_id)
    if not context:
        return jsonify({"error": "Interview slot not found."}), 404
    data = _json_body()
    slot = dict(context["slot"])
    for field in ("date", "start_time", "end_time", "timezone", "mode", "interviewer", "location", "instructions"):
        if data.get(field):
            slot[field] = data[field]
    draft = RecruitmentSchedulingAgent().compose_invitation(
        context["candidate"], context["job"], slot,
        recruiter_name=data.get("recruiter_name") or "Hiring team",
    )
    existing = previously_sent(slot_id)
    return jsonify({
        "subject": draft["subject"],
        "body": draft["body"],
        "to": str(data.get("to_email") or context["candidate"].get("email") or ""),
        "email_configured": email_configured(),
        "already_sent": bool(existing),
        "previous_status": existing["status"] if existing else "",
    })


@recruiter_bp.post("/api/interview-slots/<slot_id>/send-email")
@role_required("recruiter")
def send_email(slot_id: str):
    context = _slot_context(slot_id)
    if not context:
        return jsonify({"error": "Interview slot not found."}), 404
    if context["slot"]["status"] == "cancelled":
        return jsonify({"error": "This slot was cancelled; no invitation will be sent."}), 400

    existing = previously_sent(slot_id)
    if existing:
        return jsonify({
            "error": f"An invitation was already recorded for this slot ({existing['status']}). "
                     "Duplicate emails are blocked."
        }), 409

    data = _json_body()
    to_email = str(data.get("to_email") or context["candidate"].get("email") or "").strip()
    if not EMAIL_RE.match(to_email):
        return jsonify({"error": "Add a valid candidate email address before sending."}), 400

    subject = str(data.get("subject") or "").strip()
    body = str(data.get("body") or "").strip()
    if not subject or not body:
        draft = RecruitmentSchedulingAgent().compose_invitation(
            context["candidate"], context["job"], context["slot"]
        )
        subject = subject or draft["subject"]
        body = body or draft["body"]

    result = deliver(
        to_email, subject, body,
        slot_id=slot_id,
        application_id=context["slot"]["application_id"],
        recruiter_id=session["user_id"],
    )
    if result["status"] == "failed":
        return jsonify({"error": result["message"]}), 502

    if context["slot"]["status"] == "available":
        execute(
            "UPDATE interview_slots SET status = 'reserved', candidate_email = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (to_email, slot_id),
        )
    execute(
        "UPDATE interview_slots SET candidate_email = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (to_email, slot_id),
    )
    return jsonify({
        "status": result["status"],
        "sent": result["sent"],
        "message": result["message"],
        "to": to_email,
    })


@recruiter_bp.get("/api/email-log")
@role_required("recruiter")
def email_history():
    rows = fetch_all(
        "SELECT id, slot_id, to_email, subject, status, provider, error, created_at "
        "FROM email_log WHERE recruiter_id = ? ORDER BY created_at DESC LIMIT 50",
        (session["user_id"],),
    )
    return jsonify({"emails": rows, "email_configured": email_configured()})


# ─── Settings ──────────────────────────────────────────────────────────────

@recruiter_bp.get("/api/settings")
@role_required("recruiter")
def settings():
    owner_id = session["user_id"]
    return jsonify({
        "ai": ai_status(),
        "email_configured": email_configured(),
        "counts": {
            "jobs": fetch_one("SELECT COUNT(*) AS n FROM jobs WHERE owner_id = ?", (owner_id,))["n"],
            "resumes": fetch_one("SELECT COUNT(*) AS n FROM resumes WHERE owner_id = ?", (owner_id,))["n"],
            "applications": fetch_one("SELECT COUNT(*) AS n FROM applications WHERE recruiter_id = ?", (owner_id,))["n"],
            "emails": fetch_one("SELECT COUNT(*) AS n FROM email_log WHERE recruiter_id = ?", (owner_id,))["n"],
        },
        "required_env": [
            {"name": "GEMINI_API_KEY", "purpose": "Resume/job AI extraction, coaching and interview questions"},
            {"name": "GEMINI_MODEL", "purpose": "Optional model override (default gemini-3.8-flash)"},
            {"name": "SMTP_HOST", "purpose": "Email delivery"},
            {"name": "SMTP_PORT", "purpose": "587 (STARTTLS) or 465 (SSL)"},
            {"name": "SMTP_USER", "purpose": "SMTP username"},
            {"name": "SMTP_PASSWORD", "purpose": "SMTP password or app password"},
            {"name": "SMTP_FROM", "purpose": "Optional From address"},
        ],
    })
