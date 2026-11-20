import uuid
import logging
from agents.legacy.base_agent import BaseAgent
from memory.candidate_pool import CandidateProfile

logger = logging.getLogger(__name__)


class ParserAgent(BaseAgent):
    """
    Parser Agent: Extracts structured information (skills, experience, education)
    from raw resume text into a CandidateProfile.
    """

    def __init__(self):
        super().__init__(
            name="Parser Agent",
            description="Extracts structured candidate data from resume text",
        )

    def run(self, resume_text: str, filename: str = "") -> CandidateProfile:
        """
        Parse a resume text string into a structured CandidateProfile.
        """
        logger.info(f"[ParserAgent] Parsing resume: {filename}")

        prompt = f"""You are an expert HR data extraction specialist. Your task is to extract structured information from the following resume text.

RESUME TEXT:
--------------------
{resume_text}
--------------------

Extract ALL information and return a JSON object with this EXACT structure:
{{
    "name": "Full legal name of candidate (string)",
    "email": "Email address (string, empty string if not found)",
    "phone": "Phone number (string, empty string if not found)",
    "skills": ["Python", "AWS", "Docker", "...ALL technical skills mentioned"],
    "experience_years": 5.5,
    "education": [
        {{
            "degree": "Bachelor of Science in Computer Science",
            "institution": "MIT",
            "year": "2018"
        }}
    ],
    "certifications": ["AWS Solutions Architect", "...all certifications"],
    "work_experience": [
        {{
            "title": "Senior Python Developer",
            "company": "TechCorp",
            "duration": "2 years",
            "description": "Built scalable APIs and microservices"
        }}
    ],
    "summary": "2-3 sentence professional summary highlighting key qualifications"
}}

RULES:
- experience_years: Calculate TOTAL professional work experience as a decimal number (e.g., 7.5)
- skills: Extract EVERY skill mentioned: programming languages, frameworks, tools, cloud services, databases, methodologies
- Do NOT infer information not present in the resume
- If name is unclear, use the filename hint: {filename}"""

        try:
            data = self.call_llm_json(prompt)
            profile = CandidateProfile(
                id=str(uuid.uuid4()),
                name=data.get("name", filename.replace(".pdf", "").replace("_", " ").title()),
                email=data.get("email", ""),
                phone=data.get("phone", ""),
                skills=data.get("skills", []),
                experience_years=float(data.get("experience_years", 0)),
                education=data.get("education", []),
                certifications=data.get("certifications", []),
                work_experience=data.get("work_experience", []),
                summary=data.get("summary", ""),
                raw_text=resume_text,
            )
            logger.info(f"[ParserAgent] Parsed: {profile.name} | Skills: {len(profile.skills)} | Exp: {profile.experience_years}y")
            return profile

        except Exception as e:
            logger.error(f"[ParserAgent] Failed to parse {filename}: {e}")
            raise RuntimeError(f"Could not parse resume '{filename}'.") from e
