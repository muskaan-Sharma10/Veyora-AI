"""End-to-end tests for the Veyora AI recruiter and candidate workflows.

The suite forces the deterministic local analysis path so it runs without any
external API credentials while still exercising the real backend pipeline.
"""

import io
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from app import app
from services.scoring import score_candidate

RESUME_TEXT = """Priya Raman
priya.raman@example.test
+44 7700 900123

SUMMARY
Backend engineer with 7 years building data platforms in Python on AWS.

SKILLS
Python, AWS, Docker, PostgreSQL, REST API, CI/CD, Communication, Leadership

EXPERIENCE
Senior Backend Engineer at Northwind Data (2019 - 2024)
Led migration of ingestion services to Kubernetes, cutting latency by 40%.
Built REST APIs in Python serving 3 million requests per day.

Backend Developer at Bright Labs (2017 - 2019)
Designed PostgreSQL schemas and automated deployments with Docker.

EDUCATION
BSc Computer Science, University of Edinburgh, 2017

PROJECTS
Event Pipeline: Python and Kafka pipeline processing 200k events per minute
Cost Dashboard: AWS cost analytics service used by 12 teams

CERTIFICATIONS
AWS Solutions Architect Associate
"""

SECOND_RESUME = """Sam Okafor
sam.okafor@example.test

SUMMARY
Junior frontend developer focused on React and design systems.

SKILLS
JavaScript, React, HTML, CSS, Git

EDUCATION
BSc Software Engineering, University of Leeds, 2023

PROJECTS
Portfolio site built with React and CSS
"""

JOB_DESCRIPTION = """Job Title: Senior Backend Engineer

Required Qualifications:
- 5+ years of Python development experience
- Strong AWS cloud services experience
- Docker and Kubernetes container experience
- REST API design with FastAPI or Django
- PostgreSQL in production

Preferred Qualifications:
- Kafka or message queues
- Terraform infrastructure as code

Responsibilities:
- Design and maintain backend services
"""


