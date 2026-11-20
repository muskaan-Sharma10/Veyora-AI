/* Veyora AI — Candidate workspace */
'use strict';

const state = {
  view: 'dashboard',
  resume: null,
  prepQuestions: [],
  roadmap: null,
  jobs: [],
  files: [],
};

const VIEW_META = {
  dashboard: ['Dashboard', 'Your resume, your gaps, your next step.'],
  resume: ['Resume Review', 'Specific, evidence-based improvements for your resume.'],
  compatibility: ['Job Compatibility', 'The same explainable scoring recruiters use.'],
  gaps: ['Skill Gap Analysis', 'Prioritised gaps with practice tasks.'],
  prep: ['Interview Preparation', 'Tailored questions, sample answers and practice feedback.'],
  roadmap: ['Career Roadmap', 'Weekly goals, projects and milestones.'],
  settings: ['Settings', 'AI configuration and your stored data.'],
};

const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[char]));

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

async function api(url, options = {}) {
  let response;
  try {
    response = await fetch(url, options);
  } catch (networkError) {
    // fetch only rejects when no response arrived at all (server stopped,
    // connection dropped, or the page lost the network).
    const err = new Error(
      'The Veyora server did not respond. If it has stopped, start it again with "python app.py", then retry.'
    );
    err.status = 0;
    err.cause = networkError;
    throw err;
  }
  let data = {};
  try { data = await response.json(); } catch (error) { data = {}; }
  if (!response.ok) {
    const err = new Error(data.error || `Request failed (${response.status})`);
    err.status = response.status;
    throw err;
  }
  return data;
}

function toast(message) {
  const node = $('#toast');
  if (!node) return;
  node.textContent = message;
  node.hidden = false;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => { node.hidden = true; }, 4200);
}

function list(items, fallback = 'None found') {
  if (!Array.isArray(items) || !items.length) return `<p class="muted">${esc(fallback)}</p>`;
  return `<ul>${items.map((item) => `<li>${esc(typeof item === 'string' ? item : item.description || JSON.stringify(item))}</li>`).join('')}</ul>`;
}

