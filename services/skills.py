"""
Skill normalisation and matching.
================================
Used by the deterministic compatibility engine and by the local (no-AI)
fallbacks so that skill detection never depends on an external service.
"""

import re
from typing import Dict, Iterable, List, Optional, Tuple

# canonical name -> aliases found in resumes / job descriptions
SKILL_ALIASES: Dict[str, List[str]] = {
    # Languages
    "Python": ["python", "python3"],
    "JavaScript": ["javascript", "js", "es6"],
    "TypeScript": ["typescript", "ts"],
    "Java": ["java"],
    "C#": ["c#", "csharp"],
    "C++": ["c++", "cpp"],
    "Go": ["golang", "go"],
    "Rust": ["rust"],
    "Ruby": ["ruby", "ruby on rails"],
    "PHP": ["php"],
    "Scala": ["scala"],
    "Kotlin": ["kotlin"],
    "Swift": ["swift"],
    "SQL": ["sql", "sql queries"],
    "R": ["r"],
    "MATLAB": ["matlab"],
    "Bash": ["bash", "shell scripting"],
    # Frontend
    "React": ["react", "react.js", "reactjs"],
    "Next.js": ["next.js", "nextjs"],
    "Vue": ["vue", "vue.js", "vuejs"],
    "Angular": ["angular"],
    "Redux": ["redux"],
    "Tailwind CSS": ["tailwind", "tailwindcss", "tailwind css"],
    "HTML": ["html", "html5"],
    "CSS": ["css", "css3"],
    "Sass": ["sass", "scss"],
    "Webpack": ["webpack"],
    "Vite": ["vite"],
    # Backend / frameworks
    "Node.js": ["node.js", "nodejs", "node"],
    "Express": ["express", "express.js"],
    "Django": ["django"],
    "Flask": ["flask"],
    "FastAPI": ["fastapi"],
    "Spring Boot": ["spring boot", "spring"],
    ".NET": [".net", "dotnet", "asp.net"],
    "GraphQL": ["graphql"],
    "REST API": ["rest api", "restful", "rest apis", "rest"],
    "gRPC": ["grpc"],
    "Microservices": ["microservices", "microservice"],
    # Data
    "PostgreSQL": ["postgresql", "postgres"],
    "MySQL": ["mysql"],
    "SQLite": ["sqlite"],
    "MongoDB": ["mongodb", "mongo"],
    "Redis": ["redis"],
    "Elasticsearch": ["elasticsearch", "elastic search"],
    "Kafka": ["kafka", "apache kafka"],
    "RabbitMQ": ["rabbitmq"],
    "Spark": ["spark", "apache spark", "pyspark"],
    "Airflow": ["airflow", "apache airflow"],
    "Hadoop": ["hadoop"],
    "Snowflake": ["snowflake"],
    "BigQuery": ["bigquery"],
    "Pandas": ["pandas"],
    "NumPy": ["numpy"],
    "ETL": ["etl", "extract transform load"],
    "Data Modeling": ["data modeling", "data modelling"],
    "Data Warehousing": ["data warehouse", "data warehousing"],
    # Cloud / DevOps
    "AWS": ["aws", "amazon web services", "ec2", "s3", "lambda"],
    "Azure": ["azure", "microsoft azure"],
    "GCP": ["gcp", "google cloud", "google cloud platform"],
    "Docker": ["docker", "containers", "containerization"],
    "Kubernetes": ["kubernetes", "k8s"],
    "Terraform": ["terraform"],
    "Ansible": ["ansible"],
    "Jenkins": ["jenkins"],
    "GitHub Actions": ["github actions"],
    "CircleCI": ["circleci"],
    "CI/CD": ["ci/cd", "cicd", "continuous integration", "continuous delivery"],
    "Linux": ["linux", "unix"],
    "Nginx": ["nginx"],
    "Serverless": ["serverless"],
    "CloudFormation": ["cloudformation"],
    "Prometheus": ["prometheus"],
    "Grafana": ["grafana"],
    "Datadog": ["datadog"],
    "Observability": ["observability", "monitoring", "logging"],
    "Git": ["git", "github", "gitlab", "version control"],
    "Agile": ["agile", "scrum", "kanban", "sprint"],
    "Jira": ["jira"],
    # ML / AI
    "Machine Learning": ["machine learning", "ml models", "ml"],
    "Deep Learning": ["deep learning", "neural network", "neural networks"],
    "PyTorch": ["pytorch"],
    "TensorFlow": ["tensorflow"],
    "scikit-learn": ["scikit-learn", "sklearn"],
    "NLP": ["nlp", "natural language processing"],
    "Computer Vision": ["computer vision"],
    "LLM": ["llm", "large language model", "prompt engineering"],
    "Generative AI": ["generative ai", "genai"],
    "Data Visualization": ["data visualization", "data visualisation", "tableau", "power bi"],
    "Statistics": ["statistics", "statistical analysis", "a/b testing"],
    # Design / product
    "Figma": ["figma"],
    "UI Design": ["ui design", "user interface design"],
    "UX Research": ["ux research", "user research", "usability"],
    "Product Management": ["product management", "roadmap"],
    "Prototyping": ["prototyping", "wireframe", "wireframing"],
    # QA / security
    "Testing": ["unit testing", "pytest", "jest", "test automation", "qa"],
    "Selenium": ["selenium", "playwright", "cypress"],
    "Security": ["security", "owasp", "penetration testing", "authentication"],
    "Accessibility": ["accessibility", "a11y", "wcag"],
}

