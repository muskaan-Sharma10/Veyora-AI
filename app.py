"""
Veyora AI — Flask application entry point.
==========================================
Intelligent Recruitment. Smarter Career Growth.

Pages
  /                 landing page (signed out) or role dashboard
  /login /register  role-aware authentication
  /logout           POST sign out

Recruiter API  → routes/recruiter.py   (jobs, review pipeline, interviews, email)
Candidate API  → routes/candidate.py   (resume review, gaps, prep, roadmap)

Legacy single-shot pipeline endpoints are kept for backwards compatibility.
"""

import io
import json
import logging
import os
import secrets
import smtplib
import threading
import time
import uuid
from email.message import EmailMessage
from typing import Dict

import PyPDF2
from dotenv import load_dotenv
from flask import (
    Flask,
    Response,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    stream_with_context,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from auth_utils import current_user, login_required, role_required
from db import close_db, get_db, init_db, new_id
from routes.candidate import candidate_bp
from routes.recruiter import recruiter_bp

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024
app.config["DATABASE"] = os.environ.get(
    "VEYORA_DATABASE", os.path.join(BASE_DIR, "instance", "veyora.sqlite3")
)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

init_db(app.config["DATABASE"])

app.register_blueprint(recruiter_bp)
app.register_blueprint(candidate_bp)
app.teardown_appcontext(close_db)

ALLOWED_RESUME_EXTENSIONS = {".pdf", ".txt"}
MAX_RESUMES_PER_REQUEST = 20
SAMPLE_RESUME_FILENAMES = {
    "alice_chen_resume.pdf",
    "bob_martinez_resume.pdf",
    "carol_okonkwo_resume.pdf",
    "david_kim_resume.pdf",
    "emma_kowalski_resume.pdf",
}
DATA_DIR = os.path.join(BASE_DIR, "data")

# In-memory job store for the legacy pipeline endpoints.
jobs: Dict[str, dict] = {}


# ─── AI configuration helper ────────────────────────────────────────────────

def gemini_key_error():
    if not os.environ.get("GEMINI_API_KEY", "").strip():
        load_dotenv(override=True)
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return "Add a Google Gemini API key to the project's .env file, restart Veyora AI, and try again."
    if api_key.startswith("gsk_"):
        return "The key in .env is for a different AI service. Add a Google Gemini key from Google AI Studio, then restart Veyora AI."
    return None


# ─── Pages ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    user = current_user()
    if not user:
        return render_template("landing.html")
    template = "candidate.html" if user["role"] == "candidate" else "recruiter.html"
    return render_template(template, user=user)


@app.route("/recruiter")
@role_required("recruiter")
def recruiter_workspace():
    return render_template("recruiter.html", user=current_user())


@app.route("/candidate")
@role_required("candidate")
def candidate_workspace():
    return render_template("candidate.html", user=current_user())


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    role_choice = request.args.get("role", "recruiter")
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "")
        user = get_db().execute(
            "SELECT id, name, email, password_hash, role FROM users WHERE email = ?", (email,)
        ).fetchone()
        if user and check_password_hash(user["password_hash"], password) and user["role"] == role:
            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("index"))
        error = "That email, password, and account type do not match."
    return render_template("auth.html", mode="login", error=error, role_choice=role_choice)


@app.route("/register", methods=["GET", "POST"])
def register():
    error = None
    values = {"name": "", "email": "", "role": request.args.get("role", "candidate")}
    if request.method == "POST":
        values = {
            "name": request.form.get("name", "").strip(),
            "email": request.form.get("email", "").strip().lower(),
            "role": request.form.get("role", ""),
        }
        password = request.form.get("password", "")
        if not values["name"] or "@" not in values["email"]:
            error = "Enter your name and a valid email address."
        elif len(password) < 8:
            error = "Choose a password with at least 8 characters."
        elif values["role"] not in {"recruiter", "candidate"}:
            error = "Choose recruiter or candidate."
        else:
            database = get_db()
            try:
                cursor = database.execute(
                    "INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, ?, ?)",
                    (
                        values["name"],
                        values["email"],
                        generate_password_hash(password),
                        values["role"],
                    ),
                )
                database.commit()
            except Exception:
                error = "An account already uses that email. Please sign in instead."
            else:
                session.clear()
                session["user_id"] = cursor.lastrowid
                return redirect(url_for("index"))
    return render_template("auth.html", mode="register", error=error, values=values)


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))