/* ─── Navigation ─────────────────────────────────────────────────────── */
function setView(name) {
  if (!VIEW_META[name]) return;
  state.view = name;
  $$('.view').forEach((view) => view.classList.toggle('active', view.id === `view-${name}`));
  $$('.nav-link').forEach((link) => link.classList.toggle('active', link.dataset.view === name));
  const [title, subtitle] = VIEW_META[name];
  $('#view-title').textContent = title;
  $('#view-subtitle').textContent = subtitle;
  $('#sidebar').classList.remove('open');
  if (name === 'settings') loadSettings();
  if (name === 'roadmap') loadRoadmap();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

/* ─── Resume ─────────────────────────────────────────────────────────── */
function initUpload() {
  const zone = $('#cand-drop');
  const input = $('#cand-file');
  if (!zone || !input) return;

  input.addEventListener('change', () => {
    if (input.files && input.files[0]) selectFile(input.files[0]);
  });
  zone.addEventListener('dragover', (event) => {
    event.preventDefault();
    zone.classList.add('dragging');
  });
  zone.addEventListener('dragleave', () => zone.classList.remove('dragging'));
  zone.addEventListener('drop', (event) => {
    event.preventDefault();
    zone.classList.remove('dragging');
    if (event.dataTransfer && event.dataTransfer.files[0]) selectFile(event.dataTransfer.files[0]);
  });
  $('#btn-upload-resume').addEventListener('click', uploadResume);
}

function selectFile(file) {
  state.files = [file];
  $('#upload-status').textContent = `${file.name} selected — press Upload & parse resume.`;
  $('#upload-status').className = 'form-notice ok';
}

async function uploadResume() {
  const file = state.files[0];
  const status = $('#upload-status');
  if (!file) {
    status.textContent = 'Choose a PDF, DOCX or TXT resume first.';
    status.className = 'form-notice';
    return;
  }
  const button = $('#btn-upload-resume');
  button.disabled = true;
  status.className = 'form-notice';
  status.textContent = 'Parsing your resume…';
  const body = new FormData();
  body.append('resume', file, file.name);
  try {
    const data = await api('/api/candidate/resume', { method: 'POST', body });
    state.resume = { id: data.resume_id, filename: data.filename, profile: data.profile };
    renderProfile(data.profile);
    status.className = 'form-notice ok';
    status.textContent = `Parsed ${data.filename} (${data.ai_mode} extraction).`;
    $('#resume-status').textContent = data.filename;
    $('#resume-pill').textContent = `Resume: ${data.filename}`;
    toast('Resume uploaded and parsed.');
  } catch (error) {
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

function renderProfile(profile) {
  const node = $('#resume-profile');
  if (!node) return;
  if (!profile) {
    node.innerHTML = '<p class="muted">Upload a resume to see your structured profile.</p>';
    return;
  }
  node.innerHTML = `
    <div class="profile-grid">
      <div class="profile-block"><h4>Name</h4><p>${esc(profile.name || 'Not detected')}</p></div>
      <div class="profile-block"><h4>Experience</h4><p>${esc(profile.total_years || 0)} years</p></div>
      <div class="profile-block"><h4>Email</h4><p>${esc(profile.email || 'Not detected')}</p></div>
      <div class="profile-block"><h4>Phone</h4><p>${esc(profile.phone || 'Not detected')}</p></div>
    </div>
    <div class="profile-block"><h4>Technical skills</h4><div>${(profile.technical_skills || []).map((s) => `<span class="tag tag-skill">${esc(s)}</span>`).join('') || '<p class="muted">None detected</p>'}</div></div>
    <div class="profile-block"><h4>Education</h4>${list((profile.education || []).map((e) => [e.degree, e.institution, e.year].filter(Boolean).join(' · ')), 'No education detected')}</div>
    <div class="profile-block"><h4>Projects</h4>${list((profile.projects || []).map((p) => p.name || p.description), 'No projects detected')}</div>`;
}

async function loadDashboard() {
  try {
    const data = await api('/api/candidate/dashboard');
    const ai = data.ai;
    $('#ai-pill').textContent = ai.configured ? `AI: ${ai.model}` : 'AI: local mode';
    $('#ai-pill').className = ai.configured ? 'pill' : 'pill pill-warn';
    const banner = $('#ai-banner');
    banner.textContent = ai.message;
    banner.className = ai.configured ? 'banner' : 'banner banner-warn';

    if (data.resume) {
      state.resume = { id: data.resume.id, filename: data.resume.filename, profile: data.resume.profile };
      renderProfile(data.resume.profile);
      $('#resume-status').textContent = 'Ready';
      $('#resume-pill').textContent = `Resume: ${data.resume.filename}`;
    }

    const reports = $('#recent-reports');
    reports.innerHTML = data.recent_reports.length
      ? data.recent_reports.map((report) => `
        <div class="slot-card">
          <div class="slot-head"><strong>${esc(report.kind.replace('_', ' '))}</strong><span class="pill pill-muted">${esc(report.created_at)}</span></div>
          <div class="slot-meta"><span>${esc(report.target_role || 'General')}</span></div>
        </div>`).join('')
      : '<p class="muted">No analyses yet.</p>';
  } catch (error) {
    toast(error.message);
  }
}

function ensureResume() {
  if (!state.resume) {
    toast('Upload your resume on the Dashboard first.');
    setView('dashboard');
    return false;
  }
  return true;
}

/* ─── Shared target job description ──────────────────────────────────── */
const JD_KEY = 'veyora.targetJobDescription';

function syncJd(value) {
  if (value && value.trim().length > 30) {
    localStorage.setItem(JD_KEY, value);
    $$('.jd-input').forEach((input) => {
      if (input !== document.activeElement && !input.value) input.value = value;
    });
  }
}

function savedJd() {
  return localStorage.getItem(JD_KEY) || '';
}

function jdFrom(selector) {
  const node = $(selector);
  const value = node ? node.value.trim() : '';
  if (value) syncJd(value);
  return value;
}

/* ─── Renderers ──────────────────────────────────────────────────────── */
function scoreCard(data) {
  return `
    <article class="result-card">
      <div class="score-hero">
        <div class="score-big">${data.score}<span>/100</span></div>
        <div class="score-caption"><strong>${esc(data.grade)}</strong>${esc(data.rationale || '')}</div>
      </div>
      <div class="breakdown">
        ${(data.breakdown || []).filter((part) => part.weight > 0).map((part) => `
          <div class="breakdown-row">
            <span>${esc(part.label)}</span>
            <span class="bar"><i style="width:${Math.round((part.ratio || 0) * 100)}%"></i></span>
            <span class="points">${part.earned}/${part.weight}</span>
          </div>
          <div class="breakdown-detail">${esc(part.detail)}</div>`).join('')}
      </div>
      <h4>Matching skills</h4>
      <div>${(data.matched_skills || []).map((s) => `<span class="tag tag-matched">${esc(s)}</span>`).join('') || '<p class="muted">None</p>'}</div>
      <h4>Partially matching</h4>
      <div>${(data.partial_skills || []).map((s) => `<span class="tag tag-partial">${esc(s)}</span>`).join('') || '<p class="muted">None</p>'}</div>
      <h4>Missing skills</h4>
      <div>${(data.missing_skills || []).map((s) => `<span class="tag tag-missing">${esc(s)}</span>`).join('') || '<p class="muted">None</p>'}</div>
      <h4>Why this score</h4>
      ${list(data.reasons || [])}
      <p class="hint">${esc(data.methodology || '')}</p>
    </article>`;
}

function gapCards(gaps) {
  if (!gaps || !gaps.length) return '<p class="muted">No gaps detected for this target.</p>';
  return gaps.map((gap) => `
    <div class="gap-card">
      <div class="gap-head"><strong>${esc(gap.skill)}</strong><span class="status ${gap.importance === 'High' ? 'status-rejected' : 'status-reserved'}">${esc(gap.importance)} priority</span></div>
      <p>${esc(gap.why)}</p>
      <div class="gap-meta"><b>Learning topics:</b> ${esc((gap.learn || []).join(' · '))}</div>
      <div class="gap-meta"><b>Suggested exercise:</b> ${esc(gap.practice_task || '')}</div>
      <div class="gap-meta"><b>Expected outcome:</b> you can explain ${esc(gap.skill)} and show one concrete project using it.</div>
    </div>`).join('');
}

function renderResumeReview(data) {
  const review = data.resume_review || {};
  const profile = data.profile || {};
  $('#resume-review-output').innerHTML = `
    <div class="grid-2">
      <article class="result-card">
        <h3>Resume snapshot</h3>
        <p class="muted">${esc(profile.name || '')} · ${esc(profile.total_years || 0)} years detected</p>
        <h4>Skills found</h4>
        <div>${(profile.technical_skills || []).map((s) => `<span class="tag tag-skill">${esc(s)}</span>`).join('') || '<p class="muted">None</p>'}</div>
        <h4>Education</h4>
        ${list((profile.education || []).map((e) => e.degree || JSON.stringify(e)), 'No education detected')}
        <h4>Projects</h4>
        ${list((profile.projects || []).map((p) => p.name || p.description), 'No projects detected')}
        <h4>Certifications</h4>
        ${list(profile.certifications || [], 'No certifications detected')}
      </article>
      <article class="result-card">
        <h3>What's working</h3>
        ${list(review.strengths || [])}
        <h3>Weak descriptions</h3>
        ${list(review.weaknesses || [])}
        <h3>Missing achievements</h3>
        ${list(review.missing_achievements || [])}
        <h3>Unnecessary content</h3>
        ${list(review.unnecessary || [])}
        <h3>Formatting</h3>
        ${list(review.formatting || [])}
        <h3>Keywords to add (only if true)</h3>
        ${list(review.keywords || [])}
        <p class="hint">Suggestions never invent experience. Only include what you have actually done.</p>
      </article>
    </div>`;
}

function renderPrep(data) {
  state.prepQuestions = [];
  const groups = data.categories || {};
  const job = data.job || {};
  const required = job.required_skills || [];
  const html = `
    <div class="banner">Preparing for <strong>${esc(job.title || 'your target role')}</strong>${required.length ? ` · required skills: ${esc(required.join(', '))}` : ''}. Answer each question, then get feedback. Questions are built from your resume, the target role and your gaps.</div>
    ${Object.entries(groups).map(([key, group]) => {
      if (!group || !group.questions || !group.questions.length) return '';
      return `
        <article class="result-card">
          <h3>${esc(group.label || key)}</h3>
          ${group.questions.map((question) => {
            const index = state.prepQuestions.push(question) - 1;
            return `
              <details class="question-card">
                <summary>${esc(question.question)}</summary>
                <div class="q-body">
                  <div><b>What's being tested:</b> ${esc(question.what_tested || question.purpose || '')}</div>
                  <div><b>Concepts to mention:</b> ${esc((question.expected_concepts || []).join(', '))}</div>
                  <div><b>Answer structure:</b> ${esc((question.answer_structure || []).join(' → '))}</div>
                  <div><b>Sample answer:</b> ${esc(question.sample_answer || '')}</div>
                  <div><b>Common mistakes:</b> ${esc((question.common_mistakes || []).join(' · '))}</div>
                  <div class="practice-box">
                    <label>Your answer <textarea data-answer="${index}" rows="4" placeholder="Write your answer here…"></textarea></label>
                    <button class="btn btn-sage btn-sm" type="button" data-feedback="${index}">Get AI feedback</button>
                    <div data-feedback-result="${index}"></div>
                  </div>
                </div>
              </details>`;
          }).join('')}
        </article>`;
    }).join('')}
    <article class="result-card">
      <h3>Critical areas an interviewer will probe</h3>
      ${(data.critical_areas || []).map((area) => `
        <div class="gap-card">
          <div class="gap-head"><strong>${esc(area.area)}</strong><span class="tag tag-partial">verify</span></div>
          <p>${esc(area.why || '')}</p>
          <div class="gap-meta"><b>How to prepare:</b> ${esc(area.verify_with || '')}</div>
        </div>`).join('') || '<p class="muted">No critical areas flagged.</p>'}
    </article>`;
  $('#prep-output').innerHTML = html;
}

async function requestFeedback(index) {
  const question = state.prepQuestions[index];
  const textarea = document.querySelector(`[data-answer="${index}"]`);
  const box = document.querySelector(`[data-feedback-result="${index}"]`);
  if (!question || !textarea || !box) return;
  box.innerHTML = '<p class="muted">Evaluating your answer…</p>';
  try {
    const data = await api('/api/candidate/practice', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, answer: textarea.value }),
    });
    const feedback = data.feedback;
    box.innerHTML = `
      <div class="feedback-card">
        <div class="feedback-score">${feedback.score}/100</div>
        <div><b>Strengths:</b> ${esc((feedback.strengths || []).join(' '))}</div>
        <div><b>Improve:</b> ${esc((feedback.improvements || []).join(' '))}</div>
        ${feedback.next_step ? `<div><b>Next step:</b> ${esc(feedback.next_step)}</div>` : ''}
      </div>`;
  } catch (error) {
    box.innerHTML = `<p class="form-notice">${esc(error.message)}</p>`;
  }
}

