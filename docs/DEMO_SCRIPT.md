# Veyora AI — 8-minute demo script

For a live demo in front of a professor. Every step below was written against the
current UI, and every action calls the real backend — no sample results are
hardcoded anywhere.

## 0. Before the demo (2 minutes, once)

```powershell
python data\generate_samples.py      # only if data\sample_resumes\ is empty
python tools\seed_demo.py            # creates demo accounts + real pipeline data
python app.py                        # http://127.0.0.1:5000
```

Seed accounts (printed by the script):

| Role | Email | Password |
|------|-------|----------|
| Recruiter | `recruiter@veyora.demo` | `VeyoraDemo2026!` |
| Candidate | `candidate@veyora.demo` | `VeyoraDemo2026!` |

Open **two browser windows** (or one normal + one private window) so both roles
stay signed in: window A for the recruiter, window B for the candidate.

To rehearse again with fresh data, stop the server and rerun
`python tools\seed_demo.py` — it resets only the two demo accounts.

Say once, up front: *"The seeded data was produced by the app itself: five
resumes were parsed, scored, gap-analysed and converted into interview plans by
the pipeline before this demo started."*

## 1. Landing page — 30 s

1. Open <http://127.0.0.1:5000>. Point at the tagline **“Intelligent
   Recruitment. Smarter Career Growth.”**
2. Point at the two role cards: recruiter and candidate.
3. Say: *"One platform, two workspaces. Both sides use the same scoring rubric."*

## 2. Recruiter sign-in and dashboard — 45 s

1. Click **Recruiter** (or **Sign in**), enter `recruiter@veyora.demo` /
   `VeyoraDemo2026!`, click **Sign in**.
2. Land on **Dashboard**. Read the four counters: jobs, candidates reviewed,
   shortlisted, interviews.
3. Point at the status card: AI mode (**Deterministic local analysis** unless a
   Google Gemini key is configured) and email delivery (**Demo mode** unless SMTP
   is configured). Say: *"The app tells you which mode it is in instead of
   pretending."*

## 3. Jobs — 20 s

1. Click **Jobs** in the sidebar.
2. Show the seeded **Senior Python Developer** role with its required and
   preferred skills and candidate count.
3. Say: *"Requirements came from the job description, not from me typing them."*

## 4. Candidate Review Workspace — 2 min (the centrepiece)

1. Click **Candidate Review**.
2. Step 1: open **Select an existing job** and choose **Senior Python Developer**.
   The title and description fill in.
3. Optional: click **Analyse job description** and show the extracted
   mandatory/preferred split.
4. Step 2: point at the drop zone. Say: *"The seed already reviewed five resumes;
   I’ll run a fresh review now."* Drag in `data\sample_resumes\*.pdf` or click
   the zone and select two PDFs.
5. Step 3: click the large **REVIEW RESUME** button.
6. Narrate the live stage list as it appears: **Validate inputs → Analyse job
   description → Parse resumes → Score compatibility → Analyse skill gaps →
   Generate interview questions → AI screening decision → Schedule interviews →
   Save results**. Each line is a real server stage streamed over SSE, and each
   stage is one agent (see the README).
7. When results appear, click through the candidate tabs and say what each
   section proves:
   - **A. Resume analysis** — parsed contact, skills, experience, projects,
     education, certifications. *"Only evidence found in the resume."*
   - **B. Job compatibility** — the score out of 100 plus the weighted
     breakdown (mandatory 45 / preferred 20 / experience 15 / projects 10 /
     education 5 / competencies 5), matched vs missing skills and the reason for
     each.
   - **C. Screening decision (AI)** — the Screening Decision Agent applies the
     rule *60/100 or above = shortlisted, below = rejected* and explains it:
     reasons, blockers and next step. Say: *"I only supplied the job description
     and the resumes; the agent made the call."*
   - **D. Interview planning** — technical, project, experience, behavioural,
     problem-solving and gap questions with purpose and evaluation criteria,
     plus **critical areas to verify**.
   - **E. Status & override** — the status was set automatically by the agent.
     A recruiter can still override it if they disagree, but no manual
     shortlisting is required.
   - **Scheduling Agent card** — shortlisted candidates already have a
     non-overlapping interview slot reserved (status `reserved`). Rejected
     candidates have none.
8. Click **Compare candidates**.