# ─── Sample data ────────────────────────────────────────────────────────────

@app.route("/api/sample-data")
@login_required
def sample_data():
    sample_resume_dir = os.path.join(DATA_DIR, "sample_resumes")
    if not all(os.path.isfile(os.path.join(sample_resume_dir, name)) for name in SAMPLE_RESUME_FILENAMES):
        return jsonify({"error": "Generate the demo resumes first with: python data\\generate_samples.py"}), 404

    sample_jd_path = os.path.join(DATA_DIR, "sample_jd.txt")
    with open(sample_jd_path, "r", encoding="utf-8") as sample_jd_file:
        return jsonify({
            "job_description": sample_jd_file.read(),
            "resumes": sorted(SAMPLE_RESUME_FILENAMES),
        })


@app.route("/api/sample-resumes/<filename>")
@login_required
def sample_resume(filename):
    if filename not in SAMPLE_RESUME_FILENAMES:
        return jsonify({"error": "Sample resume not found"}), 404
    return send_from_directory(os.path.join(DATA_DIR, "sample_resumes"), filename)


@app.errorhandler(413)
def request_entity_too_large(_error):
    logger.warning("Rejected upload exceeding the configured request size limit")
    return jsonify({"error": "The upload is too large. The total request limit is 50 MB."}), 413


# ─── Legacy helpers (kept for backwards compatibility) ──────────────────────

def extract_text_from_pdf(file_bytes: bytes) -> str:
    try:
        reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
        if reader.is_encrypted:
            raise ValueError("Password-protected PDFs are not supported.")
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text.strip())
        extracted_text = "\n\n".join(pages).strip()
        if not extracted_text:
            raise ValueError("No selectable text found. Scanned PDFs require OCR and are not supported.")
        return extracted_text
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Unable to read PDF: {exc}") from exc