function renderRoadmap(data) {
  const plan = data.plan || {};
  const progress = data.progress || {};
  state.roadmap = { id: data.roadmap_id, plan, progress };
  $('#roadmap-output').innerHTML = `
    <div class="banner">${esc(plan.summary || '')}</div>
    <div class="slot-meta"><span>Target: ${esc(plan.target_role || '')}</span><span>${esc(plan.total_weeks || 0)} weeks</span><span>${esc(plan.source === 'gemini' ? 'AI generated' : 'Local plan')}</span></div>
    <div class="stack">
      ${(plan.weeks || []).map((week) => `
        <div class="roadmap-week ${progress[String(week.week)] ? 'done' : ''}">
          <div class="roadmap-head">
            <strong>Week ${esc(week.week)} · ${esc(week.focus)}</strong>
            <label class="week-check"><input type="checkbox" data-week="${esc(week.week)}" ${progress[String(week.week)] ? 'checked' : ''}> completed</label>
          </div>
          <div><b>Goals:</b> ${esc((week.goals || []).join(' · '))}</div>
          <div><b>Exercises:</b> ${esc((week.exercises || []).join(' · '))}</div>
          <div><b>Project:</b> ${esc(week.project || '')}</div>
          <div><b>Milestone:</b> ${esc(week.milestone || '')}</div>
        </div>`).join('')}
    </div>
    <h4>Overall milestones</h4>
    ${list(plan.milestones || [])}`;
}

