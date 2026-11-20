# Veyora AI

**Intelligent Recruitment. Smarter Career Growth.**

Veyora AI is a two-sided hiring platform. A recruiter pastes a job description and
uploads resumes; a pipeline of AI agents parses them, scores fit against an
explainable rubric, decides who is shortlisted and books interview slots.
Candidates get the other half: resume review, job compatibility, skill-gap
coaching, interview practice, a learning roadmap — and the real requirements of
every open role.

- **Stack:** Python 3 + Flask + SQLite, vanilla HTML/CSS/JavaScript, Google Gemini.
- **8 live agents** in `agents/platform_agents.py`.
- **52 automated tests**, all passing.
- Every score is computed by a deterministic rubric in `services/scoring.py`.
  The language model never invents the number — it structures text and writes
  explanations. When Gemini is unavailable, every agent has a deterministic
  local fallback, so the app always works.

Jump to: [Features](#features) · [Pipeline](#the-pipeline-explained) ·
[Agents](#the-8-agents) · [System prompts](#system-prompts) ·
[API](#api-reference) · [Database](#database) · [Setup](#run-it-locally) ·
[Demo](#demo-in-90-seconds) · [Limitations](#known-limitations)

---

## What this project is

| Role | What they do | What the system does for them |
|---|---|---|
| **Recruiter** | Creates a job opening, uploads resumes | Parses resumes, scores each candidate 0–100, shortlists/rejects automatically at 60/100, books interviews, drafts invitations |
| **Candidate** | Uploads one resume, picks a role or pastes a JD | Reviews the resume, scores fit, lists missing skills with practice tasks, generates interview questions with sample answers and feedback, builds a learning roadmap |

Two separate authenticated workspaces on one Flask app, with role-checked APIs:

- `/recruiter` — recruiter workspace
- `/candidate` — candidate workspace
- `/` — landing page (signed out) or redirect to the right workspace

---

## Features

### Recruiter workspace

**Dashboard**
- Counts: jobs, candidates reviewed, shortlisted, interviews.
- **AI screening decisions** — the newest decisions with score, threshold and reason.
- Recent jobs, upcoming interviews, AI/email status.

**Jobs**
- Create, edit (load into form), delete an opening: title, description, required
  skills, preferred skills, minimum years, seniority.
- "Analyse job description" runs the Job Description Analyst and fills the
  structured requirements from free text.
- Deleting a job that already has candidate records is blocked (audit trail).

**Candidate Review (the main pipeline)**
1. Job description — pick a saved job or paste one; optional requirement extraction.
2. Resumes — drag & drop PDF / DOCX / TXT, up to 20 files, 50 MB request limit.
3. **REVIEW RESUME** — starts the backend pipeline and shows every stage live.

Results, per candidate:
- **A. Resume analysis** — contact, experience, education, skills, internships,
  projects, certifications, achievements. Evidence only, never invented.
- **B. Job compatibility** — score /100, weighted breakdown, matched / partial /
  missing skills, resume evidence, "why this score".
- **C. Screening decision (AI)** — shortlisted or rejected, score vs threshold,
  reasons, blockers, next step. Applied automatically, no manual shortlisting.
- **D. Interview planning** — technical/project/experience/behavioural/
  problem-solving/gap questions plus critical claims to verify.
- **E. Status & override** — the agent's status, with manual override buttons.
- Reserved interview slot(s) for that candidate, when shortlisted.
- **Download JSON** of the whole review, **Compare candidates** shortcut.

**Candidate Comparison** — scores, mandatory/preferred coverage, experience,
missing skills, status and per-component breakdown side by side.

**Interview Planning** — every reviewed candidate's question plan and the claims
to verify, grouped per job.

**Interview Management**
- Slots auto-created by the scheduling agent (status `reserved`) plus manual slots.
- Assign a candidate, confirm, cancel.
- Double-booking protection: overlapping time **or** the same candidate twice.
- Invitation preview (editable subject/body) and send.
- Without SMTP the send is recorded as a clearly labelled **demo** delivery —
  the app never claims an email was sent.

**Settings** — AI mode/model, email configuration, required env vars, counts.

### Candidate workspace

**Dashboard**
- Resume upload + parse, structured profile snapshot, recent analyses.
- **Roles open right now** — every recruiter job with required/preferred skills and
  two shortcuts: "Required skills & gaps" and "Interview questions".

**Resume Review** — optional target job; strengths, weak descriptions, missing
achievements, unnecessary content, formatting, keywords (advice only).

**Job Compatibility** — score and breakdown against an open role or a pasted JD.

**Skill Gap Analysis** — the exact skills the role requires, then prioritised gaps
with why it matters, learning topics and a practice task.

**Interview Preparation** — questions by category with what's tested, concepts,
answer structure, sample answer, common mistakes, a **practice mode** with
AI feedback, and critical areas an interviewer will probe.

**Career Roadmap** — weekly plan (focus, goals, exercises, project, milestone),
saved with progress checkboxes.

**Settings** — AI status, stored data, account role.

---

## The pipeline explained

*(This is the section to explain to a professor.)*

A **pipeline** is a sequence of stages where each stage takes the previous stage's
output, does one job, and passes a richer result to the next stage. It is the same
idea as an assembly line: resume text → structured profile → score → gaps →
interview plan → decision → booked interview. Nothing is one giant function; each
step is small, testable and reports its own status.

Why this project is a pipeline (three properties to point at):

1. **Chained artifacts.** `run_review()` (`services/review_pipeline.py`) keeps one
   list of candidate records and enriches it stage by stage: `profile` →
   `report` (score) → `gaps` → `questions` → `decision`.
2. **Progress is observable.** Each stage calls `add_stage(...)` with
   `running / progress / complete / warning / error`, streamed to the browser over
   Server-Sent Events (`GET /api/reviews/<id>/stream`) with polling fallback.
3. **Failure is contained.** A bad resume is skipped with a warning; a provider
   outage degrades to the deterministic fallback; a fatal error marks the review
   `error` and can be retried (`POST /api/reviews/<id>/retry`) from the stored payload.

### Recruiter review pipeline (background thread, per review)

| # | Stage (visible in the UI) | Agent | Output carried forward |
|---|---|---|---|
| 0 | Validate inputs | — (Flask route) | job, ≤20 resumes, ownership checks |
| 1 | Analyse job description | `JobDescriptionAnalyst` | title, seniority, min years, required/preferred skills, competencies, responsibilities |
| 2 | Parse resumes | `ResumeIntelligenceAgent` | structured profile per candidate |
| 3 | Score compatibility | `CompatibilityScoringAgent` | 0–100 report + breakdown + evidence |
| 4 | Analyse skill gaps | `SkillGapCoachAgent` | prioritised gaps + resume critique |
| 5 | Generate interview questions | `InterviewStrategistAgent` | question categories + critical areas |
| 6 | AI screening decision | `ScreeningDecisionAgent` | shortlisted / rejected + reasons |
| 7 | Schedule interviews | `RecruitmentSchedulingAgent` | reserved, non-overlapping slots |
| 8 | Save results | — (SQLite) | applications, review JSON, statuses, slots |

Flow:

```
POST /api/reviews/start (multipart: job + resumes)
        │  202 + review_id, work runs in a daemon thread
        ▼
 Validate → JobDescriptionAnalyst → ResumeIntelligenceAgent
        → CompatibilityScoringAgent → SkillGapCoachAgent
        → InterviewStrategistAgent → ScreeningDecisionAgent
        → RecruitmentSchedulingAgent → persist to SQLite
        │  every stage appends to services/jobstore.REVIEWS
        ▼
GET /api/reviews/<id>/stream  (SSE)  ──►  live stage list + progress bar
GET /api/reviews/<id>          (poll) ─►  same data, fallback
```

The decisions are persisted in `applications.status`, so comparison, interview
planning and interview management all read one source of truth.

### Candidate "pipelines"

Candidate actions are short on-demand chains rather than background jobs:

| Action | Chain |
|---|---|
| Resume upload | `ResumeIntelligenceAgent` → store profile |
| Resume review | `JobDescriptionAnalyst` (if a JD is given) → `CompatibilityScoringAgent` → `SkillGapCoachAgent` |
| Compatibility | Job analyst → scoring agent |
| Skill gaps | Job analyst → scoring → gap coach |
| Interview prep | Job analyst → scoring → gap coach → `InterviewStrategistAgent(for_candidate=True)` |
| Practice answer | `InterviewStrategistAgent.review_answer()` |
| Roadmap | Job analyst → scoring → gap coach → `CareerIntelligenceAgent` |

### Legacy pipeline (archived)

The original prototype had its own coordinator + six agents (parser, matcher,
ranker, bias guard, scheduler, career coach). It is **not used by either
dashboard**. It now lives in `agents/legacy/`, is still reachable through the
legacy endpoints in `app.py`, and is kept only for reference. Ignore it for the
demo; the eight agents above are the system.

---

## The 8 agents

All live in `agents/platform_agents.py`. "Fallback" means the deterministic local
implementation used when there is no API key, when Gemini is rate-limited, or when
the model returns unusable JSON.

| # | Agent | Used by | Task | Fallback |
|---|---|---|---|---|
| 1 | **Resume Intelligence Agent** | recruiter review, candidate upload | Resume text → structured profile (contact, summary, skills, experience, education, projects, certs, years) | Rule-based section parsing, skill lexicon, date arithmetic |
| 2 | **Job Description Analyst** | recruiter jobs/analyze + review, candidate analyses | JD text → title, seniority, min years, required vs preferred skills, competencies, responsibilities | Heading-aware line scanning + skill lexicon |
| 3 | **Compatibility Scoring Agent** | both workspaces | Deterministic 0–100 score + weighted breakdown + rationale | Score always from `services/scoring.py`; rationale template |
| 4 | **Skill Gap & Resume Coach** | recruiter review (feeds questions/decision), candidate gaps | Missing/partial skills → importance, why, learning topics, practice task + resume critique | Gap heuristics, weak-phrase and quantification checks |
| 5 | **Interview Strategist Agent** | recruiter review + planning, candidate prep/practice | Tailored question plan; candidate mode adds sample answers, structure, mistakes; answer feedback | Question templates by category, rubric-based feedback |
| 6 | **Recruitment & Scheduling Agent** | recruiter review (auto-booking), invitations | Non-overlapping slot per shortlisted candidate + invitation draft | Availability tool + conflict checks against the DB |
| 7 | **Screening Decision Agent** | recruiter review | Applies the shortlist rule and explains it (reasons, blockers, next step) | Same rule in code; template reasons |
| 8 | **Career Intelligence Agent** | candidate roadmap | 4–8 week learning plan with weekly goals, exercises, project, milestone | Gap-ordered plan from the learning-topic table |

Each agent is a small class with `run(...)` (and `review_answer` / `auto_schedule` /
`compose_invitation` where relevant). They are deliberately independent: the
pipeline passes plain dictionaries between them, so any agent can be tested alone
(`tests/test_gemini_paths.py` does exactly that).

### How they are connected

```
                       ┌──────────────────────────────┐
  recruiter JD  ─────► │ 1 JobDescriptionAnalyst      │
  recruiter PDF ─────► │ 2 ResumeIntelligenceAgent    │
                       └──────────────┬───────────────┘
                                      ▼
                       ┌──────────────────────────────┐
                       │ 3 CompatibilityScoringAgent  │◄── services/scoring.py (the number)
                       └──────────────┬───────────────┘
                                      ▼
                       ┌──────────────────────────────┐
                       │ 4 SkillGapCoachAgent         │
                       └──────────────┬───────────────┘
                                      ▼
                       ┌──────────────────────────────┐
                       │ 5 InterviewStrategistAgent   │
                       └──────────────┬───────────────┘
                                      ▼
                       ┌──────────────────────────────┐     rule: score ≥ 60 → shortlist
                       │ 7 ScreeningDecisionAgent     │     score < 60 → rejected
                       └──────────────┬───────────────┘
                                      ▼  shortlisted only
                       ┌──────────────────────────────┐
                       │ 6 RecruitmentSchedulingAgent │──► interview_slots (reserved)
                       └──────────────────────────────┘

  candidate ──► 2 ResumeIntelligenceAgent
            ──► 1 JobDescriptionAnalyst ──► 3 Compatibility ──► 4 SkillGap
            ──► 5 InterviewStrategist (candidate mode) ──► practice feedback
            ──► 8 CareerIntelligenceAgent
```

---

## System prompts

Every LLM call is JSON-only. `llm_json()` appends this instruction to each prompt:

> "Return ONLY a valid JSON object. No markdown, no commentary. Use only
> information supported by the input; never invent facts."

Calls use `response_mime_type="application/json"`, `temperature=0.2`, per-agent
`max_output_tokens`, a configurable timeout (`GEMINI_TIMEOUT_MS`, default 30 s),
and the model from `GEMINI_MODEL` (default `gemini-3.8-flash`).

The prompts below are the actual system prompts (condensed where they only list
output keys). Every one contains an explicit no-invention rule and, where relevant,
a no-protected-attributes rule.

### 1. Resume Intelligence Agent
> You are the Resume Intelligence Agent. Extract structured data from this resume.
>
> Return JSON with exactly these keys: name, email, phone, location, links,
> summary (2–3 sentences using only resume facts), technical_skills, soft_skills,
> total_years, experience[{title, company, duration, description}],
> internships[...], projects[{name, description, technologies}],
> education[{degree, institution, year}], certifications, achievements.
>
> RULES: Never invent information. Leave a field empty/[] when the resume does not
> contain it. total_years is total professional experience in years (decimal
> allowed), computed from employment dates. technical_skills must use short
> canonical names ("Python", "AWS", "PostgreSQL"). Only include a soft skill if the
> resume demonstrates it.

### 2. Job Description Analyst
> You are the Job Description Analyst. Extract hiring requirements.
>
> Return JSON: title, seniority ("Junior|Mid|Senior|Lead|Principal or empty"),
> min_years, education_requirement, required_skills, preferred_skills,
> competencies, responsibilities.
>
> RULES: Separate must-have requirements from preferred ones. Do not mix them. Use
> short canonical skill names. min_years is the minimum years of experience stated
> in the description (0 if not stated). Only include skills the description
> actually asks for.

### 3. Compatibility Scoring Agent
> You are the Compatibility Scoring Agent. The score has already been calculated by
> a deterministic rubric and MUST NOT be changed.
>
> Context: score/grade, job JSON, matched/partial/missing skills, experience.
>
> Write JSON: {"rationale": "2 sentences explaining the score using only the
> evidence above"}. Do not mention names, age, gender, nationality or any personal
> attribute.

### 4. Skill Gap & Resume Coach
> You are the Skill Gap & Resume Coach. Use ONLY the resume and job data below.
>
> Return JSON: gaps[{skill, importance High|Medium|Low, why, learn[], practice_task,
> level missing|partial}] and resume_review{strengths[], weaknesses[],
> missing_achievements[], unnecessary[], formatting[], keywords[]}.
>
> RULES: Cover every missing and partial requirement. Never invent experience or
> achievements; phrase suggestions as advice. Be specific: name the skill, the topic
> and the exercise.

### 5. Interview Strategist Agent
> You are the Interview Strategist Agent. Build an interview plan grounded in the
> resume and job description below.
>
> Return JSON: categories{technical, project, experience, behavioral,
> problem_solving, gap} each with questions[{question, purpose,
> expected_concepts[], evaluation_criteria[], related_skill}] and
> critical_areas[{area, claim, why, verify_with}].
>
> RULES: 2–3 questions per category, each tied to this candidate's actual resume
> details. Never ask about age, gender, ethnicity, religion, disability, marital
> status or nationality. Critical areas must point to specific resume claims a
> recruiter should verify. *Candidate mode adds:* sample answers, answer structure
> and common mistakes for a candidate preparing alone; *recruiter mode:* focus on
> verification and evaluation.

Practice feedback prompt:
> You are the Interview Strategist Agent coaching a candidate. Score 0–100 against
> this rubric: structure 25, specificity/metrics 25, concept coverage 30, clarity &
> honesty 20. Return JSON: score, strengths[], improvements[], model_answer,
> concepts_covered[], next_step. Be concrete and encouraging.

### 6. Recruitment & Scheduling Agent
No language-model call. It is deterministic on purpose: an interview must never be
double-booked because a model felt creative. It:
- asks `tools/availability_tool.py` for open slots (mock corporate calendar, 3
  working days out, weekdays only, 60 minutes),
- skips any slot overlapping the recruiter's existing non-cancelled slots,
- books one slot per shortlisted candidate and marks it `reserved`,
- drafts the invitation text (subject, date/time, timezone, mode, interviewer,
  location, instructions, reply-to-reschedule paragraph) — nothing is sent until
  the recruiter presses send.

### 7. Screening Decision Agent
> You are the Screening Decision Agent for a recruiting team.
>
> NON-NEGOTIABLE SCREENING RULE
> - The shortlist threshold is 60 out of 100.
> - A candidate at 60 or above MUST be shortlisted.
> - A candidate below 60 MUST be rejected.
> - The score comes from the platform's deterministic rubric. Never recalculate it.
>
> Context: job JSON, candidate profile, score/grade, matched/partial/missing skills,
> required skills, experience vs minimum, top gaps.
>
> Return JSON: summary (2 sentences explaining this outcome against the rule),
> reasons[] (specific, tied to a score, skill or experience fact), blockers[] (only
> for rejected candidates), next_step.
>
> RULES: Write the explanation only; the platform applies the decision itself. 2–4
> reasons tied to a number or named skill. Never invent experience, skills,
> achievements or employers. Never suggest changing the threshold or overriding the
> rule.

The threshold is enforced in code as well, and can be changed with
`SCREENING_THRESHOLD` (default `60`). Because the decision is a pure comparison,
the same candidate always gets the same outcome.

### 8. Career Intelligence Agent
> You are the Career Intelligence Agent. Build a practical learning roadmap.
>
> Return JSON: summary (2 sentences on the fastest path to this role),
> total_weeks, weeks[{week, focus, goals[], exercises[], project, milestone}],
> milestones[].
>
> RULES: Order learning by job importance: mandatory gaps first. 4 to 8 weeks; each
> week must be achievable in 6–8 focused hours. Every week needs a concrete exercise
> and a measurable milestone. Never suggest inventing experience for the resume.

### Reliability behaviour shared by every prompt

- **Provider cooldown.** On a 429/daily-quota error the key is marked unavailable
  for 10 minutes (`QUOTA_COOLDOWN_SECONDS`), on a transient 5xx for 60 s. Remaining
  agents in the same review use their local fallback immediately instead of
  retrying — a review stays fast when the quota is dead.
- **No invented facts.** The prompt says it, and the merge code only accepts
  well-shaped fields; scores come from code only.
- **Explainability.** Each agent returns `reasons`, `evidence`, `why` or
  `rationale` fields that the UI renders next to the result.

---

## Compatibility scoring (the number)

`services/scoring.py` — deterministic, no model involved:

| Component | Weight |
|---|---|
| Mandatory skills | 45 |
| Preferred skills | 20 |
| Experience | 15 |
| Project relevance | 10 |
| Education & certifications | 5 |
| Role competencies | 5 |

Grades: ≥85 Excellent, ≥70 Strong, ≥55 Moderate, ≥40 Weak, below 40 Limited.
Partial matches earn half credit. Every component returns `weight`, `ratio`,
`earned`, `detail` and reasons, so the UI can show exactly where the points came
from. Same inputs → same score, always.

**Shortlist rule:** score ≥ 60 → `shortlisted`; below 60 → `rejected`. The agent
applies it; the recruiter can still override manually.

---

## API reference

All `/api/*` routes require a signed-in session with the matching role.
`GET` reads, `POST` creates/acts, `PATCH` updates, `DELETE` removes.

### Pages (Flask, `app.py`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Landing page, or redirect to the role workspace |
| GET | `/recruiter` | Recruiter workspace (recruiter only) |
| GET | `/candidate` | Candidate workspace (candidate only) |
| GET/POST | `/login`, `/register` | Role-aware authentication |
| POST | `/logout` | Sign out |
| GET | `/api/sample-data` | Demo job description + resume filenames |
| GET | `/api/sample-resumes/<filename>` | Demo resume file |

### Recruiter API (`routes/recruiter.py`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/dashboard` | Counts, jobs, upcoming interviews, recent AI decisions, threshold |
| GET | `/api/jobs` | List jobs + application counts |
| POST | `/api/jobs` | Create job |
| PATCH | `/api/jobs/<id>` | Update job |
| DELETE | `/api/jobs/<id>` | Delete job (blocked if applications exist) |
| POST | `/api/jobs/analyze` | Job Description Analyst on free text |
| POST | `/api/reviews/start` | Validate, create review, run pipeline in a thread (202) |
| GET | `/api/reviews/<id>` | Review status, stages, result |
| GET | `/api/reviews/<id>/stream` | SSE stage/progress stream (420 s window) |
| POST | `/api/reviews/<id>/retry` | Re-run a failed review from its stored payload |
| GET | `/api/jobs/<id>/applications` | Reviewed candidates for a job |
| GET | `/api/applications/<id>` | One candidate record |
| POST | `/api/applications/<id>/status` | Manual override of the AI decision |
| GET | `/api/jobs/<id>/comparison` | Side-by-side comparison |
| GET | `/api/interview-slots` | List slots |
| POST | `/api/interview-slots` | Create slot (conflict-checked) |
| PATCH | `/api/interview-slots/<id>` | Edit slot |
| DELETE | `/api/interview-slots/<id>` | Cancel slot |
| POST | `/api/interview-slots/<id>/assign` | Assign candidate |
| POST | `/api/interview-slots/<id>/confirm` | Confirm interview |
| POST | `/api/interview-slots/<id>/email-preview` | Draft invitation |
| POST | `/api/interview-slots/<id>/send-email` | Send (SMTP or labelled demo, once per slot) |
| GET | `/api/email-log` | Delivery history |
| GET | `/api/settings` | AI/email status, env vars, counts |

### Candidate API (`routes/candidate.py`)

| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/api/candidate/resume` | Latest resume / upload + parse |
| GET | `/api/candidate/dashboard` | Profile, recent reports, roadmap, AI status |
| GET | `/api/candidate/jobs` | Open roles with required/preferred skills |
| POST | `/api/candidate/compatibility` | `job_id` or `job_description` → score + gaps |
| POST | `/api/candidate/resume-review` | Resume critique (optionally role-specific) |
| POST | `/api/candidate/skill-gaps` | Required skills + prioritised gaps |
| POST | `/api/candidate/interview-prep` | Question plan, sample answers, critical areas |
| POST | `/api/candidate/practice` | Feedback on a written answer |
| POST/GET | `/api/candidate/roadmap` | Create / read latest roadmap |
| POST | `/api/candidate/roadmap/progress` | Toggle a week |
| GET | `/api/candidate/reports` | Stored analyses |
| GET | `/api/candidate/settings` | AI status, counts |

### Legacy prototype endpoints (`app.py`, archived)

`POST /api/run-pipeline`, `GET /api/stream/<job_id>`, `GET /api/status/<job_id>`,
`GET /api/download/<job_id>`, `POST /api/candidate/review`,
`GET /api/email-settings`, `POST /api/invitations/<job_id>`.
They belong to the old single-page prototype (`legacy/`), not the dashboards.

### How the front end calls the API

- One `api(url, options)` helper in `static/js/recruiter.js` and
  `static/js/candidate.js`: `fetch` → JSON parse → on `!response.ok` throw an
  `Error` with the server's `error` message and status.
- Network failure (server stopped) is caught separately and turned into an
  actionable message: "The Veyora server did not respond… start it again with
  `python app.py`".
- `POST /api/reviews/start` sends `FormData` (job fields + `resumes` files);
  other writes send `application/json`.
- Progress uses `EventSource('/api/reviews/<id>/stream')`; if that closes, the UI
  falls back to polling `GET /api/reviews/<id>` every second.
- Session cookies carry authentication; every private route is decorated with
  `@role_required("recruiter" | "candidate")`.

---

## Database

SQLite, created/upgraded idempotently on startup (`db.py`), default file
`instance/veyora.sqlite3`.

| Table | Holds |
|---|---|
| `users` | name, email, password hash (Werkzeug), role |
| `jobs` | title, description, required/preferred skills (JSON), min years, seniority |
| `resumes` | uploaded text + parsed profile (JSON), owner |
| `reviews` | one row per pipeline run: status, stages result, AI mode, error |
| `applications` | one row per candidate per review: score, **status**, full analysis JSON |
| `interview_slots` | date/time, candidate, interviewer, mode, status (`available`/`reserved`/`confirmed`/`cancelled`) |
| `email_log` | every delivery attempt (sent / demo / failed) |
| `candidate_reports` | candidate analyses (compatibility, gaps, prep, roadmap) |
| `roadmaps` | candidate learning plan + week progress |

---

## Run it locally

```bash
# 1. dependencies
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt

# 2. configuration
copy .env.example .env          # then edit .env
#    GEMINI_API_KEY=...          (optional — the app works fully without it)
#    SCREENING_THRESHOLD=60      (optional)

# 3. demo data
python tools/seed_demo.py

# 4. run
python app.py
# open http://127.0.0.1:5000  (or the port in FLASK_PORT)
```

Demo accounts (password `VeyoraDemo2026!`):

| Role | Email |
|---|---|
| Recruiter | `recruiter@veyora.demo` |
| Candidate | `candidate@veyora.demo` |

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | empty | Gemini key; without it every agent uses its local fallback |
| `GEMINI_MODEL` | `gemini-3.8-flash` | Model name |
| `GEMINI_TIMEOUT_MS` | `30000` | Per-request timeout |
| `SCREENING_THRESHOLD` | `60` | Shortlist cut-off (0–100) |
| `FLASK_SECRET_KEY` | random per boot | Session signing (set it to keep sessions across restarts) |
| `FLASK_DEBUG` | `false` | Debug mode |
| `FLASK_PORT` | `5000` | Dev server port |
| `VEYORA_DATABASE` | `instance/veyora.sqlite3` | SQLite path |
| `SMTP_HOST/PORT/USER/PASSWORD/FROM` | empty | Real email delivery; blank = labelled demo mode |

---

## Demo in 90 seconds

1. Sign in as the recruiter → **Candidate Review**.
2. **Load demo job + resumes** → **REVIEW RESUME**.
3. Watch the stages tick: job analysis → parsing → scoring → gaps → questions →
   screening decision → scheduling.
4. Open a candidate tab: score breakdown, AI decision, reasons, booked slot.
5. **Dashboard**: "AI screening decisions" shows who was shortlisted/rejected and why.
6. **Interview Planning** / **Comparison** read the same stored analyses.
7. Sign in as the candidate → **Dashboard** → pick an open role →
   "Required skills & gaps" → then **Interview Preparation** → answer a question →
   **Get AI feedback**.

More detail in `docs/DEMO_SCRIPT.md`.

---

## Tests

```bash
python -m unittest discover -s tests
# Ran 52 tests ... OK
```

| File | Covers |
|---|---|
| `tests/test_platform.py` | auth/role isolation, scoring determinism, full review pipeline, job reuse, comparison, slot conflicts, email demo logging |
| `tests/test_gemini_paths.py` | each agent with a mocked Gemini client, including junk-response fallbacks and cooldown behaviour |
| `tests/test_pipeline.py` | file extraction, sample data, candidate review endpoint, legacy scheduler/bias/base agent, error handling |

---

## Known limitations

- **Gemini free tier.** 20 requests/day. When exhausted the app logs the 429, pauses
  the provider for 10 minutes and uses local logic — results remain complete,
  explanations are template-based.
- **Interview slots are a mock calendar** (`tools/availability_tool.py`). No Google
  Calendar/Outlook integration; slots are stored in SQLite.
- **Email** requires SMTP credentials; without them the attempt is recorded as
  `demo` and the UI says so.
- **Review progress is in memory.** A server restart loses a running review's live
  stage list; completed results are in SQLite. Failed reviews can be retried.
- **No bias-review stage** in the live pipeline. Bias is mitigated by scoring only
  job-relevant evidence and by the prompts' protected-attribute rules; the archived
  prototype had a separate bias agent.
- **SQLite + Flask dev server.** Fine for a demo or a single team, not production
  scale (use a real WAIS/WSGI server and Postgres for that).
- Resume parsing supports PDF/DOCX/TXT; scanned image-only PDFs are not OCR'd.

---

## Project structure

```
app.py                     Flask app, auth pages, legacy endpoints
auth_utils.py              current_user / login_required / role_required
db.py                      schema (9 tables) + connection helpers
agents/
  platform_agents.py       the 8 live agents (+ scoring-adjacent helpers)
  legacy/                  archived prototype agents (not used by the dashboards)
routes/
  recruiter.py             jobs, review pipeline, comparison, interviews, email
  candidate.py             resume, compatibility, gaps, prep, practice, roadmap
services/
  review_pipeline.py       the 8-stage recruiter pipeline
  scoring.py               deterministic weighted rubric
  skills.py                skill lexicon, canonicalisation, matching
  jobstore.py              in-memory review stage store for SSE/polling
  extraction.py            PDF/DOCX/TXT text extraction
  emailer.py               SMTP delivery + demo logging
templates/                 landing, auth, recruiter, candidate
static/js/                 recruiter.js, candidate.js
static/css/style.css       one shared stylesheet
tools/
  availability_tool.py     mock interview calendar
  seed_demo.py             demo users + jobs + resumes
tests/                     52 unittest cases
docs/DEMO_SCRIPT.md        click-by-click demo
legacy/                    archived prototype page + script
```

---

## Tech stack

| Layer | Technology | Why |
|---|---|---|
| Language | Python 3 | one language for web + AI + data |
| Web framework | Flask 3 | small, explicit routing; blueprints per role |
| Templating | Jinja2 | server-rendered pages, no SPA build step |
| Front end | HTML5, CSS3, vanilla JavaScript | zero build tooling, easy to demo |
| Live progress | Server-Sent Events | one-way stream fits stage updates |
| Database | SQLite (`sqlite3`) | zero-config, file-based, easy to inspect |
| AI | Google Gemini via `google-genai` | structured JSON extraction + explanations |
| Auth | Werkzeug password hashing + signed session cookies | built into Flask |
| Resume parsing | PyPDF2, python-docx | PDF/DOCX text extraction |
| Config | python-dotenv | `.env` for keys |
| PDF demo data | fpdf2 | generated sample resumes |
| Tests | `unittest` + mocks | no network needed |

---

## License / attribution

Student project. Demo credentials and sample resumes are fictional.
