"""
Gemini code-path tests with a mocked client.
===========================================
These tests exercise the real request-building, response-parsing and merge
logic in `agents/platform_agents.py` without calling Google. A fake client
stands in for `google.genai.Client`, so the suite runs with no valid API key
while still covering the `ai_mode = "gemini"` branches.

Deterministic guarantees are asserted too: the AI may write narrative, but the
compatibility score must always come from `services/scoring.py`.
"""

import json
import os
import unittest
from unittest.mock import patch

from agents import platform_agents as pa
from services.scoring import score_candidate


# ─── Fake Gemini client ─────────────────────────────────────────────────────

class FakeResponse:
    def __init__(self, text: str = ""):
        self.text = text


class FakeModels:
    def __init__(self, responses=None, error=None):
        self._responses = list(responses or [])
        self._error = error
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        if not self._responses:
            raise AssertionError("The agent made more Gemini calls than expected")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, (dict, list)):
            return FakeResponse(json.dumps(item))
        return FakeResponse(str(item))


class FakeClient:
    def __init__(self, *responses, error=None):
        self.models = FakeModels(responses, error)


RESUME_TEXT = """Priya Raman
priya.raman@example.test
+44 7700 900123

SUMMARY
Backend engineer with 7 years building data platforms in Python on AWS.

SKILLS
Python, AWS, Docker, PostgreSQL, REST API, CI/CD

EXPERIENCE
Senior Backend Engineer at Northwind Data (2019 - 2024)
Led migration of ingestion services to Kubernetes, cutting latency by 40%.

EDUCATION
BSc Computer Science, University of Edinburgh, 2017

PROJECTS
Event Pipeline: Python and Kafka pipeline processing 200k events per minute

CERTIFICATIONS
AWS Solutions Architect Associate
"""

JOB_DESCRIPTION = """Job Title: Senior Backend Engineer

Required Qualifications:
- 5+ years of Python development experience
- Strong AWS cloud services experience
- Docker and Kubernetes container experience

Preferred Qualifications:
- Kafka or message queues
- Terraform infrastructure as code

Responsibilities:
- Design and maintain backend services
"""


class GeminiPathTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(
            os.environ,
            {"GEMINI_API_KEY": "AIzaSy-test-key", "GEMINI_MODEL": "gemini-test-flash"},
            clear=False,
        )
        env.start()
        self.addCleanup(env.stop)

    def use_client(self, *responses, error=None):
        fake = FakeClient(*responses, error=error)
        patcher = patch.object(pa, "_client", return_value=fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake

    def fixtures(self):
        profile = pa.heuristic_profile(RESUME_TEXT, "priya_raman_resume.pdf")
        job = pa.heuristic_job(JOB_DESCRIPTION, "Senior Backend Engineer")
        report = score_candidate(profile, job)
        gaps = pa.heuristic_gaps(profile, job, report)["gaps"]
        return profile, job, report, gaps

    # ── configuration and transport ────────────────────────────────────────

    def test_ai_status_reports_gemini_when_key_is_present(self):
        self.assertTrue(pa.ai_available())
        self.assertEqual(pa.ai_mode(), "gemini")
        status = pa.ai_status()
        self.assertTrue(status["configured"])
        self.assertEqual(status["mode"], "gemini")
        self.assertEqual(status["model"], "gemini-test-flash")

    def test_groq_key_is_still_rejected(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "gsk_not-a-google-key"}, clear=False):
            self.assertFalse(pa.ai_available())
            self.assertEqual(pa.ai_mode(), "local")

    def test_llm_json_without_key_raises_instead_of_calling_out(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}, clear=False), \
                patch.object(pa, "_client", side_effect=AssertionError("must not build a client")):
            with self.assertRaises(RuntimeError):
                pa.llm_json("hello")

    def test_llm_json_strips_fences_and_extracts_embedded_json(self):
        fake = self.use_client(
            '```json\n{"answer": 1}\n```',
            'Here you go: {"answer": 2} — thanks!',
        )
        self.assertEqual(pa.llm_json("first"), {"answer": 1})
        self.assertEqual(pa.llm_json("second"), {"answer": 2})
        self.assertEqual(fake.models.calls[0]["model"], "gemini-test-flash")
        self.assertIn("Return ONLY a valid JSON object", fake.models.calls[0]["contents"])

    def test_llm_json_invalid_payload_raises(self):
        self.use_client("I cannot help with that.")
        with self.assertRaises(json.JSONDecodeError):
            pa.llm_json("prompt")

    # ── 1. Resume Intelligence Agent ───────────────────────────────────────

    def test_resume_agent_merges_ai_output(self):
        self.use_client({
            "name": "Priya Raman",
            "email": "priya.ai@example.test",
            "summary": "Backend engineer who ships reliable data platforms.",
            "technical_skills": ["Python", "AWS", "Kubernetes"],
            "total_years": 8,
            "experience": [{"title": "Senior Backend Engineer", "company": "Northwind Data"}],
            "certifications": ["AWS Solutions Architect Associate"],
        })
        result = pa.ResumeIntelligenceAgent().run(RESUME_TEXT, "priya_raman_resume.pdf")

        # normalize_profile() keeps only the documented profile fields, so the
        # AI path is proven by its values rather than a `source` marker.
        self.assertEqual(result["email"], "priya.ai@example.test")
        self.assertEqual(result["total_years"], 8)
        self.assertIn("Kubernetes", result["technical_skills"])
        self.assertEqual(result["experience"][0]["company"], "Northwind Data")
        self.assertEqual(result["summary"], "Backend engineer who ships reliable data platforms.")

    def test_resume_agent_falls_back_when_the_model_returns_junk(self):
        self.use_client("not json at all")
        result = pa.ResumeIntelligenceAgent().run(RESUME_TEXT, "priya_raman_resume.pdf")
        self.assertEqual(result["email"], "priya.raman@example.test")
        self.assertIn("Python", result["technical_skills"])

    # ── 2. Job Description Analyst ─────────────────────────────────────────

    def test_job_analyst_uses_ai_requirements_and_deduplicates(self):
        self.use_client({
            "title": "Staff Platform Engineer",
            "seniority": "Staff",
            "min_years": 6,
            "required_skills": ["Python", "AWS", "Kubernetes", "AWS"],
            "preferred_skills": ["Kafka", "AWS", "Terraform"],
            "competencies": ["Ownership", "Mentoring"],
            "responsibilities": ["Own the ingestion platform"],
        })
        job = pa.JobDescriptionAnalyst().run(JOB_DESCRIPTION, "Senior Backend Engineer")

        self.assertEqual(job["title"], "Staff Platform Engineer")
        self.assertEqual(job["min_years"], 6)
        self.assertEqual(job["required_skills"].count("AWS"), 1)
        self.assertIn("Kafka", job["preferred_skills"])
        self.assertNotIn("AWS", job["preferred_skills"])
        self.assertIn("Ownership", job["competencies"])

    # ── 3. Compatibility Scoring Agent ─────────────────────────────────────

    def test_scoring_agent_keeps_the_deterministic_score(self):
        profile, job, _, _ = self.fixtures()
        expected = score_candidate(profile, job)["score"]
        self.use_client({"rationale": "Strong alignment on Python, AWS and Docker."})

        report = pa.CompatibilityScoringAgent().run(profile, job)

        self.assertEqual(report["score"], expected)
        self.assertEqual(report["rationale"], "Strong alignment on Python, AWS and Docker.")
        self.assertEqual(report["grade"], score_candidate(profile, job)["grade"])

    def test_scoring_agent_keeps_score_when_narrative_fails(self):
        profile, job, _, _ = self.fixtures()
        expected = score_candidate(profile, job)["score"]
        self.use_client(error=RuntimeError("quota exceeded"))

        report = pa.CompatibilityScoringAgent().run(profile, job)

        self.assertEqual(report["score"], expected)
        self.assertIn(str(expected), report["rationale"])

    # ── 4. Skill Gap & Resume Coach ────────────────────────────────────────

    def test_skill_gap_coach_uses_ai_gaps_and_keeps_local_review_fields(self):
        profile, job, report, _ = self.fixtures()
        self.use_client({
            "gaps": [
                {
                    "skill": "Kubernetes",
                    "importance": "High",
                    "why": "The role runs production workloads on Kubernetes.",
                    "learn": ["probes", "resource limits", "rollouts"],
                    "practice_task": "Deploy a small API to a local cluster.",
                    "level": "partial",
                }
            ],
            "resume_review": {
                "strengths": ["Clear Python and AWS evidence."],
                "keywords": ["Kubernetes"],
            },
        })

        result = pa.SkillGapCoachAgent().run(profile, job, report)

        self.assertEqual(result["source"], "gemini")
        self.assertEqual(result["gaps"][0]["skill"], "Kubernetes")
        self.assertEqual(result["gaps"][0]["importance"], "High")
        self.assertEqual(result["gaps"][0]["learn"], ["probes", "resource limits", "rollouts"])
        self.assertEqual(result["resume_review"]["strengths"], ["Clear Python and AWS evidence."])
        # Untouched sections still come from the deterministic review.
        self.assertTrue(result["resume_review"]["formatting"])

    def test_skill_gap_coach_falls_back_on_client_error(self):
        profile, job, report, _ = self.fixtures()
        self.use_client(error=ConnectionError("network down"))
        result = pa.SkillGapCoachAgent().run(profile, job, report)
        self.assertEqual(result["source"], "local")
        self.assertTrue(result["gaps"])

    # ── 5. Interview Strategist ───────────────────────────────────────────

    def test_interview_strategist_builds_recruiter_questions_from_ai(self):
        profile, job, report, gaps = self.fixtures()
        self.use_client({
            "categories": {
                "technical": [
                    {
                        "question": "Describe a Kubernetes rollout you owned end to end.",
                        "purpose": "Verify production container operations.",
                        "expected_concepts": ["probes", "rollbacks"],
                        "evaluation_criteria": ["Specificity"],
                        "related_skill": "Kubernetes",
                    }
                ],
                "gap": [
                    {
                        "question": "How would you ramp up on Kafka in your first month?",
                        "purpose": "Test the learning plan for a gap.",
                        "expected_concepts": ["consumer groups"],
                        "evaluation_criteria": ["Honesty"],
                        "related_skill": "Kafka",
                    }
                ],
            },
            "critical_areas": [
                {
                    "area": "Kubernetes",
                    "claim": "Only a one-line mention in the resume.",
                    "why": "Mandatory requirement.",
                    "verify_with": "Ask for architecture decisions and incident examples.",
                }
            ],
        })

        plan = pa.InterviewStrategistAgent().run(profile, job, report, gaps)

        self.assertEqual(plan["source"], "gemini")
        technical = plan["categories"]["technical"]["questions"]
        self.assertEqual(technical[0]["question"], "Describe a Kubernetes rollout you owned end to end.")
        self.assertEqual(technical[0]["expected_concepts"], ["probes", "rollbacks"])
        self.assertEqual(plan["categories"]["gap"]["questions"][0]["related_skill"], "Kafka")
        # Categories the model omitted keep the local plan instead of going blank.
        self.assertTrue(plan["categories"]["behavioral"]["questions"])
        self.assertEqual(plan["critical_areas"][0]["verify_with"],
                         "Ask for architecture decisions and incident examples.")

    def test_interview_strategist_candidate_mode_includes_answer_scaffolds(self):
        profile, job, report, gaps = self.fixtures()
        self.use_client({
            "categories": {
                "technical": [
                    {
                        "question": "How do you design a resilient ingestion pipeline?",
                        "purpose": "Check design depth.",
                        "expected_concepts": ["retries", "idempotency"],
                        "evaluation_criteria": ["Trade-offs"],
                        "related_skill": "Python",
                        "what_tested": "Systems thinking",
                        "answer_structure": ["Context", "Design", "Result"],
                        "sample_answer": "Start with the failure modes.",
                        "common_mistakes": ["Ignoring backpressure"],
                    }
                ]
            }
        })

        plan = pa.InterviewStrategistAgent().run(profile, job, report, gaps, for_candidate=True)
        question = plan["categories"]["technical"]["questions"][0]

        self.assertEqual(question["what_tested"], "Systems thinking")
        self.assertEqual(question["answer_structure"], ["Context", "Design", "Result"])
        self.assertEqual(question["common_mistakes"], ["Ignoring backpressure"])

    def test_answer_feedback_uses_ai_score_and_list_fields(self):
        self.use_client({
            "score": 84,
            "strengths": ["Clear structure", "Used a real metric"],
            "improvements": ["Name the trade-off explicitly"],
            "model_answer": "Context, decision, measured outcome.",
            "concepts_covered": ["retries"],
            "next_step": "Re-answer with the trade-off included.",
        })
        question = {
            "question": "Tell us about a production incident you owned.",
            "expected_concepts": ["diagnosis", "retries"],
        }
        feedback = pa.InterviewStrategistAgent().review_answer(
            question, "I diagnosed a retry storm by reading traces and cut latency 40%."
        )

        self.assertEqual(feedback["source"], "gemini")
        self.assertEqual(feedback["score"], 84)
        self.assertEqual(feedback["strengths"], ["Clear structure", "Used a real metric"])
        self.assertEqual(feedback["concepts_covered"], ["retries"])
        self.assertEqual(feedback["next_step"], "Re-answer with the trade-off included.")

    def test_answer_feedback_clamps_out_of_range_scores(self):
        self.use_client({"score": 250, "strengths": ["x"]})
        feedback = pa.InterviewStrategistAgent().review_answer(
            {"question": "Q", "expected_concepts": []}, "A long enough answer with numbers 12."
        )
        self.assertEqual(feedback["score"], 100)
        self.assertEqual(feedback["source"], "gemini")

    # ── 7. Career Intelligence Agent ──────────────────────────────────────

    def test_career_agent_builds_roadmap_from_ai_weeks(self):
        profile, job, _, gaps = self.fixtures()
        self.use_client({
            "summary": "Close the container gap, then apply with one strong project.",
            "total_weeks": 5,
            "weeks": [
                {
                    "week": 1,
                    "focus": "Kubernetes fundamentals",
                    "goals": ["Understand pods and services"],
                    "exercises": ["Deploy a local cluster"],
                    "project": "Containerise the event pipeline",
                    "milestone": "A running deployment with probes",
                }
            ],
            "milestones": ["A public repository with a deployed service"],
        })

        roadmap = pa.CareerIntelligenceAgent().run(profile, job, gaps)

        self.assertEqual(roadmap["source"], "gemini")
        self.assertEqual(roadmap["total_weeks"], 5)
        self.assertEqual(roadmap["weeks"][0]["focus"], "Kubernetes fundamentals")
        self.assertEqual(roadmap["weeks"][0]["milestone"], "A running deployment with probes")
        self.assertEqual(roadmap["milestones"], ["A public repository with a deployed service"])

    def test_career_agent_falls_back_when_no_weeks_are_returned(self):
        profile, job, _, gaps = self.fixtures()
        self.use_client({"summary": "Empty", "total_weeks": 6, "weeks": [], "milestones": []})
        roadmap = pa.CareerIntelligenceAgent().run(profile, job, gaps)
        self.assertEqual(roadmap["source"], "local")
        self.assertTrue(roadmap["weeks"])


if __name__ == "__main__":
    unittest.main()