/* ─── Actions ────────────────────────────────────────────────────────── */
async function runResumeReview() {
  if (!ensureResume()) return;
  const button = $('#btn-resume-review');
  button.disabled = true;
  $('#resume-review-output').innerHTML = '<p class="muted">Reviewing your resume…</p>';
  try {
    const data = await api('/api/candidate/resume-review', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ resume_id: state.resume.id, job_description: jdFrom('#review-jd') }),
    });
    renderResumeReview(data);
  } catch (error) {
    $('#resume-review-output').innerHTML = `<p class="form-notice">${esc(error.message)}</p>`;
  } finally {
    button.disabled = false;
  }
}

async function runCompatibility() {
  if (!ensureResume()) return;
  const jd = jdFrom('#compat-jd');
  if (jd.length < 30) {
    $('#compat-output').innerHTML = '<p class="form-notice">Paste a job description (30+ characters).</p>';
    return;
  }
  const button = $('#btn-compat');
  button.disabled = true;
  $('#compat-output').innerHTML = '<p class="muted">Calculating compatibility…</p>';
  try {
    const data = await api('/api/candidate/compatibility', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        resume_id: state.resume.id, job_description: jd,
        job_title: $('#compat-title').value.trim(),
      }),
    });
    $('#compat-output').innerHTML = `
      <div class="grid-2">
        ${scoreCard(data)}
        <article class="result-card">
          <h3>Priority gaps</h3>
          ${gapCards((data.gaps || []).slice(0, 4))}
        </article>
      </div>`;
  } catch (error) {
    $('#compat-output').innerHTML = `<p class="form-notice">${esc(error.message)}</p>`;
  } finally {
    button.disabled = false;
  }
}

