"""
Veyora AI — the eight platform agents.
======================================
1. ResumeIntelligenceAgent   – parses a resume into structured data
2. JobDescriptionAnalyst     – extracts structured job requirements
3. CompatibilityScoringAgent – explainable weighted match score
4. SkillGapCoachAgent        – skill gaps + resume critique
5. InterviewStrategistAgent  – tailored interview plan + practice feedback
6. RecruitmentSchedulingAgent– interview slot suggestions, auto-booking and invitation drafting
7. ScreeningDecisionAgent    – automatic shortlist/reject decision (60/100 rule)
8. CareerIntelligenceAgent   – personalised learning roadmap

Every agent uses Google Gemini when GEMINI_API_KEY is configured and falls
back to deterministic, data-derived logic otherwise. Scores are always
computed by services.scoring, never by the model.
"""

import json
import logging
import os
import re
import threading
import time
from datetime import date
from typing import Any, Dict, List, Optional

from services.scoring import normalize_job, normalize_profile, score_candidate
from services.skills import (
    canonicalize,
    dedupe_skills,
    evidence_sentence,
    extract_skills,
    extract_soft_skills,
    skill_in_text,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3.8-flash"

SKILL_TOPICS = {
    "Python": ["typing & project structure", "testing with pytest", "async I/O and performance profiling"],
    "AWS": ["IAM and least-privilege access", "EC2/S3/Lambda fundamentals", "cost and reliability trade-offs"],
    "Docker": ["image layering and caching", "multi-stage builds", "compose-based local environments"],
    "Kubernetes": ["pods, deployments and services", "probes and resource limits", "rollouts and rollbacks"],
    "SQL": ["joins and aggregation", "indexes and query plans", "transactions and isolation"],
    "React": ["component and state design", "data fetching patterns", "accessibility and performance"],
    "TypeScript": ["generics and narrowing", "strict mode adoption", "API contract typing"],
    "Kafka": ["topics, partitions and offsets", "consumer groups and rebalancing", "delivery guarantees"],
    "Terraform": ["state and modules", "plan/apply workflow", "environment separation"],
    "CI/CD": ["pipeline design", "caching and test parallelisation", "safe deployment strategies"],
    "System Design": ["capacity estimation", "caching and queues", "failure modes and observability"],
}

DEFAULT_TOPICS = ["core concepts and vocabulary", "a hands-on exercise", "one production-style project"]


# ─── Gemini plumbing ────────────────────────────────────────────────────────

def gemini_key() -> str:
    return os.environ.get("GEMINI_API_KEY", "").strip()


def ai_available() -> bool:
    key = gemini_key()
    if not key:
        return False
    if key.lower().startswith(("gsk_", "sk-proj", "your_", "changeme")):
        return False
    return True


def ai_mode() -> str:
    return "gemini" if ai_available() else "local"


def ai_status() -> Dict[str, Any]:
    configured = ai_available()
    return {
        "configured": configured,
        "mode": "gemini" if configured else "local",
        "model": os.environ.get("GEMINI_MODEL", DEFAULT_MODEL) if configured else "",
        "message": (
            f"Gemini is connected ({os.environ.get('GEMINI_MODEL', DEFAULT_MODEL)})."
            if configured
            else "No Gemini API key found. Deterministic local analysis is being used. "
            "Add GEMINI_API_KEY to .env for richer AI extraction, coaching and interview questions."
        ),
    }


def _client():
    from google import genai

    timeout_ms = int(os.environ.get("GEMINI_TIMEOUT_MS", "30000"))
    try:
        from google.genai import types

        return genai.Client(api_key=gemini_key(), http_options=types.HttpOptions(timeout=timeout_ms))
    except Exception:  # older/newer SDK shapes: fall back to SDK defaults
        return genai.Client(api_key=gemini_key())


def _is_transient(exc: Exception) -> bool:
    """True for rate-limit / capacity errors that are worth retrying."""
    text = f"{type(exc).__name__} {exc}"
    return any(
        marker in text
        for marker in ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "high demand", "overloaded")
    )


# When Google's endpoint is rate-limited or out of quota, every agent in the
# same review would otherwise wait through the same retries before falling
# back. Remember the failure briefly and let the remaining agents use local
# logic immediately, so a review stays fast instead of stalling for minutes.
_PROVIDER_DOWN_UNTIL = 0.0
_PROVIDER_LOCK = threading.Lock()
QUOTA_COOLDOWN_SECONDS = 600
TRANSIENT_COOLDOWN_SECONDS = 60


def provider_available() -> bool:
    return time.time() >= _PROVIDER_DOWN_UNTIL


def _mark_provider_down(seconds: int, reason: str) -> None:
    global _PROVIDER_DOWN_UNTIL
    with _PROVIDER_LOCK:
        _PROVIDER_DOWN_UNTIL = max(_PROVIDER_DOWN_UNTIL, time.time() + seconds)
    logger.warning(
        "Gemini paused for %ss (%s); using local logic until the cooldown ends.",
        seconds,
        reason,
    )


def _quota_exhausted(exc: Exception) -> bool:
    """True when the key is out of quota for the day (retrying will not help)."""
    text = f"{exc}"
    return "per day limit" in text or "GenerateRequestsPerDay" in text


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def llm_json(prompt: str, *, max_tokens: int = 4096) -> Dict[str, Any]:
    """Call Gemini and return parsed JSON. Raises on any failure."""
    if not ai_available():
        raise RuntimeError("Gemini is not configured")
    if not provider_available():
        raise RuntimeError("Gemini is temporarily unavailable; using local analysis")

    model = os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
    client = _client()
    instruction = (
        "\n\nReturn ONLY a valid JSON object. No markdown, no commentary. "
        "Use only information supported by the input; never invent facts."
    )
    try:
        from google.genai import types

        config: Optional[Any] = types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.2,
            max_output_tokens=max_tokens,
        )
    except Exception:
        config = None

    response = None
    last_error: Optional[Exception] = None
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model=model, contents=prompt + instruction, config=config
            )
            break
        except Exception as exc:
            last_error = exc
            if _quota_exhausted(exc):
                break  # daily quota: retrying now cannot succeed
            if _is_transient(exc) and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            break

    if response is None and last_error is not None and _is_transient(last_error):
        if _quota_exhausted(last_error):
            _mark_provider_down(QUOTA_COOLDOWN_SECONDS, "daily quota exhausted")
        else:
            _mark_provider_down(TRANSIENT_COOLDOWN_SECONDS, "service unavailable")
        raise last_error

    if response is None and config is not None:
        # Some models reject the structured-output config; retry plain once.
        try:
            response = client.models.generate_content(model=model, contents=prompt + instruction)
        except Exception as exc:
            if _is_transient(exc):
                if _quota_exhausted(exc):
                    _mark_provider_down(QUOTA_COOLDOWN_SECONDS, "daily quota exhausted")
                else:
                    _mark_provider_down(TRANSIENT_COOLDOWN_SECONDS, "service unavailable")
            raise
    if response is None:
        raise last_error or RuntimeError("The AI model did not respond")

    text = _strip_fences(getattr(response, "text", "") or "")
    if not text:
        raise RuntimeError("The AI model returned an empty response")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            return json.loads(match.group())
        raise


