"""
Seed Veyora AI with one recruiter and one candidate demo account
================================================================
Creates two accounts and fills them with *real* pipeline output (never
hardcoded scores) by calling the same HTTP endpoints the browser uses:

  recruiter  → job from data/sample_jd.txt, review of the 5 sample resumes,
                two shortlisting decisions, two interview slots (one confirmed)
  candidate  → parsed resume, resume review, compatibility, gaps,
                interview plan and a saved roadmap

Re-running the script resets only the two demo accounts and rebuilds their
data, so it is safe to use between rehearsals.

Usage
-----
    python tools/seed_demo.py
    python tools/seed_demo.py --password "MyDemoPass1"
    python tools/seed_demo.py --database instance/veyora.sqlite3

Requires the sample resumes from `python data\\generate_samples.py`.
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import time
from datetime import date, timedelta

from werkzeug.security import generate_password_hash

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app import app  # noqa: E402  (loads .env and registers blueprints)
from db import connect, init_db  # noqa: E402

DEFAULT_PASSWORD = "VeyoraDemo2026!"
JOB_TITLE = "Senior Python Developer"

RECRUITER = {"name": "Amara Okafor", "email": "recruiter@veyora.demo", "role": "recruiter"}
CANDIDATE = {"name": "David Kim", "email": "candidate@veyora.demo", "role": "candidate"}
CANDIDATE_RESUME = "david_kim_resume.pdf"

RESUME_DIR = os.path.join(BASE_DIR, "data", "sample_resumes")
JD_PATH = os.path.join(BASE_DIR, "data", "sample_jd.txt")

DEMO_TABLES = (
    ("email_log", "recruiter_id"),
    ("interview_slots", "recruiter_id"),
    ("applications", "recruiter_id"),
    ("reviews", "owner_id"),
    ("jobs", "owner_id"),
    ("resumes", "owner_id"),
    ("candidate_reports", "user_id"),
    ("roadmaps", "user_id"),
)


def _check_sample_data() -> str:
    if not os.path.isfile(JD_PATH):
        raise SystemExit(f"Missing job description: {JD_PATH}")
    if not os.path.isdir(RESUME_DIR) or not any(
        name.lower().endswith(".pdf") for name in os.listdir(RESUME_DIR)
    ):
        raise SystemExit(
            "Sample resumes are missing. Run: python data\\generate_samples.py"
        )
    with open(JD_PATH, "r", encoding="utf-8") as handle:
        return handle.read()


def reset_demo_rows(connection) -> None:
    """Delete the demo accounts and every row they own. Other data is untouched."""
    emails = (RECRUITER["email"], CANDIDATE["email"])
    ids = [
        row["id"]
        for row in connection.execute(
            "SELECT id FROM users WHERE email IN (?, ?)", emails
        ).fetchall()
    ]
    if not ids:
        return
    marks = ",".join("?" * len(ids))
    for table, column in DEMO_TABLES:
        connection.execute(f"DELETE FROM {table} WHERE {column} IN ({marks})", ids)
    connection.execute(f"DELETE FROM users WHERE id IN ({marks})", ids)
    connection.commit()


def create_user(connection, person, password: str) -> int:
    cursor = connection.execute(
        "INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, ?, ?)",
        (person["name"], person["email"], generate_password_hash(password), person["role"]),
    )
    connection.commit()
    return int(cursor.lastrowid)


def make_client(user_id: int):
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
    return client


def sample_resume_files() -> list:
    files = []
    for name in sorted(os.listdir(RESUME_DIR)):
        if not name.lower().endswith(".pdf"):
            continue
        with open(os.path.join(RESUME_DIR, name), "rb") as handle:
            files.append((io.BytesIO(handle.read()), name))
    return files


def _expect(response, label: str, *statuses):
    if response.status_code not in statuses:
        raise SystemExit(
            f"{label} failed ({response.status_code}): {response.get_data(as_text=True)[:400]}"
        )
    return response.get_json(silent=True) or {}


def poll_review(client, review_id: str, timeout: float = 240.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = _expect(client.get(f"/api/reviews/{review_id}"), "review status", 200)
        if payload["status"] == "complete":
            return payload["result"]
        if payload["status"] == "error":
            raise SystemExit(f"The review pipeline failed: {payload.get('error')}")
        time.sleep(0.3)
    raise SystemExit("The review did not finish within 4 minutes.")


def seed_recruiter(recruiter_id: int, jd_text: str) -> dict:
    client = make_client(recruiter_id)

    analysed = _expect(
        client.post("/api/jobs/analyze", json={"description": jd_text, "title": JOB_TITLE}),
        "job analysis",
        200,
    )["job"]
    job = _expect(
        client.post(
            "/api/jobs",
            json={
                "title": analysed.get("title") or JOB_TITLE,
                "description": jd_text,
                "required_skills": analysed.get("required_skills", []),
                "preferred_skills": analysed.get("preferred_skills", []),
                "min_years": analysed.get("min_years", 0),
                "seniority": analysed.get("seniority", "Senior"),
            },
        ),
        "job creation",
        201,
    )["job"]

    started = _expect(
        client.post(
            "/api/reviews/start",
            data={"job_id": job["id"], "resumes": sample_resume_files()},
            content_type="multipart/form-data",
        ),
        "review start",
        202,
    )
    result = poll_review(client, started["review_id"])

    applications = _expect(
        client.get(f"/api/jobs/{job['id']}/applications"), "applications", 200
    )["applications"]
    if not applications:
        raise SystemExit("The review produced no candidate records.")

    decisions = []
    for application, status in zip(applications, ("shortlisted", "interview")):
        _expect(
            client.post(
                f"/api/applications/{application['id']}/status", json={"status": status}
            ),
            "shortlisting",
            200,
        )
        decisions.append((application["name"], application["score"], status))

    top = applications[0]
    day_one = date.today() + timedelta(days=2)
    day_two = date.today() + timedelta(days=3)

    slot = _expect(
        client.post(
            "/api/interview-slots",
            json={
                "date": day_one.isoformat(),
                "start_time": "10:00",
                "end_time": "11:00",
                "timezone": "Europe/London",
                "interviewer": "Amara Okafor",
                "mode": "Video",
                "location": "https://meet.example.com/veyora-demo",
                "application_id": top["id"],
                "candidate_name": top["name"],
                "candidate_email": top["email"],
                "job_id": job["id"],
                "instructions": "60-minute technical interview. Portfolio and code samples welcome.",
            },
        ),
        "slot creation",
        201,
    )["slot"]
    _expect(client.post(f"/api/interview-slots/{slot['id']}/confirm"), "slot confirm", 200)
    _expect(
        client.post(
            "/api/interview-slots",
            json={
                "date": day_two.isoformat(),
                "start_time": "14:00",
                "end_time": "15:00",
                "timezone": "Europe/London",
                "interviewer": "Amara Okafor",
                "mode": "Video",
                "location": "https://meet.example.com/veyora-demo",
                "job_id": job["id"],
                "instructions": "Open slot reserved for the next shortlisted candidate.",
            },
        ),
        "open slot creation",
        201,
    )

    return {
        "job": job,
        "review": result,
        "applications": applications,
        "decisions": decisions,
        "confirmed_slot": slot,
        "open_slot_date": day_two.isoformat(),
    }


def seed_candidate(candidate_id: int, jd_text: str) -> dict:
    client = make_client(candidate_id)
    path = os.path.join(RESUME_DIR, CANDIDATE_RESUME)
    if not os.path.isfile(path):
        raise SystemExit(f"Missing resume: {path}")

    with open(path, "rb") as handle:
        payload = handle.read()
    uploaded = _expect(
        client.post(
            "/api/candidate/resume",
            data={"resume": (io.BytesIO(payload), CANDIDATE_RESUME)},
            content_type="multipart/form-data",
        ),
        "candidate resume upload",
        201,
    )

    body = {
        "resume_id": uploaded["resume_id"],
        "job_description": jd_text,
        "job_title": JOB_TITLE,
    }
    reports = {}
    for endpoint in ("resume-review", "compatibility", "skill-gaps", "interview-prep", "roadmap"):
        reports[endpoint] = _expect(
            client.post(f"/api/candidate/{endpoint}", json=body), endpoint, 200
        )

    return {"profile": uploaded["profile"], "reports": reports}


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed Veyora AI demo accounts.")
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="Password for both demo accounts.")
    parser.add_argument("--database", default="", help="Override the SQLite database path.")
    args = parser.parse_args()

    if args.database:
        app.config["DATABASE"] = os.path.abspath(args.database)
        init_db(app.config["DATABASE"])

    jd_text = _check_sample_data()

    connection = connect(app.config["DATABASE"])
    try:
        reset_demo_rows(connection)
        recruiter_id = create_user(connection, RECRUITER, args.password)
        candidate_id = create_user(connection, CANDIDATE, args.password)
    finally:
        connection.close()

    print(f"Database: {app.config['DATABASE']}")
    print("Seeding recruiter workspace (running the real review pipeline)...")
    recruiter = seed_recruiter(recruiter_id, jd_text)
    print("Seeding candidate workspace...")
    candidate = seed_candidate(candidate_id, jd_text)

    applications = recruiter["applications"]
    print("\nDemo accounts created")
    print(f"  Recruiter  {RECRUITER['email']}  /  {args.password}")
    print(f"  Candidate  {CANDIDATE['email']}  /  {args.password}")
    print(
        f"\nJob: {recruiter['job']['title']} "
        f"({len(recruiter['job']['required_skills'])} required skills)"
    )
    print(f"Candidates reviewed: {len(applications)}")
    for application in applications:
        print(f"  - {application['name']}: {application['score']}/100")
    print("Shortlisting: " + ", ".join(f"{name} -> {status}" for name, _, status in recruiter["decisions"]))
    print(f"Interview slot confirmed for {recruiter['confirmed_slot']['candidate_name']}")
    print(f"Open slot on {recruiter['open_slot_date']} at 14:00")
    print(
        f"Candidate compatibility: {candidate['reports']['compatibility']['score']}/100, "
        f"roadmap {candidate['reports']['roadmap']['plan'].get('total_weeks')} weeks"
    )
    print("\nStart the app with: python app.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
