"""
Explainable, deterministic compatibility scoring.
================================================
The AI never invents the number. Every point is produced by this weighted
rubric from structured resume/job data, so the same inputs always produce the
same score and every result can be traced back to a requirement.

Weights (out of 100):
    Mandatory skills      45
    Preferred skills      20
    Experience            15
    Project relevance     10
    Education & certs      5
    Role competencies      5
"""

import re
from typing import Any, Dict, Iterable, List, Optional

from services.skills import (
    canonicalize,
    dedupe_skills,
    evidence_sentence,
    extract_soft_skills,
    match_skill,
    skill_in_text,
)

BASE_WEIGHTS = {
    "mandatory": 45.0,
    "preferred": 20.0,
    "experience": 15.0,
    "projects": 10.0,
    "education": 5.0,
    "competencies": 5.0,
}

LABELS = {
    "mandatory": "Mandatory skills",
    "preferred": "Preferred skills",
    "experience": "Experience",
    "projects": "Project relevance",
    "education": "Education & certifications",
    "competencies": "Role competencies",
}

LEVEL_VALUE = {"matched": 1.0, "partial": 0.5, "missing": 0.0}


def _as_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        return [item.strip() for item in re.split(r"[,;\n]", value) if item.strip()]
    return []


def _as_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, number)