async function runGaps() {
  if (!ensureResume()) return;
  const jd = jdFrom('#gaps-jd');
  const button = $('#btn-gaps');
  button.disabled = true;
  $('#gaps-output').innerHTML = '<p class="muted">Analysing your skill gaps…</p>';
  try {
    const data = await api('/api/candidate/skill-gaps', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        resume_id: state.resume.id,
        job_id: $('#gaps-job') ? $('#gaps-job').value : '',
        job_description: jd,
      }),
    });
    const job = data.job || {};
    const required = job.required_skills || [];
    const preferred = job.preferred_skills || [];
    $('#gaps-output').innerHTML = `
      <article class="result-card">
        <h3>Skills ${esc(job.title || 'this role')} requires</h3>
        <p class="muted">Required (must-have)</p>
        <div>${required.map((skill) => `<span class="tag tag-missing">${esc(skill)}</span>`).join('') || '<p class="muted">No mandatory skills listed.</p>'}</div>
        <p class="muted">Preferred (bonus)</p>
        <div>${preferred.map((skill) => `<span class="tag tag-partial">${esc(skill)}</span>`).join('') || '<p class="muted">None listed.</p>'}</div>
      </article>
      <div class="banner">Current compatibility: <strong>${data.score}/100</strong> (${esc(data.grade)}). Gaps are ordered by importance for this role.</div>
      <h3>What to close first</h3>
      <div class="stack">${gapCards(data.gaps)}</div>`;
  } catch (error) {
    $('#gaps-output').innerHTML = `<p class="form-notice">${esc(error.message)}</p>`;
  } finally {
    button.disabled = false;
  }
}