def extract_text_from_file(file_bytes: bytes, filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_RESUME_EXTENSIONS:
        raise ValueError("Unsupported file type. Upload a PDF or UTF-8 text file.")
    if ext == ".pdf":
        return extract_text_from_pdf(file_bytes)
    try:
        text = file_bytes.decode("utf-8-sig").strip()
    except UnicodeDecodeError as exc:
        raise ValueError("Text files must use UTF-8 encoding.") from exc
    if not text:
        raise ValueError("The file is empty.")
    return text


def pool_to_dict(pool) -> dict:
    return {
        "job_id": pool.job_id,
        "ranked": [
            {
                "id": c.profile.id,
                "rank": c.rank,
                "name": c.profile.name,
                "email": c.profile.email,
                "phone": c.profile.phone,
                "score": round(c.match.score, 2),
                "skills": c.profile.skills[:12],
                "experience_years": c.profile.experience_years,
                "education": c.profile.education,
                "certifications": c.profile.certifications,
                "summary": c.profile.summary,
                "rationale": c.rationale,
                "matched_requirements": c.match.matched_requirements,
                "missing_requirements": c.match.missing_requirements,
                "overall_assessment": c.match.overall_assessment,
                "guidance": c.guidance,
                "per_requirement_justification": [
                    {
                        "requirement": j.requirement,
                        "met": j.met,
                        "evidence": j.evidence,
                        "score_contribution": round(j.score_contribution, 2),
                    }
                    for j in c.match.per_requirement_justification
                ],
                "bias_flags": [
                    {
                        "text": f.rationale_text,
                        "attribute": f.protected_attribute,
                        "explanation": f.explanation,
                        "severity": f.severity,
                    }
                    for f in c.bias_flags
                ],
            }
            for c in pool.ranked
        ],
        "bias_report": (
            {
                "passed": pool.bias_report.passed,
                "flags": [
                    {
                        "text": f.rationale_text,
                        "attribute": f.protected_attribute,
                        "explanation": f.explanation,
                        "severity": f.severity,
                        "candidate_name": candidate.profile.name,
                        "candidate_rank": candidate.rank,
                    }
                    for candidate in pool.ranked
                    for f in candidate.bias_flags
                ],
                "overall_assessment": pool.bias_report.overall_assessment,
                "reviewed_candidates": pool.bias_report.reviewed_candidates,
            }
            if pool.bias_report
            else None
        ),
        "schedule": [
            {
                "candidate_name": s.candidate_name,
                "candidate_id": s.candidate_id,
                "candidate_email": next(
                    (c.profile.email for c in pool.ranked if c.profile.id == s.candidate_id), ""
                ),
                "rank": s.rank,
                "score": round(s.score, 2),
                "slot_date": s.slot_date,
                "slot_time": s.slot_time,
                "slot_datetime": s.slot_datetime,
                "interviewer": s.interviewer,
                "interviewer_role": s.interviewer_role,
                "mode": s.mode,
                "duration_minutes": s.duration_minutes,
                "meeting_link": s.meeting_link,
            }
            for s in pool.schedule
        ],
        "stages": [
            {
                "name": s.name,
                "status": s.status,
                "message": s.message,
                "started_at": s.started_at,
                "completed_at": s.completed_at,
            }
            for s in pool.stages
        ],
    }


# ─── Legacy pipeline endpoints ──────────────────────────────────────────────

@app.route("/api/run-pipeline", methods=["POST"])
@role_required("recruiter")
def run_pipeline_endpoint():
    key_error = gemini_key_error()
    if key_error:
        return jsonify({"error": key_error}), 503

    job_description = request.form.get("job_description", "").strip()
    if not job_description:
        return jsonify({"error": "Job description is required"}), 400

    resume_files = request.files.getlist("resumes")
    if not resume_files or all(f.filename == "" for f in resume_files):
        return jsonify({"error": "At least one resume file is required"}), 400
    if len([file for file in resume_files if file.filename]) > MAX_RESUMES_PER_REQUEST:
        return jsonify({"error": f"Upload no more than {MAX_RESUMES_PER_REQUEST} resumes at a time."}), 400

    try:
        top_n = int(request.form.get("top_n", 3))
    except (TypeError, ValueError):
        return jsonify({"error": "Interview slots must be a whole number from 3 to 5."}), 400
    top_n = max(3, min(5, top_n))

    resume_texts: Dict[str, str] = {}
    extraction_errors = []
    for file in resume_files:
        if not file.filename:
            continue
        filename = secure_filename(file.filename)
        if not filename:
            extraction_errors.append("One uploaded file has an invalid filename.")
            continue
        try:
            text = extract_text_from_file(file.read(), filename)
        except ValueError as exc:
            extraction_errors.append(f"{filename}: {exc}")
            continue
        unique_filename = filename
        suffix = 2
        while unique_filename in resume_texts:
            stem, extension = os.path.splitext(filename)
            unique_filename = f"{stem}_{suffix}{extension}"
            suffix += 1
        resume_texts[unique_filename] = text

    if extraction_errors:
        return jsonify({"error": "Some resumes could not be processed.", "details": extraction_errors}), 400
    if not resume_texts:
        return jsonify({"error": "Could not extract text from any uploaded files"}), 400

    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "status": "running",
        "progress": [],
        "result": None,
        "error": None,
        "created_at": time.time(),
        "owner_id": session["user_id"],
        "job_description": job_description,
        "invitations_sent": set(),
    }

    def run_in_background():
        def progress_callback(stage_name, status, message, pool):
            jobs[job_id]["progress"].append({
                "stage": stage_name,
                "status": status,
                "message": message,
                "candidate_count": len(pool.profiles),
                "timestamp": time.time(),
            })

        try:
            from agents.legacy.coordinator import PipelineCoordinator

            pool = PipelineCoordinator().run_pipeline(
                job_description=job_description,
                resume_texts=resume_texts,
                top_n=top_n,
                progress_callback=progress_callback,
            )
            jobs[job_id]["result"] = pool_to_dict(pool)
            jobs[job_id]["status"] = "complete"
        except Exception as exc:
            logger.error("Pipeline %s failed: %s", job_id, exc, exc_info=True)
            jobs[job_id]["error"] = (
                "We couldn't complete this review. Check your AI key and internet connection, then try again."
            )
            jobs[job_id]["status"] = "error"

    threading.Thread(target=run_in_background, daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/api/stream/<job_id>")
@role_required("recruiter")
def stream_job(job_id):
    if job_id not in jobs:
        return jsonify({"error": "Job not found"}), 404
    if jobs[job_id].get("owner_id") != session["user_id"]:
        return jsonify({"error": "This review is not available to your account."}), 403

    def generate():
        last_sent = 0
        start = time.time()
        while time.time() - start < 300:
            job = jobs.get(job_id, {})
            progress = job.get("progress", [])
            for update in progress[last_sent:]:
                payload = {
                    "stage": update["stage"],
                    "status": update["status"],
                    "message": update["message"],
                    "candidate_count": update["candidate_count"],
                }
                yield f"data: {json.dumps(payload)}\n\n"
                last_sent += 1
            if job.get("status") == "complete":
                yield f"data: {json.dumps({'status': 'complete', 'result': job.get('result')})}\n\n"
                return
            if job.get("status") == "error":
                yield f"data: {json.dumps({'status': 'error', 'error': job.get('error')})}\n\n"
                return
            time.sleep(0.5)
        yield f"data: {json.dumps({'status': 'error', 'error': 'Pipeline timed out'})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@app.route("/api/status/<job_id>")
@role_required("recruiter")
def job_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    if job.get("owner_id") != session["user_id"]:
        return jsonify({"error": "This review is not available to your account."}), 403
    return jsonify({
        "job_id": job_id,
        "status": job["status"],
        "progress": job.get("progress", []),
        "result": job.get("result") if job["status"] == "complete" else None,
        "error": job.get("error"),
    })


@app.route("/api/download/<job_id>")
@role_required("recruiter")
def download_report(job_id):
    job = jobs.get(job_id)
    if not job or job["status"] != "complete":
        return jsonify({"error": "Job not complete or not found"}), 404
    if job.get("owner_id") != session["user_id"]:
        return jsonify({"error": "This review is not available to your account."}), 403
    return Response(
        json.dumps(job["result"], indent=2),
        mimetype="application/json",
        headers={"Content-Disposition": f"attachment; filename=veyora_review_{job_id[:8]}.json"},
    )


@app.route("/api/candidate/review", methods=["POST"])
@role_required("candidate")
def candidate_review():
    key_error = gemini_key_error()
    if key_error:
        return jsonify({"error": key_error}), 503
    job_description = request.form.get("job_description", "").strip()
    resume_file = request.files.get("resume")
    if not job_description:
        return jsonify({"error": "Add the job description or role details first."}), 400
    if not resume_file or not resume_file.filename:
        return jsonify({"error": "Choose a PDF or text resume to review."}), 400
    filename = secure_filename(resume_file.filename)
    if not filename:
        return jsonify({"error": "The resume filename is not valid."}), 400
    try:
        resume_text = extract_text_from_file(resume_file.read(), filename)
        from agents.legacy.career_coach_agent import CareerCoachAgent
        from agents.legacy.matcher_agent import MatcherAgent
        from agents.legacy.parser_agent import ParserAgent

        profile = ParserAgent().run(resume_text, filename)
        match = MatcherAgent().run(profile, job_description)
        guidance = CareerCoachAgent().run(profile, job_description, match)
        return jsonify({
            "profile": {
                "name": profile.name,
                "email": profile.email,
                "skills": profile.skills,
                "experience_years": profile.experience_years,
                "education": profile.education,
                "summary": profile.summary,
            },
            "match": {
                "score": round(match.score, 1),
                "matched_requirements": match.matched_requirements,
                "missing_requirements": match.missing_requirements,
                "per_requirement_justification": [
                    {"requirement": item.requirement, "met": item.met, "evidence": item.evidence}
                    for item in match.per_requirement_justification
                ],
                "overall_assessment": match.overall_assessment,
            },
            "guidance": guidance,
        })
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        logger.exception("Candidate review failed")
        return jsonify({"error": "We couldn't finish this review. Check your Gemini key and try again."}), 502


@app.route("/api/email-settings")
@role_required("recruiter")
def email_settings():
    load_dotenv()
    configured = all(
        os.environ.get(name, "").strip() for name in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD")
    )
    return jsonify({"configured": configured})


@app.route("/api/invitations/<job_id>", methods=["POST"])
@role_required("recruiter")
def send_invitation(job_id):
    job = jobs.get(job_id)
    if not job or job.get("status") != "complete":
        return jsonify({"error": "Complete a resume review before sending an invitation."}), 404
    if job.get("owner_id") != session["user_id"]:
        return jsonify({"error": "This review is not available to your account."}), 403

    payload = request.get_json(silent=True) or {}
    candidate_id = payload.get("candidate_id", "") if isinstance(payload, dict) else ""
    candidate = next((item for item in job["result"]["ranked"] if item["id"] == candidate_id), None)
    slot = next((item for item in job["result"]["schedule"] if item["candidate_id"] == candidate_id), None)
    if not candidate or not slot:
        return jsonify({"error": "No suggested interview time was found for this candidate."}), 404
    if not candidate.get("email") or "@" not in candidate["email"]:
        return jsonify({"error": "This resume does not include a usable email address."}), 400
    sent = job.setdefault("invitations_sent", set())
    if candidate_id in sent:
        return jsonify({"error": "An invitation has already been sent for this suggested time."}), 409

    load_dotenv()
    host = os.environ.get("SMTP_HOST", "").strip()
    username = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "").strip()
    if not host or not username or not password:
        return jsonify({
            "error": "Email is not set up yet. Add SMTP_HOST, SMTP_USER, and SMTP_PASSWORD to .env before sending."
        }), 503

    message = EmailMessage()
    message["Subject"] = f"Interview invitation - {job.get('role_title') or 'your application'}"
    message["From"] = os.environ.get("SMTP_FROM", username).strip()
    message["To"] = candidate["email"]
    message.set_content(
        f"Hello {candidate['name']},\n\n"
        "We would like to invite you to an interview.\n\n"
        f"Suggested time: {slot['slot_datetime']}\n"
        f"Format: {slot['mode']}\n"
        f"Interviewer: {slot['interviewer']}\n"
        f"Duration: {slot['duration_minutes']} minutes\n\n"
        "Please reply to confirm whether this time works for you.\n\n"
        "Regards,\nHiring team"
    )
    try:
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=20) as smtp:
            smtp.starttls()
            smtp.login(username, password)
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException, ValueError):
        logger.exception("Could not send an interview invitation")
        return jsonify({"error": "The email could not be sent. Check the email settings and try again."}), 502

    sent.add(candidate_id)
    return jsonify({"message": "The invitation was sent."})


if __name__ == "__main__":
    if not os.environ.get("GEMINI_API_KEY"):
        logger.warning("GEMINI_API_KEY is not set. AI extraction falls back to local analysis.")

    port = int(os.environ.get("FLASK_PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "false").strip().lower() in {"1", "true", "yes"}
    logger.info("Starting Veyora AI on http://127.0.0.1:%s", port)
    app.run(debug=debug, host="127.0.0.1", port=port, threaded=True)