def poll_review(client, review_id, timeout=25.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = client.get(f"/api/reviews/{review_id}")
        data = response.get_json()
        if data["status"] in {"complete", "error"}:
            return data
        time.sleep(0.1)
    raise AssertionError("review did not finish in time")


class PlatformTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.previous_database = app.config["DATABASE"]
        app.config["DATABASE"] = os.path.join(self.temp_dir.name, "test.sqlite3")
        self.addCleanup(lambda: app.config.__setitem__("DATABASE", self.previous_database))

        self.ai_patch = patch("agents.platform_agents.ai_available", return_value=False)
        self.ai_patch.start()
        self.addCleanup(self.ai_patch.stop)

        email_env = patch.dict(os.environ, {
            "GEMINI_API_KEY": "test-key",
            "SMTP_HOST": "",
            "SMTP_USER": "",
            "SMTP_PASSWORD": "",
        })
        email_env.start()
        self.addCleanup(email_env.stop)
        app.config["TESTING"] = True

    def recruiter_client(self):
        client = app.test_client()
        response = client.post("/register", data={
            "name": "Rita Recruiter",
            "email": f"recruiter-{time.time_ns()}@example.test",
            "password": "test-password-123",
            "role": "recruiter",
        })
        self.assertEqual(response.status_code, 302)
        return client

    def candidate_client(self):
        client = app.test_client()
        response = client.post("/register", data={
            "name": "Cara Candidate",
            "email": f"candidate-{time.time_ns()}@example.test",
            "password": "test-password-123",
            "role": "candidate",
        })
        self.assertEqual(response.status_code, 302)
        return client

    def start_review(self, client, resumes=(RESUME_TEXT, SECOND_RESUME)):
        data = {
            "job_title": "Senior Backend Engineer",
            "job_description": JOB_DESCRIPTION,
            "resumes": [
                (io.BytesIO(text.encode("utf-8")), f"resume_{index}.txt")
                for index, text in enumerate(resumes)
            ],
        }
        return client.post("/api/reviews/start", data=data, content_type="multipart/form-data")


class PageTests(PlatformTestCase):
    def test_landing_page_offers_both_roles(self):
        client = app.test_client()
        response = client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.data.decode("utf-8")
        self.assertIn("Login as Recruiter", html)
        self.assertIn("Login as Candidate", html)
        self.assertIn("Intelligent Recruitment. Smarter Career Growth.", html)

    def test_role_workspaces_are_separate(self):
        recruiter = self.recruiter_client()
        html = recruiter.get("/").data.decode("utf-8")
        self.assertIn("Recruiter workspace", html)
        self.assertIn("Candidate Review", html)
        self.assertNotIn("Candidate workspace", html)

        candidate = self.candidate_client()
        html = candidate.get("/").data.decode("utf-8")
        self.assertIn("Candidate workspace", html)
        self.assertIn("Get ready for your next opportunity", html)
        self.assertNotIn("Candidate Review Workspace", html)

    def test_cross_role_api_access_is_blocked(self):
        candidate = self.candidate_client()
        self.assertEqual(candidate.post("/api/reviews/start").status_code, 403)
        self.assertEqual(candidate.get("/api/dashboard").status_code, 403)

        recruiter = self.recruiter_client()
        self.assertEqual(recruiter.post("/api/candidate/resume").status_code, 403)
        self.assertEqual(recruiter.get("/api/jobs").status_code, 200)

    def test_anonymous_api_calls_are_rejected(self):
        client = app.test_client()
        self.assertEqual(client.get("/api/jobs").status_code, 401)


class ScoringTests(unittest.TestCase):
    def profile(self):
        return {
            "name": "Candidate",
            "technical_skills": ["Python", "Docker", "PostgreSQL"],
            "soft_skills": ["Communication"],
            "total_years": 6,
            "projects": [{"name": "API", "description": "Python REST API on AWS"}],
            "education": [{"degree": "BSc Computer Science"}],
            "certifications": ["AWS Solutions Architect"],
            "experience": [{"title": "Backend Engineer", "company": "Acme"}],
            "raw_text": "Python Docker PostgreSQL AWS REST API",
        }

    def test_score_is_deterministic_and_auditable(self):
        job = {
            "title": "Backend Engineer",
            "description": "Python AWS Docker",
            "required_skills": ["Python", "AWS", "Docker"],
            "preferred_skills": ["Kafka"],
            "min_years": 5,
        }
        first = score_candidate(self.profile(), job)
        second = score_candidate(self.profile(), job)
        self.assertEqual(first["score"], second["score"])
        self.assertGreaterEqual(first["score"], 0)
        self.assertLessEqual(first["score"], 100)
        weights = sum(part["weight"] for part in first["breakdown"])
        self.assertAlmostEqual(weights, 100, places=1)
        self.assertIn("Python", first["matched_skills"])
        self.assertIn("Kafka", first["missing_skills"])
        self.assertTrue(first["methodology"])
        self.assertTrue(first["reasons"])

    def test_missing_experience_reduces_score(self):
        job = {"title": "Role", "description": "", "required_skills": ["Python"], "min_years": 10}
        junior = dict(self.profile(), total_years=1)
        senior = dict(self.profile(), total_years=12)
        self.assertLess(score_candidate(junior, job)["score"], score_candidate(senior, job)["score"])


class RecruiterReviewTests(PlatformTestCase):
    def test_full_review_pipeline_returns_all_sections(self):
        client = self.recruiter_client()
        response = self.start_review(client)
        self.assertEqual(response.status_code, 202, response.get_data(as_text=True))
        review_id = response.get_json()["review_id"]

        data = poll_review(client, review_id)
        self.assertEqual(data["status"], "complete", data.get("error"))
        result = data["result"]
        self.assertEqual(len(result["candidates"]), 2)
        self.assertEqual(result["ai_mode"], "local")

        top = result["candidates"][0]
        for key in ("profile", "compatibility", "gaps", "questions", "application_id", "score"):
            self.assertIn(key, top)
        self.assertGreater(top["score"], 0)
        self.assertIn("breakdown", top["compatibility"])
        self.assertTrue(top["compatibility"]["matched_skills"])
        self.assertTrue(top["gaps"]["gaps"])
        self.assertTrue(top["questions"]["categories"]["technical"]["questions"])
        self.assertTrue(top["questions"]["critical_areas"])
        self.assertIn("shortlisted", ["under_review", "shortlisted"])

        job_id = result["job_id"]
        applications = client.get(f"/api/jobs/{job_id}/applications").get_json()["applications"]
        self.assertEqual(len(applications), 2)
        self.assertEqual(applications[0]["score"], max(item["score"] for item in applications))

        updated = client.post(
            f"/api/applications/{applications[0]['id']}/status",
            json={"status": "shortlisted"},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["application"]["status"], "shortlisted")

        comparison = client.get(f"/api/jobs/{job_id}/comparison")
        self.assertEqual(comparison.status_code, 200)
        self.assertEqual(len(comparison.get_json()["candidates"]), 2)

    def test_review_requires_job_and_resumes(self):
        client = self.recruiter_client()
        response = client.post("/api/reviews/start", data={}, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 400)
        self.assertIn("job description", response.get_json()["error"].lower())

        response = client.post(
            "/api/reviews/start",
            data={"job_description": JOB_DESCRIPTION},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("resume", response.get_json()["error"].lower())

    def test_unsupported_resume_type_is_reported(self):
        client = self.recruiter_client()
        response = client.post(
            "/api/reviews/start",
            data={
                "job_description": JOB_DESCRIPTION,
                "resumes": (io.BytesIO(b"not a resume"), "resume.exe"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Unsupported file type", " ".join(response.get_json()["details"]))

    def test_saved_job_can_be_reused_for_a_review(self):
        client = self.recruiter_client()
        job = client.post("/api/jobs", json={
            "title": "Backend Engineer",
            "description": JOB_DESCRIPTION,
            "required_skills": "Python, AWS",
            "min_years": 4,
        })
        self.assertEqual(job.status_code, 201)
        job_id = job.get_json()["job"]["id"]

        response = client.post(
            "/api/reviews/start",
            data={
                "job_id": job_id,
                "resumes": (io.BytesIO(RESUME_TEXT.encode()), "resume.txt"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 202)
        data = poll_review(client, response.get_json()["review_id"])
        self.assertEqual(data["status"], "complete")
        self.assertEqual(data["result"]["job_id"], job_id)

    def test_job_analysis_endpoint_extracts_requirements(self):
        client = self.recruiter_client()
        response = client.post("/api/jobs/analyze", json={"description": JOB_DESCRIPTION})
        self.assertEqual(response.status_code, 200)
        job = response.get_json()["job"]
        self.assertIn("Python", job["required_skills"])
        self.assertGreaterEqual(job["min_years"], 5)


class InterviewManagementTests(PlatformTestCase):
    def create_slot(self, client, date="2030-05-06", start="10:00", end="11:00", **extra):
        payload = {"date": date, "start_time": start, "end_time": end, "timezone": "UTC", "mode": "Video"}
        payload.update(extra)
        return client.post("/api/interview-slots", json=payload)

    def test_double_booking_is_prevented(self):
        client = self.recruiter_client()
        first = self.create_slot(client)
        self.assertEqual(first.status_code, 201)

        overlap = self.create_slot(client, start="10:30", end="11:30")
        self.assertEqual(overlap.status_code, 409)

        adjacent = self.create_slot(client, start="11:00", end="12:00")
        self.assertEqual(adjacent.status_code, 201)

    def test_invalid_times_are_rejected(self):
        client = self.recruiter_client()
        self.assertEqual(self.create_slot(client, start="11:00", end="10:00").status_code, 400)
        self.assertEqual(self.create_slot(client, start="25:00", end="26:00").status_code, 400)

    def test_assign_confirm_and_cancel(self):
        client = self.recruiter_client()
        review = self.start_review(client, resumes=(RESUME_TEXT,))
        data = poll_review(client, review.get_json()["review_id"])
        candidate = data["result"]["candidates"][0]

        slot = self.create_slot(client).get_json()["slot"]
        assigned = client.post(f"/api/interview-slots/{slot['id']}/assign", json={
            "application_id": candidate["application_id"],
        })
        self.assertEqual(assigned.status_code, 200)
        self.assertEqual(assigned.get_json()["slot"]["status"], "reserved")
        self.assertEqual(assigned.get_json()["slot"]["candidate_name"], candidate["name"])

        confirmed = client.post(f"/api/interview-slots/{slot['id']}/confirm")
        self.assertEqual(confirmed.get_json()["slot"]["status"], "confirmed")

        cancelled = client.delete(f"/api/interview-slots/{slot['id']}")
        self.assertEqual(cancelled.get_json()["slot"]["status"], "cancelled")

    def test_email_preview_and_demo_send_are_logged_once(self):
        client = self.recruiter_client()
        review = self.start_review(client, resumes=(RESUME_TEXT,))
        candidate = poll_review(client, review.get_json()["review_id"])["result"]["candidates"][0]

        slot = self.create_slot(client).get_json()["slot"]
        client.post(f"/api/interview-slots/{slot['id']}/assign", json={"application_id": candidate["application_id"]})

        preview = client.post(f"/api/interview-slots/{slot['id']}/email-preview", json={})
        self.assertEqual(preview.status_code, 200)
        preview_data = preview.get_json()
        self.assertFalse(preview_data["email_configured"])
        self.assertIn("Interview invitation", preview_data["subject"])
        self.assertIn(candidate["email"], preview_data["to"])
        self.assertIn("UTC", preview_data["body"])

        sent = client.post(f"/api/interview-slots/{slot['id']}/send-email", json={
            "to_email": candidate["email"],
            "subject": preview_data["subject"],
            "body": preview_data["body"],
        })
        self.assertEqual(sent.status_code, 200)
        self.assertEqual(sent.get_json()["status"], "demo")
        self.assertFalse(sent.get_json()["sent"])
        self.assertIn("NOT sent", sent.get_json()["message"])

        duplicate = client.post(f"/api/interview-slots/{slot['id']}/send-email", json={})
        self.assertEqual(duplicate.status_code, 409)

        log = client.get("/api/email-log").get_json()["emails"]
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["status"], "demo")


class CandidateWorkspaceTests(PlatformTestCase):
    def upload(self, client, text=RESUME_TEXT):
        return client.post(
            "/api/candidate/resume",
            data={"resume": (io.BytesIO(text.encode()), "my_resume.txt")},
            content_type="multipart/form-data",
        )

    def test_resume_review_compatibility_gaps_and_prep(self):
        client = self.candidate_client()
        upload = self.upload(client)
        self.assertEqual(upload.status_code, 201)
        resume_id = upload.get_json()["resume_id"]
        self.assertIn("Python", upload.get_json()["profile"]["technical_skills"])

        review = client.post("/api/candidate/resume-review", json={"resume_id": resume_id})
        self.assertEqual(review.status_code, 200)
        review_data = review.get_json()
        self.assertIn("resume_review", review_data)
        self.assertIn("strengths", review_data["resume_review"])
        self.assertIn("formatting", review_data["resume_review"])

        compatibility = client.post("/api/candidate/compatibility", json={
            "resume_id": resume_id, "job_description": JOB_DESCRIPTION,
        })
        self.assertEqual(compatibility.status_code, 200)
        compat = compatibility.get_json()
        self.assertGreater(compat["score"], 50)
        self.assertTrue(compat["breakdown"])
        self.assertIn("AWS", compat["matched_skills"])

        gaps = client.post("/api/candidate/skill-gaps", json={
            "resume_id": resume_id, "job_description": JOB_DESCRIPTION,
        })
        self.assertEqual(gaps.status_code, 200)
        self.assertTrue(gaps.get_json()["gaps"])

        prep = client.post("/api/candidate/interview-prep", json={
            "resume_id": resume_id, "job_description": JOB_DESCRIPTION,
        })
        self.assertEqual(prep.status_code, 200)
        prep_data = prep.get_json()
        technical = prep_data["categories"]["technical"]["questions"]
        self.assertTrue(technical)
        self.assertIn("sample_answer", technical[0])
        self.assertIn("common_mistakes", technical[0])

        practice = client.post("/api/candidate/practice", json={
            "question": technical[0],
            "answer": (
                "In my last role I built a Python ingestion service on AWS. The situation was "
                "growing traffic, so I chose a queue based design, cut latency 40%, and documented trade-offs."
            ),
        })
        self.assertEqual(practice.status_code, 200)
        self.assertGreater(practice.get_json()["feedback"]["score"], 0)

    def test_roadmap_is_saved_with_progress(self):
        client = self.candidate_client()
        resume_id = self.upload(client).get_json()["resume_id"]
        roadmap = client.post("/api/candidate/roadmap", json={
            "resume_id": resume_id, "job_description": JOB_DESCRIPTION,
        })
        self.assertEqual(roadmap.status_code, 200)
        plan = roadmap.get_json()["plan"]
        self.assertTrue(plan["weeks"])
        self.assertTrue(plan["milestones"])

        progress = client.post("/api/candidate/roadmap/progress", json={"week": 1, "completed": True})
        self.assertEqual(progress.status_code, 200)
        self.assertTrue(progress.get_json()["progress"]["1"])

        latest = client.get("/api/candidate/roadmap").get_json()["roadmap"]
        self.assertTrue(latest["progress"]["1"])

    def test_missing_resume_returns_actionable_error(self):
        client = self.candidate_client()
        response = client.post("/api/candidate/compatibility", json={"job_description": JOB_DESCRIPTION})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Upload your resume", response.get_json()["error"])

    def test_unsupported_candidate_file_is_rejected(self):
        client = self.candidate_client()
        response = client.post(
            "/api/candidate/resume",
            data={"resume": (io.BytesIO(b"binary"), "resume.rtf")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
