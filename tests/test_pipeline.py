import io
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from app import app, extract_text_from_file, gemini_key_error, jobs
from agents.legacy.base_agent import BaseAgent
from agents.legacy.bias_guard_agent import BiasGuardAgent
from agents.legacy.scheduler_agent import SchedulerAgent
from memory.candidate_pool import CandidateProfile, MatchResult, RankedCandidate
from tools.availability_tool import AvailabilityTool


class ResumeInputTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        previous_database = app.config["DATABASE"]
        app.config["DATABASE"] = os.path.join(self.temp_dir.name, "test.sqlite3")
        self.addCleanup(lambda: app.config.__setitem__("DATABASE", previous_database))
        env_patch = patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"})
        env_patch.start()
        self.addCleanup(env_patch.stop)
        self.client = app.test_client()
        self.client.post(
            "/register",
            data={
                "name": "Test Recruiter",
                "email": "recruiter@example.test",
                "password": "test-password-123",
                "role": "recruiter",
            },
        )

    def test_text_resume_is_extracted(self):
        self.assertEqual(
            extract_text_from_file(b"\xef\xbb\xbfPython engineer", "resume.txt"),
            "Python engineer",
        )

    def test_unsupported_file_type_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unsupported file type"):
            extract_text_from_file(b"not a resume", "resume.docx")

    def test_invalid_slot_count_returns_bad_request(self):
        response = self.client.post(
            "/api/run-pipeline",
            data={
                "job_description": "Python engineer",
                "top_n": "not-a-number",
                "resumes": (io.BytesIO(b"Python engineer"), "resume.txt"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("whole number", response.get_json()["error"])

    def test_unsupported_upload_returns_explanation(self):
        response = self.client.post(
            "/api/run-pipeline",
            data={
                "job_description": "Python engineer",
                "resumes": (io.BytesIO(b"resume"), "resume.docx"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Unsupported file type", response.get_json()["details"][0])

    def test_demo_data_endpoint_serves_job_description_and_resumes(self):
        response = self.client.get("/api/sample-data")
        self.assertEqual(response.status_code, 200)
        sample = response.get_json()
        self.assertEqual(len(sample["resumes"]), 5)
        self.assertIn("Required Qualifications", sample["job_description"])

        for filename in sample["resumes"]:
            with self.subTest(filename=filename):
                resume_response = self.client.get(f"/api/sample-resumes/{filename}")
                self.assertEqual(resume_response.status_code, 200)
                self.assertTrue(resume_response.data.startswith(b"%PDF"))
                self.assertTrue(extract_text_from_file(resume_response.data, filename))
                resume_response.close()

    def test_dashboard_describes_services_in_plain_language(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Resume summaries", response.data)
        self.assertNotIn(b"Pipeline Agents", response.data)

    def test_missing_ai_key_returns_beginner_friendly_message(self):
        with patch("app.load_dotenv"), patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            response = self.client.post("/api/run-pipeline")
        self.assertEqual(response.status_code, 503)
        self.assertIn(".env file", response.get_json()["error"])

    def test_key_from_another_provider_gets_a_clear_setup_error(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "gsk_test_not_a_real_key"}):
            message = gemini_key_error()
        self.assertIn("different AI service", message)
        self.assertNotIn("gsk_test_not_a_real_key", message)

    def test_unknown_demo_resume_is_not_served(self):
        response = self.client.get("/api/sample-resumes/../../app.py")
        self.assertEqual(response.status_code, 404)

    def test_candidate_account_uses_candidate_workspace_and_cannot_start_recruiter_review(self):
        response = self.client.post(
            "/register",
            data={
                "name": "Test Candidate",
                "email": "candidate@example.test",
                "password": "test-password-123",
                "role": "candidate",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(b"Get ready for your next opportunity", self.client.get("/").data)
        forbidden = self.client.post("/api/run-pipeline")
        self.assertEqual(forbidden.status_code, 403)
        self.client.post("/logout")
        wrong_role = self.client.post(
            "/login",
            data={
                "email": "candidate@example.test",
                "password": "test-password-123",
                "role": "recruiter",
            },
        )
        self.assertIn(b"do not match", wrong_role.data)
        correct_role = self.client.post(
            "/login",
            data={
                "email": "candidate@example.test",
                "password": "test-password-123",
                "role": "candidate",
            },
        )
        self.assertEqual(correct_role.status_code, 302)

    def test_candidate_review_returns_fit_and_career_guidance(self):
        recruiter_response = self.client.post("/api/candidate/review")
        self.assertEqual(recruiter_response.status_code, 403)
        self.client.post(
            "/register",
            data={
                "name": "Candidate",
                "email": "candidate-review@example.test",
                "password": "test-password-123",
                "role": "candidate",
            },
        )
        profile = CandidateProfile(
            id="candidate-1",
            name="Candidate",
            email="candidate-review@example.test",
            phone="",
            skills=["Python"],
            experience_years=2,
            education=[],
            certifications=[],
            work_experience=[],
            summary="Python developer",
            raw_text="Python developer",
        )
        match = MatchResult(
            candidate_id=profile.id,
            score=82,
            matched_requirements=["Python"],
            missing_requirements=["Cloud experience"],
            per_requirement_justification=[],
            overall_assessment="Good match based on Python experience.",
        )
        guidance = {
            "gap_summary": "Cloud experience is not clear.",
            "learning_steps": ["Practice a cloud deployment."],
            "resume_improvements": ["Add project outcomes."],
            "interview_questions": ["Describe a Python project."],
        }
        with (
            patch("app.gemini_key_error", return_value=None),
            patch("agents.legacy.parser_agent.ParserAgent") as parser,
            patch("agents.legacy.matcher_agent.MatcherAgent") as matcher,
            patch("agents.legacy.career_coach_agent.CareerCoachAgent") as coach,
        ):
            parser.return_value.run.return_value = profile
            matcher.return_value.run.return_value = match
            coach.return_value.run.return_value = guidance
            response = self.client.post(
                "/api/candidate/review",
                data={
                    "job_description": "Python role",
                    "resume": (io.BytesIO(b"Python developer"), "resume.txt"),
                },
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertEqual(result["match"]["score"], 82)
        self.assertEqual(result["guidance"]["learning_steps"], guidance["learning_steps"])
        self.assertEqual(result["guidance"]["interview_questions"], guidance["interview_questions"])

    def test_interview_email_is_sent_only_after_explicit_request(self):
        with self.client.session_transaction() as browser_session:
            owner_id = browser_session["user_id"]
        jobs["test-review"] = {
            "status": "complete",
            "owner_id": owner_id,
            "result": {
                "ranked": [{
                    "id": "candidate-1",
                    "name": "Candidate",
                    "email": "candidate@example.test",
                }],
                "schedule": [{
                    "candidate_id": "candidate-1",
                    "slot_datetime": "2030-01-02 09:00",
                    "mode": "Video",
                    "interviewer": "Hiring Lead",
                    "duration_minutes": 60,
                }],
            },
            "invitations_sent": set(),
        }
        self.addCleanup(lambda: jobs.pop("test-review", None))
        smtp = Mock()
        smtp.__enter__ = Mock(return_value=smtp)
        smtp.__exit__ = Mock(return_value=False)
        with patch.dict(os.environ, {
            "SMTP_HOST": "smtp.example.test",
            "SMTP_PORT": "587",
            "SMTP_USER": "sender@example.test",
            "SMTP_PASSWORD": "test-password",
        }), patch("app.smtplib.SMTP", return_value=smtp):
            response = self.client.post(
                "/api/invitations/test-review",
                json={"candidate_id": "candidate-1"},
            )
            duplicate = self.client.post(
                "/api/invitations/test-review",
                json={"candidate_id": "candidate-1"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(duplicate.status_code, 409)
        smtp.send_message.assert_called_once()


class SchedulerTests(unittest.TestCase):
    def test_schedules_available_candidates_when_pool_is_smaller_than_three(self):
        scheduler = SchedulerAgent.__new__(SchedulerAgent)
        scheduler.availability_tool = AvailabilityTool()
        candidates = []
        for index in range(2):
            profile = CandidateProfile(
                id=f"candidate-{index}",
                name=f"Candidate {index}",
                email="",
                phone="",
                skills=["Python"],
                experience_years=3.0,
                education=[],
                certifications=[],
                work_experience=[],
                summary="",
                raw_text="",
            )
            match = MatchResult(
                candidate_id=profile.id,
                score=80.0 - index,
                matched_requirements=["Python"],
                missing_requirements=[],
                per_requirement_justification=[],
                overall_assessment="",
            )
            candidates.append(
                RankedCandidate(
                    rank=index + 1,
                    profile=profile,
                    match=match,
                    rationale="",
                    bias_flags=[],
                )
            )

        schedule = scheduler.run(candidates, top_n=5)

        self.assertEqual(len(schedule), 2)
        self.assertTrue(all(slot.meeting_link is None for slot in schedule))
        self.assertEqual(len(scheduler.availability_tool.get_booked_slots()), 2)


class GeminiClientTests(unittest.TestCase):
    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-key", "GEMINI_MODEL": "test-model"})
    @patch("agents.legacy.base_agent.genai.Client")
    def test_base_agent_uses_configured_google_genai_client(self, client_factory):
        client = Mock()
        client.models.generate_content.return_value.text = "response"
        client_factory.return_value = client

        agent = BaseAgent("Test Agent", "Test description")

        self.assertEqual(agent.call_llm("prompt"), "response")
        client_factory.assert_called_once_with(api_key="test-key")
        client.models.generate_content.assert_called_once_with(
            model="test-model",
            contents="prompt",
        )


class BiasAuditTests(unittest.TestCase):
    def test_audit_error_is_not_reported_as_a_pass(self):
        guard = BiasGuardAgent.__new__(BiasGuardAgent)
        profile = CandidateProfile(
            id="candidate-1",
            name="Candidate",
            email="",
            phone="",
            skills=[],
            experience_years=0,
            education=[],
            certifications=[],
            work_experience=[],
            summary="",
            raw_text="",
        )
        match = MatchResult(
            candidate_id=profile.id,
            score=50,
            matched_requirements=[],
            missing_requirements=[],
            per_requirement_justification=[],
            overall_assessment="",
        )
        candidate = RankedCandidate(
            rank=1,
            profile=profile,
            match=match,
            rationale="",
            bias_flags=[],
        )

        with patch.object(guard, "call_llm_json", side_effect=RuntimeError("API unavailable")):
            with self.assertRaisesRegex(RuntimeError, "Bias review could not be completed"):
                guard.run([candidate])


if __name__ == "__main__":
    unittest.main()