def _text_of(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return " ".join(str(value) for value in item.values() if value)
    return str(item or "")


def normalize_profile(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Coerce agent output (or the legacy CandidateProfile shape) into one shape."""
    raw = raw or {}
    technical = dedupe_skills(
        _as_list(raw.get("technical_skills")) + _as_list(raw.get("skills"))
    )
    soft = dedupe_skills(
        _as_list(raw.get("soft_skills"))
        + extract_soft_skills(raw.get("summary", "") or "")
    )
    experience = []
    for entry in _as_list(raw.get("experience")) + _as_list(raw.get("work_experience")):
        if isinstance(entry, dict):
            experience.append(entry)
        elif isinstance(entry, str):
            experience.append({"title": entry, "company": "", "duration": "", "description": entry})

    projects = []
    for entry in _as_list(raw.get("projects")):
        if isinstance(entry, dict):
            projects.append(entry)
        elif isinstance(entry, str):
            projects.append({"name": entry, "description": entry})

    internships = []
    for entry in _as_list(raw.get("internships")):
        internships.append(entry if isinstance(entry, dict) else {"title": str(entry)})

    education = []
    for entry in _as_list(raw.get("education")):
        if isinstance(entry, dict):
            education.append(entry)
        elif isinstance(entry, str):
            education.append({"degree": entry, "institution": "", "year": ""})

    years = _as_float(raw.get("total_years"))
    if not years:
        years = _as_float(raw.get("experience_years"))

    return {
        "name": str(raw.get("name") or "").strip(),
        "email": str(raw.get("email") or "").strip(),
        "phone": str(raw.get("phone") or "").strip(),
        "location": str(raw.get("location") or "").strip(),
        "links": _as_list(raw.get("links")),
        "summary": str(raw.get("summary") or "").strip(),
        "technical_skills": technical,
        "soft_skills": soft,
        "experience": experience,
        "internships": internships,
        "projects": projects,
        "education": education,
        "certifications": [str(item) for item in _as_list(raw.get("certifications")) if str(item).strip()],
        "achievements": [str(item) for item in _as_list(raw.get("achievements")) if str(item).strip()],
        "total_years": years,
        "raw_text": str(raw.get("raw_text") or ""),
    }


def normalize_job(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    raw = raw or {}
    description = str(raw.get("description") or "")
    required = dedupe_skills(_as_list(raw.get("required_skills")))
    preferred = [s for s in dedupe_skills(_as_list(raw.get("preferred_skills"))) if s not in required]

    if not required:
        detected = _detect_skills(description)
        required = detected[:8]
        preferred = [skill for skill in detected[8:14] if skill not in required]

    competencies = dedupe_skills(_as_list(raw.get("competencies")))
    if not competencies:
        competencies = extract_soft_skills(description)[:4]

    return {
        "id": str(raw.get("id") or ""),
        "title": str(raw.get("title") or "").strip() or _guess_title(description),
        "description": description,
        "required_skills": required,
        "preferred_skills": preferred,
        "min_years": _as_float(raw.get("min_years")),
        "seniority": str(raw.get("seniority") or "").strip(),
        "education_requirement": str(raw.get("education_requirement") or "").strip(),
        "competencies": competencies,
        "responsibilities": [str(item) for item in _as_list(raw.get("responsibilities"))],
    }


def _detect_skills(text: str) -> List[str]:
    from services.skills import extract_skills

    return extract_skills(text)


def _guess_title(description: str) -> str:
    match = re.search(r"job\s*title\s*[:\-]\s*(.+)", description or "", re.IGNORECASE)
    if match:
        return match.group(1).split("\n")[0].strip()[:120]
    return ""


def _component(key: str, weight: float, ratio: float, detail: str, reasons: List[str]) -> Dict[str, Any]:
    ratio = max(0.0, min(1.0, ratio))
    return {
        "key": key,
        "label": LABELS[key],
        "weight": round(weight, 1),
        "ratio": round(ratio, 3),
        "earned": round(weight * ratio, 1),
        "detail": detail,
        "reasons": reasons,
    }


def _grade(score: int) -> str:
    if score >= 85:
        return "Excellent match"
    if score >= 70:
        return "Strong match"
    if score >= 55:
        return "Moderate match"
    if score >= 40:
        return "Weak match"
    return "Limited match"


def score_candidate(
    profile: Optional[Dict[str, Any]],
    job: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Return a fully explainable compatibility report for one candidate."""
    candidate = normalize_profile(profile)
    position = normalize_job(job)

    required = position["required_skills"]
    preferred = [s for s in position["preferred_skills"] if s not in required]
    profile_skills = candidate["technical_skills"]
    resume_text = candidate["raw_text"]

    weights = dict(BASE_WEIGHTS)
    notes: List[str] = []

    # If a section has no requirements, move its weight to the closest signal
    # instead of awarding or deducting arbitrary points.
    if not required:
        weights["experience"] += weights["mandatory"]
        weights["mandatory"] = 0.0
        notes.append(
            "No mandatory skills could be identified in the job description, so that weight "
            "was moved to experience."
        )
    if not preferred:
        weights["projects"] += weights["preferred"]
        weights["preferred"] = 0.0
    if not candidate["soft_skills"] or not position["competencies"]:
        weights["experience"] += weights["competencies"]
        weights["competencies"] = 0.0

    breakdown: List[Dict[str, Any]] = []
    evidence: List[Dict[str, Any]] = []
    matched: List[str] = []
    partial: List[str] = []
    missing: List[str] = []

    # ── Mandatory skills ────────────────────────────────────────────────
    if required:
        total = 0.0
        reasons = []
        for skill in required:
            level, proof = match_skill(skill, profile_skills, resume_text)
            total += LEVEL_VALUE[level]
            evidence.append({"skill": skill, "level": level, "evidence": proof, "importance": "required"})
            if level == "matched":
                matched.append(skill)
            elif level == "partial":
                partial.append(skill)
            else:
                missing.append(skill)
            reasons.append(f"{skill}: {level} — {proof}")
        ratio = total / len(required)
        breakdown.append(
            _component(
                "mandatory",
                weights["mandatory"],
                ratio,
                f"{sum(1 for s in required if s in matched)} of {len(required)} mandatory skills matched",
                reasons,
            )
        )
    else:
        reasons = ["No mandatory skills were listed."]
        breakdown.append(_component("mandatory", 0.0, 0.0, "No mandatory skills listed", reasons))

    # ── Preferred skills ────────────────────────────────────────────────
    if preferred:
        total = 0.0
        reasons = []
        for skill in preferred:
            level, proof = match_skill(skill, profile_skills, resume_text)
            total += LEVEL_VALUE[level]
            evidence.append({"skill": skill, "level": level, "evidence": proof, "importance": "preferred"})
            if level == "matched":
                matched.append(skill)
            elif level == "partial":
                partial.append(skill)
            elif skill not in missing:
                missing.append(skill)
            reasons.append(f"{skill}: {level} — {proof}")
        ratio = total / len(preferred)
        breakdown.append(
            _component(
                "preferred",
                weights["preferred"],
                ratio,
                f"{len([s for s in preferred if s in matched])} of {len(preferred)} preferred skills matched",
                reasons,
            )
        )
    else:
        breakdown.append(
            _component("preferred", 0.0, 0.0, "No preferred skills listed; weight moved to project relevance", [])
        )

    # ── Experience ──────────────────────────────────────────────────────
    years = candidate["total_years"]
    min_years = position["min_years"]
    if min_years > 0:
        ratio = min(1.0, years / min_years) if years else 0.0
        detail = f"{years:g} years detected against a {min_years:g}-year requirement"
        reason = (
            f"Meet requirement ({years:g} ≥ {min_years:g} years)."
            if years >= min_years
            else f"Short by {max(0.0, min_years - years):g} years."
        )
    else:
        target = 5.0
        ratio = min(1.0, years / target) if years else 0.0
        detail = f"{years:g} years of relevant experience detected (benchmark {target:g} years)"
        reason = "The job description did not state a minimum, so a 5-year benchmark was used."
    roles = ", ".join(
        str(entry.get("title") or entry.get("company") or "").strip()
        for entry in candidate["experience"][:3]
        if str(entry.get("title") or entry.get("company") or "").strip()
    )
    if roles:
        reason += f" Roles found: {roles}."
    breakdown.append(_component("experience", weights["experience"], ratio, detail, [reason]))

    # ── Project relevance ───────────────────────────────────────────────
    project_blobs = []
    for project in candidate["projects"]:
        project_blobs.append(" ".join(str(value) for value in project.values() if value))
    for internship in candidate["internships"]:
        project_blobs.append(_text_of(internship))
    project_text = " ".join(project_blobs)
    target_skills = required + preferred
    if project_text:
        hits = [skill for skill in target_skills if skill_in_text(skill, project_text)]
        denominator = max(1, min(4, len(target_skills) or 4))
        ratio = min(1.0, len(hits) / denominator)
        detail = f"{len(hits)} role-relevant skill(s) evidenced in projects"
        reason = (
            "Projects reference: " + ", ".join(hits) + "."
            if hits
            else "Projects are listed but do not demonstrate the role's core skills."
        )
    else:
        ratio = 0.0
        detail = "No projects or internships were found in the resume"
        reason = "Adding 1–2 relevant projects would strengthen this area."
    breakdown.append(_component("projects", weights["projects"], ratio, detail, [reason]))

    # ── Education & certifications ──────────────────────────────────────
    has_degree = bool(candidate["education"])
    cert_text = " ".join(candidate["certifications"])
    relevant_certs = [skill for skill in target_skills if cert_text and skill_in_text(skill, cert_text)]
    ratio = 0.0
    reasons = []
    if has_degree:
        ratio += 0.6
        reasons.append("A degree/education entry is present.")
    else:
        reasons.append("No education entry was found.")
    if relevant_certs:
        ratio += 0.4
        reasons.append("Relevant certification(s): " + ", ".join(relevant_certs) + ".")
    elif candidate["certifications"]:
        ratio += 0.2
        reasons.append("Certifications are listed but are not directly role-specific.")
    else:
        reasons.append("No certifications were found.")
    if candidate["achievements"]:
        reasons.append("Achievements provide additional supporting evidence.")
    breakdown.append(
        _component(
            "education",
            weights["education"],
            min(1.0, ratio),
            f"{len(candidate['education'])} education entr(ies), {len(candidate['certifications'])} certification(s)",
            reasons,
        )
    )

    # ── Role competencies ───────────────────────────────────────────────
    if weights["competencies"] > 0:
        wanted = position["competencies"]
        have = candidate["soft_skills"]
        hit = [item for item in wanted if item in have]
        ratio = len(hit) / len(wanted) if wanted else 0.0
        reasons = [
            "Demonstrated: " + ", ".join(hit) + "." if hit else "No matching soft skills are evidenced in the resume."
        ]
        if len(hit) < len(wanted):
            reasons.append("Not evidenced: " + ", ".join([i for i in wanted if i not in hit]) + ".")
        breakdown.append(
            _component(
                "competencies",
                weights["competencies"],
                ratio,
                f"{len(hit)} of {len(wanted)} role competencies evidenced",
                reasons,
            )
        )
    else:
        breakdown.append(
            _component("competencies", 0.0, 0.0, "Not scored for this role", ["Soft skills were not evidenced."])
        )

    total = round(sum(component["earned"] for component in breakdown))
    total = max(0, min(100, total))

    matched = dedupe_skills(matched)
    partial = dedupe_skills(partial)
    missing = [skill for skill in dedupe_skills(missing) if skill not in matched]

    explanation = []
    for component in breakdown:
        if component["weight"] <= 0:
            continue
        explanation.append(
            f"{component['label']}: {component['earned']:g}/{component['weight']:g} points "
            f"({component['detail']})."
        )

    return {
        "score": total,
        "grade": _grade(total),
        "breakdown": breakdown,
        "matched_skills": matched,
        "partial_skills": partial,
        "missing_skills": missing,
        "skill_evidence": evidence,
        "reasons": explanation + notes,
        "methodology": (
            "Weighted rubric out of 100: mandatory skills 45, preferred skills 20, experience 15, "
            "project relevance 10, education & certifications 5, role competencies 5. Partial skills "
            "earn half credit. The score is calculated from resume evidence only, never by the language model."
        ),
        "job": position,
        "profile": candidate,
    }