SOFT_SKILL_ALIASES: Dict[str, List[str]] = {
    "Communication": ["communication", "communicate", "presentation", "presenting"],
    "Leadership": ["leadership", "led a team", "team lead", "mentored", "mentoring", "managed a team"],
    "Collaboration": ["collaboration", "collaborated", "cross-functional", "teamwork", "partnered"],
    "Problem Solving": ["problem solving", "problem-solving", "troubleshooting", "root cause"],
    "Ownership": ["ownership", "owned", "self-starter", "autonomy"],
    "Mentoring": ["mentored", "mentoring", "coached", "coaching"],
    "Stakeholder Management": ["stakeholder", "stakeholders", "client-facing"],
    "Adaptability": ["adaptable", "adaptability", "fast-paced"],
    "Attention to Detail": ["attention to detail", "detail-oriented"],
    "Time Management": ["time management", "prioritize", "prioritisation", "prioritization"],
}


def _compile(alias: str) -> re.Pattern:
    escaped = re.escape(alias)
    if len(alias) <= 2:
        # Avoid matching "R" or "Go" inside other words / normal prose.
        return re.compile(r"(?<![A-Za-z])" + escaped + r"(?![A-Za-z])")
    return re.compile(r"(?<![A-Za-z0-9+#])" + escaped + r"(?![A-Za-z0-9+#])", re.IGNORECASE)


_CANONICAL_INDEX: Dict[str, str] = {}
_PATTERNS: List[Tuple[str, re.Pattern]] = []
_SOFT_PATTERNS: List[Tuple[str, re.Pattern]] = []

for canonical, aliases in SKILL_ALIASES.items():
    for alias in aliases:
        _CANONICAL_INDEX.setdefault(alias.lower(), canonical)
        _PATTERNS.append((canonical, _compile(alias)))

for canonical, aliases in SOFT_SKILL_ALIASES.items():
    for alias in aliases:
        _SOFT_PATTERNS.append((canonical, _compile(alias)))


def canonicalize(value: str) -> Optional[str]:
    """Map a free-text skill to its canonical name, or return None."""
    if not value:
        return None
    cleaned = str(value).strip().lower()
    if cleaned in _CANONICAL_INDEX:
        return _CANONICAL_INDEX[cleaned]
    for canonical, aliases in SKILL_ALIASES.items():
        if cleaned == canonical.lower():
            return canonical
    # Try a substring match for phrases like "Amazon Web Services (AWS)".
    for canonical, pattern in _PATTERNS:
        if pattern.search(str(value)):
            return canonical
    return None