def _safe_list(value: Any) -> List[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _safe_dict_list(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if isinstance(item, dict):
            result.append({str(k): v for k, v in item.items()})
        elif isinstance(item, str) and item.strip():
            result.append({"description": item.strip()})
    return result


def _guess_title(description: str) -> str:
    match = re.search(r"(?:job\s*)?title\s*[:\-]\s*(.+)", description or "", re.IGNORECASE)
    if match:
        return match.group(1).split("\n")[0].strip()[:120]
    first = next((line.strip() for line in (description or "").splitlines() if line.strip()), "")
    return first[:120]


# ─── 1. Resume Intelligence Agent ───────────────────────────────────────────

SECTION_ALIASES = {
    "summary": ["summary", "professional summary", "profile", "objective", "about me", "about"],
    "experience": ["experience", "work experience", "professional experience", "employment",
                   "employment history", "career history", "work history"],
    "education": ["education", "academic background", "academics", "qualifications"],
    "skills": ["skills", "technical skills", "core competencies", "technologies", "tech stack",
               "technical proficiency", "expertise"],
    "projects": ["projects", "personal projects", "selected projects", "academic projects", "portfolio"],
    "certifications": ["certifications", "certificates", "licenses", "credentials"],
    "achievements": ["achievements", "awards", "honors", "accomplishments", "awards & honors"],
    "internships": ["internships", "internship experience", "internship"],
}

HEADER_LOOKUP = {alias: key for key, aliases in SECTION_ALIASES.items() for alias in aliases}
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"(\+?\d{1,3}[\s.-]?)?(\(?\d{2,4}\)?[\s.-]?){2,4}\d{2,4}")
YEAR_RANGE_RE = re.compile(r"(19\d{2}|20\d{2})\s*(?:-|–|—|to)\s*(19\d{2}|20\d{2}|present|current)", re.I)


def _heading_key(line: str) -> Optional[str]:
    cleaned = re.sub(r"[^a-z& ]", "", line.strip().lower())
    cleaned = " ".join(cleaned.split())
    if not cleaned or len(cleaned) > 44:
        return None
    if cleaned in HEADER_LOOKUP:
        return HEADER_LOOKUP[cleaned]
    for alias, key in HEADER_LOOKUP.items():
        if len(alias) > 5 and cleaned.startswith(alias) and len(cleaned) - len(alias) <= 4:
            return key
    return None


def _split_sections(text: str) -> Dict[str, List[str]]:
    sections: Dict[str, List[str]] = {key: [] for key in SECTION_ALIASES}
    current = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        key = _heading_key(line)
        if key:
            current = key
            continue
        if current:
            sections[current].append(line)
    return sections


def _bullets(lines: List[str]) -> List[str]:
    items: List[str] = []
    buffer = ""
    for line in lines:
        if re.match(r"^[•\-*–—]\s+", line) or re.match(r"^\d+[.)]\s+", line):
            if buffer:
                items.append(buffer.strip())
            buffer = re.sub(r"^[•\-*–—]\s+|^\d+[.)]\s+", "", line)
        elif buffer and len(line) > 60:
            buffer += " " + line
        else:
            if buffer:
                items.append(buffer.strip())
                buffer = ""
            items.append(line.strip())
    if buffer:
        items.append(buffer.strip())
    return [item for item in items if item]


def _detect_name(text: str, filename: str) -> str:
    for line in text.splitlines()[:8]:
        candidate = line.strip()
        if not candidate or len(candidate) > 60 or len(candidate.split()) > 6:
            continue
        lowered = candidate.lower()
        if "@" in candidate or any(ch.isdigit() for ch in candidate):
            continue
        if lowered in HEADER_LOOKUP or "resume" in lowered or "curriculum" in lowered:
            continue
        if re.search(r"[|•]", candidate):
            continue
        return candidate.title() if candidate.isupper() else candidate
    stem = re.sub(r"\.(pdf|docx|txt)$", "", filename or "", flags=re.I)
    stem = re.sub(r"(?i)(resume|cv|final|updated)", " ", stem.replace("_", " ").replace("-", " "))
    return " ".join(stem.split()).title() or "Candidate"


def _total_years(text: str) -> float:
    stated = [float(m) for m in re.findall(r"(\d{1,2}(?:\.\d)?)\+?\s*years?", text, re.I)]
    stated = [value for value in stated if 0 < value <= 45]
    ranges = []
    this_year = date.today().year
    for start, end in YEAR_RANGE_RE.findall(text):
        start_year = int(start)
        end_year = this_year if end.lower() in {"present", "current"} else int(end)
        if end_year > start_year >= 1980 and end_year <= this_year + 1:
            ranges.append((start_year, end_year))
    range_years = 0
    if ranges:
        ranges.sort()
        merged = [list(ranges[0])]
        for start, end in ranges[1:]:
            if start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        range_years = sum(end - start for start, end in merged)
    return float(max(stated + [min(range_years, 40)]))


def heuristic_profile(text: str, filename: str = "") -> Dict[str, Any]:
    """Deterministic resume parsing used when Gemini is unavailable."""
    sections = _split_sections(text)
    email_match = EMAIL_RE.search(text)
    phone_match = PHONE_RE.search(re.sub(r"\S+@\S+", " ", text))
    phone = phone_match.group(0).strip() if phone_match else ""
    if sum(ch.isdigit() for ch in phone) < 7:
        phone = ""

    skills = dedupe_skills(extract_skills("\n".join(sections["skills"]) or text))
    if not skills:
        skills = dedupe_skills(extract_skills(text))

    education = []
    for line in _bullets(sections["education"])[:4]:
        year = re.search(r"(19|20)\d{2}", line)
        education.append({
            "degree": line[:160],
            "institution": "",
            "year": year.group(0) if year else "",
        })

    experience = []
    experience_lines = sections["experience"]
    blocks: List[List[str]] = []
    for line in experience_lines:
        if re.match(r"^[•\-*–—]\s+", line) and blocks:
            blocks[-1].append(re.sub(r"^[•\-*–—]\s+", "", line))
        elif len(blocks) >= 1 and len(line) > 40 and not re.search(r"(?i)\b(20\d{2}|19\d{2})\b", line) is None:
            blocks[-1].append(line)
        else:
            blocks.append([line])
    for block in blocks[:6]:
        if not block:
            continue
        heading = block[0]
        parts = re.split(r"\s+(?:at|@|\||-|–)\s+", heading, maxsplit=1)
        experience.append({
            "title": parts[0][:120],
            "company": parts[1][:120] if len(parts) > 1 else "",
            "duration": (re.search(r"((19|20)\d{2}\s*(?:-|–|to)\s*(?:present|current|(19|20)\d{2}))", heading, re.I) or [""])[0]
            if re.search(r"((19|20)\d{2}\s*(?:-|–|to)\s*(?:present|current|(19|20)\d{2}))", heading, re.I) else "",
            "description": " ".join(block[1:])[:600],
        })

    projects = []
    for block in _bullets(sections["projects"])[:6]:
        projects.append({"name": block.split(":")[0][:90], "description": block[:500]})

    summary = " ".join(sections["summary"])[:700]

    return normalize_profile({
        "name": _detect_name(text, filename),
        "email": email_match.group(0) if email_match else "",
        "phone": phone,
        "summary": summary or text[:280],
        "technical_skills": skills,
        "soft_skills": extract_soft_skills(text),
        "experience": experience,
        "internships": [{"title": item} for item in _bullets(sections["internships"])[:4]],
        "projects": projects,
        "education": education,
        "certifications": _bullets(sections["certifications"])[:8],
        "achievements": _bullets(sections["achievements"])[:8],
        "total_years": _total_years(text),
        "raw_text": text,
    })


class ResumeIntelligenceAgent:
    """Resume Intelligence Agent: parses and structures resume content."""

    name = "Resume Intelligence Agent"

    def run(self, resume_text: str, filename: str = "") -> Dict[str, Any]:
        fallback = heuristic_profile(resume_text, filename)
        if not ai_available():
            return fallback

        prompt = f"""You are the Resume Intelligence Agent. Extract structured data from this resume.

RESUME ({filename}):
\"\"\"
{resume_text[:14000]}
\"\"\"

Return JSON with exactly these keys:
{{
  "name": "", "email": "", "phone": "", "location": "",
  "links": [],
  "summary": "2-3 sentence professional summary using only resume facts",
  "technical_skills": ["hard skills, tools, languages, frameworks"],
  "soft_skills": ["soft skills evidenced in the resume"],
  "total_years": 0,
  "experience": [{{"title": "", "company": "", "duration": "", "description": ""}}],
  "internships": [{{"title": "", "company": "", "duration": "", "description": ""}}],
  "projects": [{{"name": "", "description": "", "technologies": []}}],
  "education": [{{"degree": "", "institution": "", "year": ""}}],
  "certifications": [],
  "achievements": []
}}

RULES:
- Never invent information. Leave a field empty/[] when the resume does not contain it.
- total_years is total professional experience in years (decimal allowed), computed from employment dates.
- technical_skills must use short canonical names (e.g. "Python", "AWS", "PostgreSQL").
- Only include a soft skill if the resume demonstrates it."""

        try:
            data = llm_json(prompt)
            merged = dict(fallback)
            for key in ("name", "email", "phone", "location", "summary"):
                value = str(data.get(key) or "").strip()
                if value:
                    merged[key] = value
            for key in ("links", "technical_skills", "soft_skills", "certifications", "achievements"):
                values = data.get(key)
                if isinstance(values, list) and values:
                    merged[key] = [str(v).strip() for v in values if str(v).strip()]
            for key in ("experience", "internships", "projects", "education"):
                values = data.get(key)
                if isinstance(values, list) and values:
                    merged[key] = _safe_dict_list(values)
            try:
                years = float(data.get("total_years") or 0)
                if years > 0:
                    merged["total_years"] = min(years, 50)
            except (TypeError, ValueError):
                pass
            merged["technical_skills"] = dedupe_skills(merged["technical_skills"])
            merged["raw_text"] = resume_text
            merged["source"] = "gemini"
            return normalize_profile(merged)
        except Exception as exc:
            logger.warning("[%s] AI parse failed (%s); using local extraction", self.name, exc)
            fallback["source"] = "local"
            return fallback


# ─── 2. Job Description Analyst ─────────────────────────────────────────────

REQUIRED_HEADINGS = re.compile(r"(?i)(required|requirements|must[- ]have|qualifications|you have)")
PREFERRED_HEADINGS = re.compile(r"(?i)(preferred|nice[- ]to[- ]have|bonus|plus)")


def heuristic_job(description: str, title: str = "") -> Dict[str, Any]:
    lines = [line.strip() for line in description.splitlines() if line.strip()]
    required_skills: List[str] = []
    preferred_skills: List[str] = []
    responsibilities: List[str] = []
    mode = "required"

    for line in lines:
        if PREFERRED_HEADINGS.search(line) and len(line) < 60:
            mode = "preferred"
            continue
        if REQUIRED_HEADINGS.search(line) and len(line) < 60:
            mode = "required"
            continue
        if re.search(r"(?i)^(responsibilities|what you.ll do|the role|duties)", line):
            mode = "responsibilities"
            continue
        if mode == "responsibilities":
            responsibilities.append(re.sub(r"^[•\-*–—]\s+", "", line))
            continue
        found = extract_skills(line)
        (preferred_skills if mode == "preferred" else required_skills).extend(found)

    if not required_skills:
        required_skills = extract_skills(description)[:8]
        preferred_skills = [s for s in extract_skills(description)[8:14] if s not in required_skills]

    years_match = re.search(r"(\d{1,2})\+?\s*years?", description, re.I)
    min_years = float(years_match.group(1)) if years_match else 0.0

    return normalize_job({
        "title": title or _guess_title(description),
        "description": description,
        "required_skills": dedupe_skills(required_skills),
        "preferred_skills": [s for s in dedupe_skills(preferred_skills) if s not in required_skills],
        "min_years": min_years,
        "competencies": extract_soft_skills(description)[:4],
        "responsibilities": responsibilities[:8],
        "source": "local",
    })


class JobDescriptionAnalyst:
    """Job Description Analyst: turns free-text role details into requirements."""

    name = "Job Description Analyst"

    def run(self, description: str, title: str = "") -> Dict[str, Any]:
        fallback = heuristic_job(description, title)
        if not ai_available():
            return fallback

        prompt = f"""You are the Job Description Analyst. Extract hiring requirements.

JOB TITLE HINT: {title or "(unknown)"}
JOB DESCRIPTION:
\"\"\"
{description[:12000]}
\"\"\"

Return JSON:
{{
  "title": "",
  "seniority": "Junior|Mid|Senior|Lead|Principal or empty",
  "min_years": 0,
  "education_requirement": "",
  "required_skills": ["must-have hard skills"],
  "preferred_skills": ["nice-to-have hard skills"],
  "competencies": ["soft skills / behavioural competencies the role needs"],
  "responsibilities": ["main duties"]
}}

RULES:
- Separate must-have requirements from preferred ones. Do not mix them.
- Use short canonical skill names ("Python", "AWS", "Docker", "PostgreSQL").
- min_years is the minimum years of experience stated in the description (0 if not stated).
- Only include skills the description actually asks for."""

        try:
            data = llm_json(prompt)
            merged = dict(fallback)
            merged["title"] = str(data.get("title") or "").strip() or fallback["title"]
            merged["seniority"] = str(data.get("seniority") or "").strip()
            merged["education_requirement"] = str(data.get("education_requirement") or "").strip()
            for key in ("required_skills", "preferred_skills", "competencies", "responsibilities"):
                values = [str(v).strip() for v in (data.get(key) or []) if str(v).strip()]
                if values:
                    merged[key] = values
            try:
                merged["min_years"] = max(0.0, float(data.get("min_years") or 0))
            except (TypeError, ValueError):
                pass
            merged["required_skills"] = dedupe_skills(merged["required_skills"])
            merged["preferred_skills"] = [
                s for s in dedupe_skills(merged["preferred_skills"])
                if s not in merged["required_skills"]
            ]
            merged["description"] = description
            merged["source"] = "gemini"
            return normalize_job(merged)
        except Exception as exc:
            logger.warning("[%s] AI analysis failed (%s); using local extraction", self.name, exc)
            return fallback


# ─── 3. Compatibility Scoring Agent ─────────────────────────────────────────

class CompatibilityScoringAgent:
    """Compatibility Scoring Agent: deterministic score + evidence narrative."""

    name = "Compatibility Scoring Agent"

    def run(self, profile: Dict[str, Any], job: Dict[str, Any]) -> Dict[str, Any]:
        report = score_candidate(profile, job)
        report["rationale"] = self._rationale(report)
        report["strengths"] = [s for s in report["matched_skills"]][:6]
        report["concerns"] = [s for s in report["missing_skills"]][:6]
        return report

    def _rationale(self, report: Dict[str, Any]) -> str:
        position = report.get("job", {})
        title = position.get("title") or "this role"
        matched = report["matched_skills"][:3]
        missing = report["missing_skills"][:3]
        parts = [f"{report['grade']} for {title}: {report['score']}/100."]
        if matched:
            parts.append("Meets " + ", ".join(matched) + ".")
        if missing:
            parts.append("Gaps: " + ", ".join(missing) + ".")
        if not ai_available():
            return " ".join(parts)

        prompt = f"""You are the Compatibility Scoring Agent. The score has already been
calculated by a deterministic rubric and MUST NOT be changed.

Score: {report['score']}/100 ({report['grade']})
Job: {json.dumps(position)[:3000]}
Matched skills: {', '.join(report['matched_skills']) or 'none'}
Partial skills: {', '.join(report['partial_skills']) or 'none'}
Missing skills: {', '.join(report['missing_skills']) or 'none'}
Experience: {report['profile']['total_years']} years

Write JSON: {{"rationale": "2 sentences explaining the score using only the evidence above"}}
Do not mention names, age, gender, nationality or any personal attribute."""
        try:
            data = llm_json(prompt, max_tokens=300)
            rationale = str(data.get("rationale") or "").strip()
            return rationale or " ".join(parts)
        except Exception as exc:
            logger.info("[%s] narrative skipped: %s", self.name, exc)
            return " ".join(parts)


# ─── 4. Skill Gap & Resume Coach ────────────────────────────────────────────

WEAK_PHRASES = [
    "responsible for", "worked on", "helped with", "involved in", "duties included",
    "assisted with", "participated in",
]


def heuristic_gaps(profile: Dict[str, Any], job: Dict[str, Any], report: Dict[str, Any]) -> Dict[str, Any]:
    required = job.get("required_skills", [])
    gaps = []
    for skill in report["missing_skills"]:
        importance = "High" if skill in required else "Medium"
        gaps.append({
            "skill": skill,
            "importance": importance,
            "why": (
                f"{skill} is listed as a mandatory requirement for {job.get('title') or 'this role'}."
                if skill in required
                else f"{skill} is a preferred skill that would strengthen the application."
            ),
            "learn": SKILL_TOPICS.get(skill, DEFAULT_TOPICS),
            "practice_task": (
                f"Build a small project that uses {skill} end to end and add a short "
                f"section to the resume describing the problem, your approach and the measurable result."
            ),
            "level": "missing",
        })
    for skill in report["partial_skills"]:
        gaps.append({
            "skill": skill,
            "importance": "Medium",
            "why": f"{skill} appears only indirectly in the resume, so its depth is unclear.",
            "learn": SKILL_TOPICS.get(skill, DEFAULT_TOPICS),
            "practice_task": f"Document one concrete {skill} contribution with scope, decisions and outcome.",
            "level": "partial",
        })

    gaps.sort(key=lambda gap: (0 if gap["importance"] == "High" else 1, gap["skill"]))
    return {"gaps": gaps[:10], "resume_review": heuristic_resume_review(profile, job, report), "source": "local"}


def heuristic_resume_review(profile: Dict[str, Any], job: Dict[str, Any], report: Dict[str, Any]) -> Dict[str, Any]:
    raw = profile.get("raw_text", "") or ""
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    weak_hits = [phrase for phrase in WEAK_PHRASES if phrase in raw.lower()]
    quantified = len(re.findall(r"\b\d+(?:\.\d+)?%|\$\d|\b\d{2,}\b", raw))
    bullet_lines = [line for line in lines if re.match(r"^[•\-*–—]", line)]
    long_lines = [line for line in lines if len(line) > 220]

    weaknesses: List[str] = []
    if weak_hits:
        weaknesses.append(
            "Phrases like \"" + "\", \"".join(weak_hits[:3]) + "\" describe duties rather than outcomes."
        )
    if quantified < 3:
        weaknesses.append("Very few quantified results (numbers, %, scale, time saved) appear in the resume.")
    if len(lines) < 12:
        weaknesses.append("The resume is very short; detail on scope and impact is limited.")
    if long_lines:
        weaknesses.append(f"{len(long_lines)} paragraph(s) exceed 220 characters and are hard to scan.")

    missing_achievements: List[str] = []
    if quantified < 3:
        missing_achievements.append("Add 2–3 measurable outcomes (latency, revenue, adoption, cost, time saved).")
    for skill in report["missing_skills"][:3]:
        missing_achievements.append(f"No achievement demonstrates {skill}; add one if you have the experience.")

    formatting: List[str] = []
    if len(bullet_lines) < 5:
        formatting.append("Use short bullet points under each role instead of paragraphs.")
    if not profile.get("email") or not profile.get("phone"):
        formatting.append("Add a professional email address and phone number to the header.")
    if not profile.get("projects"):
        formatting.append("Add a Projects section with 1–2 role-relevant projects.")
    if len(raw) > 9000:
        formatting.append("The resume is long; keep it to 1–2 pages for this role.")

    keywords = [f"Add the exact keyword \"{skill}\" where it is genuinely demonstrated." for skill in report["missing_skills"][:5]]
    if not keywords:
        keywords = ["Mirror the job title and core requirement wording in your summary and skills section."]

    strengths: List[str] = []
    if report["matched_skills"]:
        strengths.append("Clear evidence of " + ", ".join(report["matched_skills"][:5]) + ".")
    if profile.get("total_years"):
        strengths.append(f"{profile['total_years']:g} years of experience detected.")
    if profile.get("certifications"):
        strengths.append("Certifications listed: " + ", ".join(profile["certifications"][:3]) + ".")

    return {
        "strengths": strengths or ["The resume contains enough information for a basic review."],
        "weaknesses": weaknesses or ["No major structural weaknesses were detected."],
        "missing_achievements": missing_achievements or ["Quantified achievements already appear; keep them role-relevant."],
        "unnecessary": (
            ["Remove or shorten long prose that does not support the target role."] if long_lines
            else ["No obviously unnecessary content was detected."]
        ),
        "formatting": formatting or ["Formatting is scannable; keep bullets parallel and start with action verbs."],
        "keywords": keywords,
    }


class SkillGapCoachAgent:
    """Skill Gap & Resume Coach: gaps plus concrete resume improvements."""

    name = "Skill Gap & Resume Coach"

    def run(self, profile: Dict[str, Any], job: Dict[str, Any], report: Dict[str, Any]) -> Dict[str, Any]:
        fallback = heuristic_gaps(profile, job, report)
        if not ai_available():
            return fallback

        prompt = f"""You are the Skill Gap & Resume Coach. Use ONLY the resume and job data below.

JOB: {json.dumps(job)[:3000]}
RESUME PROFILE: {json.dumps({k: v for k, v in profile.items() if k != 'raw_text'})[:4000]}
RESUME TEXT (excerpt): {profile.get('raw_text', '')[:6000]}
MATCHED: {', '.join(report['matched_skills']) or 'none'}
PARTIAL: {', '.join(report['partial_skills']) or 'none'}
MISSING: {', '.join(report['missing_skills']) or 'none'}

Return JSON:
{{
  "gaps": [{{
     "skill": "", "importance": "High|Medium|Low",
     "why": "why it matters for this role",
     "learn": ["specific topics to learn"],
     "practice_task": "one concrete project or exercise",
     "level": "missing|partial"
  }}],
  "resume_review": {{
     "strengths": [], "weaknesses": [], "missing_achievements": [],
     "unnecessary": [], "formatting": [], "keywords": []
  }}
}}

RULES:
- Cover every missing and partial requirement.
- Never invent experience or achievements; phrase suggestions as advice.
- Be specific: name the skill, the topic and the exercise."""

        try:
            data = llm_json(prompt, max_tokens=3500)
            gaps = []
            for item in _safe_dict_list(data.get("gaps")):
                skill = str(item.get("skill") or "").strip()
                if not skill:
                    continue
                gaps.append({
                    "skill": canonicalize(skill) or skill,
                    "importance": str(item.get("importance") or "Medium").title(),
                    "why": str(item.get("why") or "").strip(),
                    "learn": _safe_list(item.get("learn")) or SKILL_TOPICS.get(skill, DEFAULT_TOPICS),
                    "practice_task": str(item.get("practice_task") or "").strip(),
                    "level": str(item.get("level") or "missing").lower(),
                })
            review = data.get("resume_review") or {}
            merged_review = fallback["resume_review"]
            if isinstance(review, dict):
                for key in ("strengths", "weaknesses", "missing_achievements", "unnecessary", "formatting", "keywords"):
                    values = _safe_list(review.get(key))
                    if values:
                        merged_review[key] = values
            return {
                "gaps": gaps or fallback["gaps"],
                "resume_review": merged_review,
                "source": "gemini",
            }
        except Exception as exc:
            logger.warning("[%s] AI coaching failed (%s); using local analysis", self.name, exc)
            return fallback


# ─── 5. Interview Strategist Agent ──────────────────────────────────────────

CATEGORY_LABELS = {
    "technical": "Technical",
    "project": "Project-related",
    "experience": "Experience-based",
    "behavioral": "Behavioral",
    "problem_solving": "Problem-solving",
    "gap": "Skill-gap focused",
}


def heuristic_questions(
    profile: Dict[str, Any],
    job: Dict[str, Any],
    report: Dict[str, Any],
    gaps: List[Dict[str, Any]],
    for_candidate: bool = False,
) -> Dict[str, Any]:
    title = job.get("title") or "this role"
    questions: Dict[str, List[Dict[str, Any]]] = {key: [] for key in CATEGORY_LABELS}

    for skill in (job.get("required_skills") or [])[:4]:
        topics = SKILL_TOPICS.get(skill, DEFAULT_TOPICS)
        questions["technical"].append({
            "question": f"How have you used {skill} in production, and what trade-offs did you make?",
            "purpose": f"Verify practical depth in {skill}, not just familiarity.",
            "expected_concepts": topics,
            "evaluation_criteria": [
                f"Concrete example of {skill} in a real project",
                "Reasoning about trade-offs and failure modes",
                "Ownership of the outcome",
            ],
            "related_skill": skill,
        })

    for project in profile.get("projects", [])[:2]:
        name = project.get("name") or "your project"
        questions["project"].append({
            "question": f"Walk us through {name}: the problem, your specific contribution, and the result.",
            "purpose": "Check whether the project claim is genuine and whether the candidate can explain decisions.",
            "expected_concepts": ["Problem framing", "Technical choices", "Measured outcome", "Individual contribution"],
            "evaluation_criteria": ["Specificity", "Technical depth", "Honest about limitations"],
            "related_skill": ", ".join((job.get("required_skills") or [])[:2]) or "Project delivery",
        })

    for entry in profile.get("experience", [])[:2]:
        working_title = entry.get("title") or "your previous role"
        company = entry.get("company") or ""
        questions["experience"].append({
            "question": f"In your role as {working_title}{' at ' + company if company else ''}, what was the hardest technical problem you solved?",
            "purpose": "Assess depth, judgement and impact in a real employment context.",
            "expected_concepts": ["Context and constraints", "Diagnosis approach", "Resolution", "Impact"],
            "evaluation_criteria": ["Clarity", "Depth of diagnosis", "Ownership"],
            "related_skill": (job.get("required_skills") or ["Problem solving"])[0],
        })

    questions["behavioral"].append({
        "question": "Tell us about a time you disagreed with a teammate on a technical decision. How did you resolve it?",
        "purpose": "Assess collaboration and conflict handling.",
        "expected_concepts": ["Situation", "Respectful challenge", "Evidence-based decision", "Outcome"],
        "evaluation_criteria": ["Self-awareness", "Collaboration", "Outcome focus"],
        "related_skill": "Collaboration",
    })
    questions["behavioral"].append({
        "question": f"Describe a time you had to deliver under a tight deadline on {title} work. What did you cut and why?",
        "purpose": "Check prioritisation and communication under pressure.",
        "expected_concepts": ["Constraints", "Trade-offs", "Stakeholder communication", "Result"],
        "evaluation_criteria": ["Prioritisation", "Transparency", "Delivery"],
        "related_skill": "Time Management",
    })

    questions["problem_solving"].append({
        "question": f"A production system for {title} starts failing intermittently. How would you investigate?",
        "purpose": "Evaluate structured debugging and observability thinking.",
        "expected_concepts": ["Reproduce and scope", "Logs/metrics/traces", "Hypothesis testing", "Mitigation vs root cause"],
        "evaluation_criteria": ["Structured approach", "Observability awareness", "Communication"],
        "related_skill": "Problem Solving",
    })

    for gap in gaps[:3]:
        skill = gap.get("skill") or ""
        questions["gap"].append({
            "question": f"The role requires {skill}, which is not clearly evidenced in your resume. How would you become productive with it quickly?",
            "purpose": f"Understand the candidate's plan for closing the {skill} gap.",
            "expected_concepts": [f"{skill} fundamentals", "Learning plan", "Transferable experience", "First 30 days"],
            "evaluation_criteria": ["Honesty", "Concrete plan", "Evidence of learning ability"],
            "related_skill": skill,
        })

    critical_areas = []
    for skill in report.get("missing_skills", [])[:4]:
        critical_areas.append({
            "area": skill,
            "claim": f"Not evidenced in the resume",
            "why": f"{skill} is a stated requirement for {title}.",
            "verify_with": f"Ask for a specific {skill} example with scope and outcome, or a short practical exercise.",
        })
    for item in report.get("skill_evidence", []):
        if item["level"] == "partial":
            critical_areas.append({
                "area": item["skill"],
                "claim": item["evidence"],
                "why": "Only indirect evidence was found.",
                "verify_with": f"Probe for depth: architecture decisions, scale, and what the candidate personally built.",
            })

    result = {
        "categories": {
            key: {
                "label": CATEGORY_LABELS[key],
                "questions": value,
            }
            for key, value in questions.items()
        },
        "critical_areas": critical_areas,
        "source": "local",
    }
    if for_candidate:
        for entries in result["categories"].values():
            for question in entries["questions"]:
                question.update(_candidate_answer_scaffold(question))
    return result


def _candidate_answer_scaffold(question: Dict[str, Any]) -> Dict[str, Any]:
    concepts = question.get("expected_concepts") or []
    return {
        "what_tested": question.get("purpose", ""),
        "answer_structure": ["Situation / context", "Your specific action", "Result with numbers", "What you learned"],
        "sample_answer": (
            "Use a real example: set the context in one sentence, explain the decision you made and why, "
            "include a measurable result, then close with what you would do differently. Key ideas to cover: "
            + ", ".join(concepts[:4]) + "."
        ),
        "common_mistakes": [
            "Describing the team's work instead of your own contribution.",
            "No measurable outcome or outcome left vague.",
            "Too much background before getting to the decision.",
        ],
    }


class InterviewStrategistAgent:
    """Interview Strategist: tailored questions, critical areas, answer feedback."""

    name = "Interview Strategist Agent"

    def run(
        self,
        profile: Dict[str, Any],
        job: Dict[str, Any],
        report: Dict[str, Any],
        gaps: List[Dict[str, Any]],
        for_candidate: bool = False,
    ) -> Dict[str, Any]:
        fallback = heuristic_questions(profile, job, report, gaps, for_candidate)
        if not ai_available():
            return fallback

        schema_extra = (
            ', "what_tested": "", "answer_structure": [], "sample_answer": "", "common_mistakes": []'
            if for_candidate
            else ""
        )
        prompt = f"""You are the Interview Strategist Agent. Build an interview plan grounded in
the resume and job description below.

JOB: {json.dumps(job)[:2500]}
CANDIDATE PROFILE: {json.dumps({k: v for k, v in profile.items() if k != 'raw_text'})[:3500]}
MATCHED: {', '.join(report['matched_skills']) or 'none'}
MISSING: {', '.join(report['missing_skills']) or 'none'}
IDENTIFIED GAPS: {json.dumps(gaps)[:1500]}

Return JSON:
{{
  "categories": {{
    "technical": [{{"question": "", "purpose": "", "expected_concepts": [], "evaluation_criteria": [], "related_skill": ""{schema_extra}}}],
    "project": [], "experience": [], "behavioral": [], "problem_solving": [], "gap": []
  }},
  "critical_areas": [{{"area": "", "claim": "", "why": "", "verify_with": ""}}]
}}

RULES:
- 2-3 questions per category, each tied to this candidate's actual resume details.
- Never ask about age, gender, ethnicity, religion, disability, marital status or nationality.
- Critical areas must point to specific resume claims a recruiter should verify.
- {"Add sample answers, answer structure and common mistakes for a candidate preparing alone." if for_candidate else "These are for a recruiter, so focus on verification and evaluation."}"""

        try:
            data = llm_json(prompt, max_tokens=4500)
            categories = data.get("categories") or {}
            result_categories = {}
            for key, label in CATEGORY_LABELS.items():
                raw_questions = _safe_dict_list(categories.get(key))
                questions = []
                for item in raw_questions:
                    question_text = str(item.get("question") or "").strip()
                    if not question_text:
                        continue
                    question = {
                        "question": question_text,
                        "purpose": str(item.get("purpose") or "").strip(),
                        "expected_concepts": _safe_list(item.get("expected_concepts")),
                        "evaluation_criteria": _safe_list(item.get("evaluation_criteria")),
                        "related_skill": str(item.get("related_skill") or "").strip(),
                    }
                    if for_candidate:
                        question.update({
                            "what_tested": str(item.get("what_tested") or question["purpose"]).strip(),
                            "answer_structure": _safe_list(item.get("answer_structure")),
                            "sample_answer": str(item.get("sample_answer") or "").strip(),
                            "common_mistakes": _safe_list(item.get("common_mistakes")),
                        })
                    questions.append(question)
                result_categories[key] = {
                    "label": label,
                    "questions": questions or fallback["categories"][key]["questions"],
                }

            critical = []
            for item in _safe_dict_list(data.get("critical_areas")):
                if not str(item.get("area") or "").strip():
                    continue
                critical.append({
                    "area": str(item.get("area")).strip(),
                    "claim": str(item.get("claim") or "").strip(),
                    "why": str(item.get("why") or "").strip(),
                    "verify_with": str(item.get("verify_with") or "").strip(),
                })
            return {
                "categories": result_categories,
                "critical_areas": critical or fallback["critical_areas"],
                "source": "gemini",
            }
        except Exception as exc:
            logger.warning("[%s] AI question generation failed (%s); using local plan", self.name, exc)
            return fallback

    def review_answer(self, question: Dict[str, Any], answer: str) -> Dict[str, Any]:
        answer = (answer or "").strip()
        concepts = [str(c) for c in question.get("expected_concepts") or []]
        local = self._local_feedback(question, answer, concepts)
        if not ai_available():
            return local

        prompt = f"""You are the Interview Strategist Agent coaching a candidate.

QUESTION: {question.get('question')}
EXPECTED CONCEPTS: {json.dumps(concepts)}
CANDIDATE ANSWER:
\"\"\"{answer[:4000]}\"\"\"

Return JSON:
{{"score": 0, "strengths": [], "improvements": [], "model_answer": "",
  "concepts_covered": [], "next_step": ""}}

Score 0-100 against this rubric: structure 25, specificity/metrics 25,
concept coverage 30, clarity & honesty 20. Be concrete and encouraging."""
        try:
            data = llm_json(prompt, max_tokens=1200)
            return {
                "score": max(0, min(100, int(float(data.get("score") or 0)))),
                "strengths": _safe_list(data.get("strengths")) or local["strengths"],
                "improvements": _safe_list(data.get("improvements")) or local["improvements"],
                "model_answer": str(data.get("model_answer") or "").strip() or local["model_answer"],
                "concepts_covered": _safe_list(data.get("concepts_covered")),
                "next_step": str(data.get("next_step") or "").strip(),
                "source": "gemini",
            }
        except Exception as exc:
            logger.info("[%s] answer feedback fell back to local rubric: %s", self.name, exc)
            return local

    @staticmethod
    def _local_feedback(question: Dict[str, Any], answer: str, concepts: List[str]) -> Dict[str, Any]:
        words = re.findall(r"[A-Za-z']+", answer.lower())
        covered = [concept for concept in concepts if concept.lower() in answer.lower()]
        structure_hits = sum(
            1 for marker in ("situation", "context", "because", "result", "impact", "learned", "trade-off", "first", "then")
            if marker in answer.lower()
        )
        has_metric = bool(re.search(r"\d", answer))
        length = len(words)

        score = 0
        score += 25 if length >= 60 else 15 if length >= 30 else 5
        score += 25 if has_metric else 10
        score += int(30 * (len(covered) / len(concepts))) if concepts else 15
        score += min(20, structure_hits * 4)

        strengths, improvements = [], []
        if length >= 60:
            strengths.append("Your answer has enough detail to evaluate.")
        if has_metric:
            strengths.append("You included measurable detail.")
        if covered:
            strengths.append("You covered: " + ", ".join(covered) + ".")
        if length < 50:
            improvements.append("Expand the answer to roughly 120–180 words using Situation → Action → Result.")
        if not has_metric:
            improvements.append("Add one measurable outcome (scale, %, time saved, cost).")
        missing = [concept for concept in concepts if concept not in covered]
        if missing:
            improvements.append("Mention: " + ", ".join(missing[:4]) + ".")
        if structure_hits < 2:
            improvements.append("Make the structure explicit: what the situation was, what you decided, what happened.")
        if not improvements:
            improvements.append("Tighten the opening sentence and finish with the impact.")

        return {
            "score": max(0, min(100, score)),
            "strengths": strengths or ["You made a start; now add a concrete example."],
            "improvements": improvements,
            "model_answer": _candidate_answer_scaffold(question)["sample_answer"],
            "concepts_covered": covered,
            "next_step": "Re-answer the same question applying the improvements above.",
            "source": "local",
        }


# ─── 6. Recruitment & Scheduling Agent ──────────────────────────────────────

class RecruitmentSchedulingAgent:
    """Recruitment & Scheduling Agent: slot suggestions and invitation emails."""

    name = "Recruitment & Scheduling Agent"

    def suggestions(self, candidates: List[Dict[str, Any]], count: int = 5) -> List[Dict[str, str]]:
        """Suggest non-overlapping interview slots for the top candidates."""
        from tools.availability_tool import AvailabilityTool

        tool = AvailabilityTool()
        slots = tool.get_available_slots(num_slots=max(count * 2, 8))
        suggestions = []
        for candidate, slot in zip(candidates, slots):
            suggestions.append({
                "candidate_name": candidate.get("name", ""),
                "application_id": candidate.get("application_id", ""),
                "date": slot["date"],
                "start_time": slot["time"],
                "end_time": _add_minutes(slot["time"], slot.get("duration_minutes", 60)),
                "timezone": "UTC",
                "interviewer": slot["interviewer"]["name"],
                "interviewer_role": slot["interviewer"]["role"],
                "mode": slot["mode"],
                "duration_minutes": slot.get("duration_minutes", 60),
            })
        return suggestions

    def auto_schedule(
        self,
        candidates: List[Dict[str, Any]],
        job: Optional[Dict[str, Any]] = None,
        busy: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Reserve a non-overlapping interview slot for every shortlisted candidate.

        Deterministic on purpose: the agent picks the earliest open slots from the
        availability tool, skipping anything that is already booked for this
        recruiter (``busy`` rows come from the database). No email is sent here;
        the recruiter confirms and previews the invitation afterwards.
        """
        if not candidates:
            return []

        from tools.availability_tool import AvailabilityTool

        tool = AvailabilityTool()
        taken = list(busy or [])
        used = set()
        scheduled: List[Dict[str, Any]] = []
        for candidate in candidates:
            slots = tool.get_available_slots(max(len(candidates) * 3, 12))
            chosen = None
            for slot in slots:
                start = slot["time"]
                end = _add_minutes(start, slot.get("duration_minutes", 60))
                overlaps = any(
                    item.get("date") == slot["date"]
                    and start < str(item.get("end_time") or "")
                    and end > str(item.get("start_time") or "")
                    for item in taken
                )
                if overlaps:
                    continue
                chosen = {**slot, "start_time": start, "end_time": end}
                break
            if not chosen:
                logger.warning("[%s] No free slot left for %s", self.name, candidate.get("name"))
                continue

            tool.book_slot(chosen["id"], candidate.get("name") or "candidate")
            taken.append({
                "date": chosen["date"],
                "start_time": chosen["start_time"],
                "end_time": chosen["end_time"],
            })
            used.add(chosen["id"])
            scheduled.append({
                "application_id": candidate.get("application_id", ""),
                "candidate_name": candidate.get("name", ""),
                "candidate_email": candidate.get("email", ""),
                "job_title": (job or {}).get("title", ""),
                "date": chosen["date"],
                "start_time": chosen["start_time"],
                "end_time": chosen["end_time"],
                "timezone": "UTC",
                "interviewer": chosen["interviewer"]["name"],
                "interviewer_role": chosen["interviewer"]["role"],
                "mode": chosen["mode"],
                "status": "reserved",
                "instructions": (
                    "Reserved automatically by the Recruitment & Scheduling Agent "
                    "for a candidate the Screening Decision Agent shortlisted."
                ),
            })
        return scheduled

    def compose_invitation(
        self,
        candidate: Dict[str, Any],
        job: Dict[str, Any],
        slot: Dict[str, Any],
        recruiter_name: str = "Hiring team",
    ) -> Dict[str, str]:
        name = (candidate.get("name") or "Candidate").strip()
        role = job.get("title") or "the role"
        date_text = slot.get("date") or ""
        start = slot.get("start_time") or ""
        end = slot.get("end_time") or ""
        timezone = slot.get("timezone") or "UTC"
        mode = slot.get("mode") or "Video"
        interviewer = slot.get("interviewer") or ""
        location = slot.get("location") or ""
        instructions = slot.get("instructions") or ""

        subject = f"Interview invitation - {role} at Veyora AI"
        body = (
            f"Dear {name},\n\n"
            f"Thank you for your interest in the {role} role. We would like to invite you to an interview.\n\n"
            f"Date: {date_text}\n"
            f"Time: {start} – {end} ({timezone})\n"
            f"Format: {mode}\n"
            f"{'Interviewer: ' + interviewer + chr(10) if interviewer else ''}"
            f"{'Location / link: ' + location + chr(10) if location else ''}"
            f"{'Instructions: ' + instructions + chr(10) if instructions else ''}\n"
            "Please reply to this email to confirm whether this time works for you. "
            "If it does not, let us know two alternative times and we will reschedule.\n\n"
            "Kind regards,\n"
            f"{recruiter_name}\n"
            "Veyora AI — Intelligent Recruitment. Smarter Career Growth."
        )
        return {"subject": subject, "body": body}


def _add_minutes(time_text: str, minutes: int) -> str:
    try:
        hour, minute = [int(part) for part in time_text.split(":")]
    except (ValueError, AttributeError):
        return time_text
    total = hour * 60 + minute + int(minutes)
    return f"{(total // 60) % 24:02d}:{total % 60:02d}"


# ─── 7. Screening Decision Agent ────────────────────────────────────────────

SCREENING_THRESHOLD_DEFAULT = 60.0
SHORTLIST_RULE = (
    "Shortlist a candidate only when the compatibility score is {threshold:.0f} or above out of 100. "
    "A candidate scoring below {threshold:.0f} is rejected automatically."
)


class ScreeningDecisionAgent:
    """Screening Decision Agent: applies the shortlist rule and explains the outcome.

    The threshold lives in this agent's system prompt and in the deterministic
    fallback, so the same rule holds whether Gemini answers or the platform runs
    locally. The score itself always comes from services.scoring; the model only
    writes the explanation.
    """

    name = "Screening Decision Agent"

    def threshold(self) -> float:
        try:
            value = float(os.environ.get("SCREENING_THRESHOLD", str(SCREENING_THRESHOLD_DEFAULT)))
        except (TypeError, ValueError):
            return SCREENING_THRESHOLD_DEFAULT
        return max(0.0, min(100.0, value))

    def run(
        self,
        profile: Dict[str, Any],
        job: Dict[str, Any],
        report: Dict[str, Any],
        gaps: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        threshold = self.threshold()
        score = float(report.get("score") or 0)
        decision = "shortlisted" if score >= threshold else "rejected"
        fallback = self._local_decision(
            profile, job, report, gaps or [], threshold, decision, score
        )
        if not ai_available():
            return fallback

        missing = report.get("missing_skills") or []
        matched = report.get("matched_skills") or []
        prompt = f"""You are the Screening Decision Agent for a recruiting team.

NON-NEGOTIABLE SCREENING RULE
- The shortlist threshold is {threshold:.0f} out of 100.
- A candidate at {threshold:.0f} or above MUST be shortlisted.
- A candidate below {threshold:.0f} MUST be rejected.
- The score comes from the platform's deterministic rubric. Never recalculate it.

JOB: {json.dumps(job)[:2500]}
CANDIDATE: {json.dumps({k: v for k, v in profile.items() if k != 'raw_text'})[:2500]}
COMPATIBILITY SCORE: {score}/100 ({report.get('grade', '')})
MATCHED SKILLS: {', '.join(matched) or 'none'}
PARTIAL SKILLS: {', '.join(report.get('partial_skills') or []) or 'none'}
MISSING SKILLS: {', '.join(missing) or 'none'}
REQUIRED SKILLS: {', '.join(job.get('required_skills') or []) or 'none'}
EXPERIENCE: {profile.get('total_years', 0)} years (minimum required: {job.get('min_years', 0)})
TOP GAPS: {json.dumps(gaps[:4])[:1500]}

Return JSON:
{{
  "summary": "2 sentences explaining this screening outcome against the rule",
  "reasons": ["specific reason tied to a score, skill or experience fact"],
  "blockers": ["only for rejected candidates"],
  "next_step": "what the recruiter should do next"
}}

RULES:
- Write the explanation only; the platform applies the decision itself.
- 2 to 4 reasons, each tied to a number or a named skill from the data above.
- Never invent experience, skills, achievements or employers.
- Never suggest changing the threshold or overriding the rule."""

        try:
            data = llm_json(prompt, max_tokens=900)
            summary = str(data.get("summary") or "").strip()
            reasons = _safe_list(data.get("reasons"))
            blockers = _safe_list(data.get("blockers"))
            next_step = str(data.get("next_step") or "").strip()
            return {
                **fallback,
                "summary": summary or fallback["summary"],
                "reasons": (reasons or fallback["reasons"])[:5],
                "blockers": (blockers or fallback["blockers"])[:5] if decision == "rejected" else [],
                "next_step": next_step or fallback["next_step"],
                "source": "gemini",
            }
        except Exception as exc:
            logger.warning("[%s] AI explanation failed (%s); using local decision", self.name, exc)
            return fallback

    @staticmethod
    def _local_decision(
        profile: Dict[str, Any],
        job: Dict[str, Any],
        report: Dict[str, Any],
        gaps: List[Dict[str, Any]],
        threshold: float,
        decision: str,
        score: float,
    ) -> Dict[str, Any]:
        matched = report.get("matched_skills") or []
        missing = report.get("missing_skills") or []
        required = job.get("required_skills") or []
        years = float(profile.get("total_years") or 0)
        min_years = float(job.get("min_years") or 0)
        margin = round(score - threshold, 1)

        reasons: List[str] = []
        if decision == "shortlisted":
            reasons.append(
                f"Scored {score:g}/100 — {margin:g} point(s) above the {threshold:g}/100 shortlist threshold."
            )
            if matched:
                reasons.append(
                    f"Matched {len(matched)} of {len(required) or len(matched)} required skills: "
                    f"{', '.join(matched[:6])}."
                )
            if min_years and years >= min_years:
                reasons.append(
                    f"{years:g} years of experience meets the {min_years:g}-year requirement."
                )
        else:
            reasons.append(
                f"Scored {score:g}/100 — {abs(margin):g} point(s) below the {threshold:g}/100 shortlist threshold."
            )
            if missing:
                reasons.append(f"Missing required skills: {', '.join(missing[:6])}.")
            if min_years and years < min_years:
                reasons.append(
                    f"{years:g} years of experience is below the {min_years:g}-year requirement."
                )
        if gaps:
            top = [str(gap.get("skill") or "") for gap in gaps[:2] if gap.get("skill")]
            if top:
                reasons.append(f"Highest-priority development areas: {', '.join(top)}.")

        blockers: List[str] = []
        if decision == "rejected":
            blockers = [f"Missing {skill}" for skill in missing[:5]]
            if min_years and years < min_years:
                blockers.append(f"{min_years - years:g} more year(s) of experience needed")

        summary = (
            f"Shortlisted: {score:g}/100 clears the {threshold:g}/100 bar for "
            f"{job.get('title') or 'this role'}."
            if decision == "shortlisted"
            else f"Rejected automatically: {score:g}/100 is below the {threshold:g}/100 bar for "
                 f"{job.get('title') or 'this role'}."
        )
        next_step = (
            "Confirm the reserved interview slot and send the invitation."
            if decision == "shortlisted"
            else "Keep the profile on file and close the application for this role."
        )
        return {
            "decision": decision,
            "score": score,
            "threshold": threshold,
            "margin": margin,
            "rule": SHORTLIST_RULE.format(threshold=threshold),
            "summary": summary,
            "reasons": reasons[:5],
            "blockers": blockers,
            "next_step": next_step,
            "source": "local",
        }


# ─── 8. Career Intelligence Agent ───────────────────────────────────────────

class CareerIntelligenceAgent:
    """Career Intelligence Agent: personalised learning roadmap."""

    name = "Career Intelligence Agent"

    def run(
        self,
        profile: Dict[str, Any],
        job: Dict[str, Any],
        gaps: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        fallback = self._local_roadmap(profile, job, gaps)
        if not ai_available():
            return fallback

        prompt = f"""You are the Career Intelligence Agent. Build a practical learning roadmap.

TARGET ROLE: {json.dumps(job)[:2000]}
CURRENT SKILLS: {', '.join(profile.get('technical_skills', []))}
EXPERIENCE: {profile.get('total_years', 0)} years
SKILL GAPS: {json.dumps(gaps)[:2500]}

Return JSON:
{{
  "summary": "2 sentences on the fastest path to this role",
  "total_weeks": 6,
  "weeks": [{{
     "week": 1, "focus": "", "goals": [], "exercises": [],
     "project": "", "milestone": ""
  }}],
  "milestones": ["capstone outcome"]
}}

RULES:
- Order learning by job importance: mandatory gaps first.
- 4 to 8 weeks; each week must be achievable in 6–8 focused hours.
- Every week needs a concrete exercise and a measurable milestone.
- Never suggest inventing experience for the resume."""

        try:
            data = llm_json(prompt, max_tokens=3000)
            weeks = []
            for index, item in enumerate(_safe_dict_list(data.get("weeks")), start=1):
                weeks.append({
                    "week": int(item.get("week") or index),
                    "focus": str(item.get("focus") or "").strip(),
                    "goals": _safe_list(item.get("goals")),
                    "exercises": _safe_list(item.get("exercises")),
                    "project": str(item.get("project") or "").strip(),
                    "milestone": str(item.get("milestone") or "").strip(),
                })
            if not weeks:
                return fallback
            return {
                "target_role": job.get("title") or fallback["target_role"],
                "summary": str(data.get("summary") or fallback["summary"]).strip(),
                "total_weeks": int(data.get("total_weeks") or len(weeks)),
                "weeks": weeks,
                "milestones": _safe_list(data.get("milestones")) or fallback["milestones"],
                "source": "gemini",
            }
        except Exception as exc:
            logger.warning("[%s] AI roadmap failed (%s); using local plan", self.name, exc)
            return fallback

    @staticmethod
    def _local_roadmap(profile: Dict[str, Any], job: Dict[str, Any], gaps: List[Dict[str, Any]]) -> Dict[str, Any]:
        title = job.get("title") or "your target role"
        ordered = sorted(
            gaps,
            key=lambda gap: (0 if gap.get("importance") == "High" else 1 if gap.get("importance") == "Medium" else 2),
        )
        weeks = []
        for index, gap in enumerate(ordered[:6], start=1):
            skill = gap.get("skill") or "the target skill"
            topics = gap.get("learn") or SKILL_TOPICS.get(skill, DEFAULT_TOPICS)
            weeks.append({
                "week": index,
                "focus": f"Build working competence in {skill}",
                "goals": [f"Understand {topic}" for topic in topics[:3]],
                "exercises": [
                    f"Complete one guided exercise covering: {topics[0]}",
                    f"Write a short note comparing {skill} with something you already use.",
                ],
                "project": gap.get("practice_task") or f"Ship a small {skill} project and document the result.",
                "milestone": f"Can explain and demonstrate {skill} with a concrete example.",
            })
        if not weeks:
            weeks.append({
                "week": 1,
                "focus": f"Deepen evidence for {title}",
                "goals": ["Turn existing work into measurable achievements"],
                "exercises": ["Rewrite three resume bullets with scope, action and measurable result"],
                "project": f"Document one substantial {title} contribution end to end",
                "milestone": "A resume section that proves the role's core requirements",
            })

        return {
            "target_role": title,
            "summary": (
                f"Close {len(ordered)} priority gap(s) in about {max(4, min(len(weeks), 8))} weeks, "
                f"then apply to {title} roles with concrete project evidence."
            ),
            "total_weeks": max(4, min(len(weeks), 8)),
            "weeks": weeks,
            "milestones": [
                f"A portfolio project that demonstrates {gap.get('skill')}"
                for gap in ordered[:4]
            ] or [f"One strong, quantified {title} case study"],
            "source": "local",
        }