async function runPrep() {
  if (!ensureResume()) return;
  const button = $('#btn-prep');
  button.disabled = true;
  $('#prep-output').innerHTML = '<p class="muted">Building your interview plan…</p>';
  try {
    const data = await api('/api/candidate/interview-prep', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        resume_id: state.resume.id,
        job_id: $('#prep-job') ? $('#prep-job').value : '',
        job_description: jdFrom('#prep-jd'),
      }),
    });
    renderPrep(data);
  } catch (error) {
    $('#prep-output').innerHTML = `<p class="form-notice">${esc(error.message)}</p>`;
  } finally {
    button.disabled = false;
  }
}

async function runRoadmap() {
  if (!ensureResume()) return;
  const button = $('#btn-roadmap');
  button.disabled = true;
  $('#roadmap-output').innerHTML = '<p class="muted">Building your roadmap…</p>';
  try {
    const data = await api('/api/candidate/roadmap', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ resume_id: state.resume.id, job_description: jdFrom('#roadmap-jd') }),
    });
    renderRoadmap({ roadmap_id: data.roadmap_id, plan: data.plan, progress: data.progress });
  } catch (error) {
    $('#roadmap-output').innerHTML = `<p class="form-notice">${esc(error.message)}</p>`;
  } finally {
    button.disabled = false;
  }
}

async function loadRoadmap() {
  if (state.roadmap) return;
  try {
    const data = await api('/api/candidate/roadmap');
    if (data.roadmap) {
      renderRoadmap({ roadmap_id: data.roadmap.id, plan: data.roadmap.plan, progress: data.roadmap.progress });
    }
  } catch (error) { /* no roadmap yet */ }
}

async function loadSettings() {
  const node = $('#cand-settings');
  try {
    const data = await api('/api/candidate/settings');
    node.innerHTML = `
      <div class="banner ${data.ai.configured ? '' : 'banner-warn'}">${esc(data.ai.message)}</div>
      <div class="profile-grid">
        <div class="profile-block"><h4>AI agent</h4><p>${data.ai.configured ? 'Gemini connected' : 'Local deterministic mode'}</p><p class="muted">Model: ${esc(data.ai.model || 'n/a')}</p></div>
        <div class="profile-block"><h4>Stored data</h4><p>${data.counts.resumes} resume(s)</p><p>${data.counts.reports} analyses</p></div>
      </div>
      <div class="profile-block"><h4>Your account</h4><p>Role: candidate</p><p class="muted">Resume text and analyses are stored only in this application's local SQLite database.</p></div>`;
  } catch (error) {
    node.innerHTML = `<p class="muted">${esc(error.message)}</p>`;
  }
}