def extract_skills(text: str, limit: Optional[int] = None) -> List[str]:
    """Return canonical technical skills found in `text`, in reading order."""
    if not text:
        return []
    lowered = text.lower()
    found: List[Tuple[int, str]] = []
    seen = set()
    for canonical, pattern in _PATTERNS:
        if canonical in seen:
            continue
        match = pattern.search(text)
        if match:
            found.append((match.start(), canonical))
            seen.add(canonical)
    found.sort(key=lambda item: item[0])
    skills = [canonical for _, canonical in found]
    return skills[:limit] if limit else skills


def extract_soft_skills(text: str) -> List[str]:
    if not text:
        return []
    found: List[str] = []
    for canonical, pattern in _SOFT_PATTERNS:
        if canonical in found:
            continue
        if pattern.search(text):
            found.append(canonical)
    return found


def _tokens(value: str) -> List[str]:
    return [t for t in re.split(r"[^A-Za-z0-9+#]+", value.lower()) if len(t) > 1]


def skill_in_text(skill: str, text: str) -> bool:
    if not skill or not text:
        return False
    canonical = canonicalize(skill) or skill
    aliases = SKILL_ALIASES.get(canonical, [])
    for alias in aliases or [canonical]:
        if _compile(alias).search(text):
            return True
    return False


def evidence_sentence(skill: str, text: str) -> str:
    """Return the resume sentence that demonstrates `skill`, if any."""
    if not text:
        return ""
    canonical = canonicalize(skill) or skill
    aliases = SKILL_ALIASES.get(canonical, [])
    pattern = None
    for alias in aliases or [canonical]:
        candidate = _compile(alias)
        if candidate.search(text):
            pattern = candidate
            break
    if not pattern:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+|\n+", text)
    for sentence in sentences:
        if pattern.search(sentence):
            cleaned = " ".join(sentence.split())
            return cleaned[:240]
    return ""


def match_skill(required: str, profile_skills: Iterable[str], resume_text: str = "") -> Tuple[str, str]:
    """
    Compare one required skill against a candidate's profile.

    Returns (level, evidence) where level is one of:
      "matched"   – skill explicitly present
      "partial"   – related/adjacent evidence only
      "missing"   – no evidence found
    """
    canonical = canonicalize(required) or str(required).strip()
    profile_list = [str(s) for s in profile_skills if s]
    canonical_set = {(canonicalize(s) or s).lower() for s in profile_list}

    if canonical.lower() in canonical_set:
        return "matched", f"Listed in the candidate's skills ({canonical})"

    req_tokens = set(_tokens(canonical))
    for candidate in profile_list:
        candidate_tokens = set(_tokens(canonicalize(candidate) or candidate))
        if req_tokens and req_tokens.issubset(candidate_tokens):
            return "matched", f"Listed in the candidate's skills ({candidate})"

    sentence = evidence_sentence(canonical, resume_text)
    if sentence:
        return "matched", sentence

    for candidate in profile_list:
        candidate_tokens = set(_tokens(canonicalize(candidate) or candidate))
        if req_tokens and candidate_tokens and req_tokens & candidate_tokens:
            return "partial", f"Adjacent evidence: {candidate}"

    if resume_text:
        lowered_resume = resume_text.lower()
        if canonical.lower() in lowered_resume:
            return "partial", f"Mentioned in the resume text but not in the skills list"
        hits = [token for token in req_tokens if re.search(rf"\b{re.escape(token)}", lowered_resume)]
        if req_tokens and hits and len(hits) / len(req_tokens) >= 0.5:
            return "partial", f"Related keywords found in the resume: {', '.join(hits)}"

    return "missing", "No evidence of this skill in the resume"


def dedupe_skills(values: Iterable[str]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        if not value:
            continue
        cleaned = str(value).strip()
        if not cleaned:
            continue
        key = (canonicalize(cleaned) or cleaned).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(canonicalize(cleaned) or cleaned)
    return result