## 5. Candidate Comparison — 30 s

1. Choose the job in the **Job** dropdown, click **Load comparison**.
2. Show side-by-side scores, shared/missing skills and each component
   breakdown. Say: *"A recruiter can defend a ranking component by component."*

## 6. Interview Planning — 20 s

1. Click **Interview Planning**.
2. Choose the job, click **Load interview plans**.
3. Expand one candidate and read a question plus one **critical area**, e.g.
   a Kubernetes claim that only appears once in the resume.

## 7. Interview Management + email — 90 s

1. Click **Interview Management**.
2. Show the seeded **Confirmed** slot with the candidate name and the free
   **Available** slot.
3. **Double-booking test:** in **Create an interview slot**, pick the *same date*
   as the confirmed slot, start `10:00`, end `11:00`, click **Create slot**. A red
   notice rejects it as overlapping — nothing is saved.
4. Change the time to `16:00`–`17:00`, click **Create slot**. It is accepted.
5. On the new slot click **Assign**, choose a candidate, then **Confirm** — the
   status pill moves Reserved → Confirmed.
6. Click **Preview email** on a slot. The panel opens with the recipient,
   subject and body rendered by the scheduling agent. Edit one sentence.
7. Click **SEND EMAIL**.
   - Without SMTP credentials: a clear **demo mode** banner says the email was
     *not* sent and was only logged. Say: *"It refuses to claim a send it did not
     make."*
   - With SMTP configured: it sends through the mail server and logs status
     `sent`.
8. Click **SEND EMAIL** again on the same slot → duplicate send is blocked with a
   notice.

## 8. Candidate side — 2 min

Switch to window B.

1. Open <http://127.0.0.1:5000>, sign in with `candidate@veyora.demo` /
   `VeyoraDemo2026!`.
2. **Dashboard**: the seeded resume is already parsed — name, experience,
   skills, education, projects.
3. **Resume Review** → click **REVIEW MY RESUME**. Strengths, weak descriptions,
   missing achievements, formatting and keyword advice appear.
4. **Dashboard → Roles open right now** — point at the recruiter's real job
   postings with the exact required and preferred skills. This is what used to
   sit in the recruiter view; it now belongs to the candidate.
5. **Job Compatibility** → either pick an **open role** or paste a job
   description, click **Check compatibility**. Same rubric and breakdown the
   recruiter saw.
6. **Skill Gap Analysis** → pick the open role (or paste a JD) and click
   **Analyse my gaps**. The view starts with **“Skills <role> requires”**
   (required vs preferred), then prioritised gaps with learning topics and a
   practice task.
7. **Interview Preparation** → pick the open role and click **Generate my
   interview plan**, open a question, then use **practice mode**: type a 3–4
   sentence answer and submit. A score with strengths and concrete improvements
   comes back.
8. **Career Roadmap** → click **Build my roadmap**. Tick one week’s checkbox,
   then click another sidebar item and return: progress persisted in SQLite.
9. **Settings** shows the same AI/email status as the recruiter side.

## 9. Close — 30 s

1. Back on the recruiter window, click **Settings**.
2. Point at the AI/email status and the required environment variables.
3. Say: *"Scores are deterministic code, the model can only add narrative. With
   no AI key the app runs in labelled local mode; with no mail credentials it
   records a demo instead of faking delivery."*

## If something goes wrong

| Symptom | Recovery |
|---------|----------|
| Wrong password | Rerun `python tools\seed_demo.py` and use the printed password |
| Review fails on stage 4+ | The error banner names the failure; click **Run review** again — the retry endpoint reuses the stored inputs |
| Slots from a previous run | Expected on the same date; pick another day or rerun the seed script |
| “Gemini not configured” | Intended: local mode. Do not insert a key live — explain the fallback instead |
| Page shows stale data | Hard refresh (Ctrl+F5); all data is server-side |

## 60-second version (if time is cut)

Sign in as recruiter → **Candidate Review** → pick job → **REVIEW RESUME** →
show sections A–E (C is the agent's automatic shortlist/reject decision) →
**Dashboard** to show the decisions card → **Interview Management** →
**Preview email** → **SEND EMAIL** (demo banner) → switch to the candidate →
open a role from **Roles open right now** → **Required skills & gaps** →
**Interview questions** → close on the honest AI/email status in **Settings**.