async function loadOpenRoles() {
  const node = $('#open-roles');
  try {
    const data = await api('/api/candidate/jobs');
    state.jobs = data.jobs || [];
    const count = $('#open-roles-count');
    if (count) count.textContent = `${state.jobs.length}`;

    const options = '<option value="">— Paste a job description below —</option>' + state.jobs.map(
      (job) => `<option value="${esc(job.id)}">${esc(job.title || 'Untitled role')}</option>`
    ).join('');
    ['#gaps-job', '#prep-job'].forEach((selector) => {
      const select = $(selector);
      if (select) select.innerHTML = options;
    });

    if (!node) return;
    node.innerHTML = state.jobs.length
      ? state.jobs.map((job) => `
        <div class="slot-card">
          <div class="slot-head">
            <strong>${esc(job.title || 'Untitled role')}</strong>
            <span class="pill pill-muted">${esc(job.seniority || 'Open role')}</span>
          </div>
          <div class="slot-meta"><span>${job.min_years ? `${esc(job.min_years)}+ yrs` : 'Any experience'}</span><span>${(job.required_skills || []).length} required skills</span></div>
          <div>${(job.required_skills || []).map((skill) => `<span class="tag tag-missing">${esc(skill)}</span>`).join('') || '<span class="muted">No mandatory skills listed</span>'}</div>
          ${(job.preferred_skills || []).length ? `<div class="slot-meta"><span>Preferred: ${esc(job.preferred_skills.join(', '))}</span></div>` : ''}
          <div class="slot-actions">
            <button class="btn btn-ghost btn-sm" data-role-gaps="${esc(job.id)}">Required skills &amp; gaps</button>
            <button class="btn btn-ghost btn-sm" data-role-prep="${esc(job.id)}">Interview questions</button>
          </div>
        </div>`).join('')
      : '<p class="muted">No open roles yet. Paste any job description in Skill Gap Analysis to check it.</p>';
  } catch (error) {
    if (node) node.innerHTML = `<p class="muted">${esc(error.message)}</p>`;
  }
}

/* ─── Init ───────────────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
  $$('.nav-link').forEach((link) => link.addEventListener('click', () => setView(link.dataset.view)));
  $$('[data-goto]').forEach((button) => button.addEventListener('click', () => setView(button.dataset.goto)));
  $('#menu-toggle').addEventListener('click', () => $('#sidebar').classList.toggle('open'));

  initUpload();
  $('#btn-resume-review').addEventListener('click', runResumeReview);
  $('#btn-compat').addEventListener('click', runCompatibility);
  $('#btn-gaps').addEventListener('click', runGaps);
  $('#btn-prep').addEventListener('click', runPrep);
  $('#btn-roadmap').addEventListener('click', runRoadmap);

  const roles = $('#open-roles');
  if (roles) {
    roles.addEventListener('click', (event) => {
      const gapsButton = event.target.closest('[data-role-gaps]');
      if (gapsButton) {
        setView('gaps');
        $('#gaps-job').value = gapsButton.dataset.roleGaps;
        runGaps();
        return;
      }
      const prepButton = event.target.closest('[data-role-prep]');
      if (prepButton) {
        setView('prep');
        $('#prep-job').value = prepButton.dataset.rolePrep;
        runPrep();
      }
    });
  }
  loadOpenRoles();

  const stored = savedJd();
  if (stored) $$('.jd-input').forEach((input) => { if (!input.value) input.value = stored; });

  $$('.jd-input').forEach((input) => {
    input.addEventListener('change', () => syncJd(input.value));
    input.addEventListener('blur', () => syncJd(input.value));
  });

  $('#prep-output').addEventListener('click', (event) => {
    const button = event.target.closest('[data-feedback]');
    if (!button) return;
    requestFeedback(Number(button.dataset.feedback));
  });

  $('#roadmap-output').addEventListener('change', async (event) => {
    const checkbox = event.target.closest('[data-week]');
    if (!checkbox) return;
    try {
      const data = await api('/api/candidate/roadmap/progress', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ week: checkbox.dataset.week, completed: checkbox.checked }),
      });
      if (state.roadmap) state.roadmap.progress = data.progress;
      checkbox.closest('.roadmap-week').classList.toggle('done', checkbox.checked);
      toast(checkbox.checked ? 'Week marked complete.' : 'Week reopened.');
    } catch (error) {
      toast(error.message);
      checkbox.checked = !checkbox.checked;
    }
  });

  loadDashboard();
});
