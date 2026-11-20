/* Veyora AI — Recruiter workspace */
'use strict';

const state = {
  view: 'dashboard',
  jobs: [],
  files: [],
  review: null,
  stages: [],
  activeCandidate: 0,
  activeSlot: null,
  allCandidates: [],
  stream: null,
};

const VIEW_META = {
  dashboard: ['Dashboard', 'Review resumes, understand role fit and plan next steps.'],
  jobs: ['Jobs', 'Create openings and keep their requirements structured.'],
  review: ['Candidate Review', 'Job description, resumes, then one real backend review.'],
  comparison: ['Candidate Comparison', 'Compare candidates for the same role side by side.'],
  planning: ['Interview Planning', 'Question plans and claims to verify for each candidate.'],
  interviews: ['Interview Management', 'Slots, double-booking protection and invitation emails.'],
  settings: ['Settings', 'AI, email and environment configuration.'],
};

const STAGE_PROGRESS = {
  'Validate inputs': 8,
  'Analyse job description': 18,
  'Parse resumes': 38,
  'Score compatibility': 58,
  'Analyse skill gaps': 74,
  'Generate interview questions': 82,
  'AI screening decision': 90,
  'Schedule interviews': 94,
  'Save results': 98,
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
    const message = data.error || `Request failed (${response.status})`;
    const err = new Error(data.details && data.details.length ? `${message} ${data.details.join(' ')}` : message);
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
  return `<ul>${items.map((item) => `<li>${esc(typeof item === 'string' ? item : JSON.stringify(item))}</li>`).join('')}</ul>`;
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
  if (name === 'dashboard') loadDashboard();
  if (name === 'jobs') loadJobs();
  if (name === 'settings') loadSettings();
  if (name === 'interviews') loadSlots();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

/* ─── Dashboard / settings ───────────────────────────────────────────── */
async function loadDashboard() {
  try {
    const data = await api('/api/dashboard');
    $('#stat-jobs').textContent = data.counts.jobs;
    $('#stat-applications').textContent = data.counts.applications;
    $('#stat-shortlisted').textContent = data.counts.shortlisted;
    $('#stat-interviews').textContent = data.counts.interviews;

    setPills(data.ai, data.email_configured);

    const jobsNode = $('#dash-jobs');
    jobsNode.innerHTML = data.jobs.length
      ? data.jobs.map((job) => `
        <div class="slot-card">
          <div class="slot-head">
            <strong>${esc(job.title || 'Untitled role')}</strong>
            <button class="btn btn-ghost btn-sm" data-review-job="${esc(job.id)}">Review</button>
          </div>
          <div class="slot-meta"><span>${(job.required_skills || []).length} required skills</span><span>${job.min_years ? job.min_years + '+ yrs' : 'No min. years'}</span></div>
        </div>`).join('')
      : '<p class="muted">No jobs yet.</p>';

    const slotsNode = $('#dash-interviews');
    slotsNode.innerHTML = data.upcoming_interviews.length
      ? data.upcoming_interviews.map((slot) => `
        <div class="slot-card">
          <div class="slot-head">
            <span class="slot-when">${esc(slot.date)} · ${esc(slot.start_time)}–${esc(slot.end_time)}</span>
            <span class="status status-${esc(slot.status)}">${esc(slot.status)}</span>
          </div>
          <div class="slot-meta"><span>${esc(slot.candidate_name || 'Unassigned')}</span><span>${esc(slot.mode)}</span></div>
        </div>`).join('')
      : '<p class="muted">No interviews scheduled.</p>';

    const decisionsNode = $('#dash-decisions');
    if (decisionsNode) {
      const decisions = data.recent_decisions || [];
      decisionsNode.innerHTML = decisions.length
        ? decisions.map((row) => `
          <div class="slot-card">
            <div class="slot-head">
              <strong>${esc(row.name)}</strong>
              <span class="status status-${esc(row.decision)}">${esc(String(row.decision || '').replace('_', ' '))}</span>
            </div>
            <div class="slot-meta"><span>${esc(row.score)}/100</span><span>${esc(row.job_title || '')}</span><span>threshold ${esc(row.threshold ?? data.screening_threshold ?? 60)}</span></div>
            ${row.reason ? `<p class="muted">${esc(row.reason)}</p>` : ''}
          </div>`).join('')
        : '<p class="muted">No screening decisions yet. Run a review and the agent decides for each candidate.</p>';
    }
    const thresholdPill = $('#screening-threshold');
    if (thresholdPill) thresholdPill.textContent = `${data.screening_threshold ?? 60}/100 rule`;
  } catch (error) {
    toast(error.message);
  }
}

function setPills(ai, emailConfigured) {
  if (ai) {
    const pill = $('#ai-pill');
    pill.textContent = ai.configured ? `AI: ${ai.model}` : 'AI: local mode';
    pill.className = ai.configured ? 'pill' : 'pill pill-warn';
    const banner = $('#ai-banner');
    if (banner) {
      banner.textContent = ai.message;
      banner.className = ai.configured ? 'banner' : 'banner banner-warn';
    }
  }
  const emailPill = $('#email-pill');
  if (emailPill) {
    emailPill.textContent = emailConfigured ? 'Email: SMTP ready' : 'Email: demo mode';
    emailPill.className = emailConfigured ? 'pill' : 'pill pill-warn';
  }
}

async function loadSettings() {
  const node = $('#settings-body');
  node.innerHTML = '<p class="muted">Loading settings…</p>';
  try {
    const data = await api('/api/settings');
    setPills(data.ai, data.email_configured);
    node.innerHTML = `
      <div class="banner ${data.ai.configured ? '' : 'banner-warn'}">${esc(data.ai.message)}</div>
      <div class="profile-grid">
        <div class="profile-block"><h4>AI agent</h4><p>${data.ai.configured ? 'Gemini connected' : 'Deterministic local analysis'}</p><p class="muted">Model: ${esc(data.ai.model || 'n/a')}</p></div>
        <div class="profile-block"><h4>Email delivery</h4><p>${data.email_configured ? 'SMTP configured' : 'Demo mode (nothing is sent)'}</p><p class="muted">Invitations are logged either way.</p></div>
      </div>
      <table class="settings-table">
        <thead><tr><th>Variable</th><th>Purpose</th></tr></thead>
        <tbody>${data.required_env.map((item) => `<tr><td><code>${esc(item.name)}</code></td><td>${esc(item.purpose)}</td></tr>`).join('')}</tbody>
      </table>
      <div class="profile-grid">
        <div class="profile-block"><h4>Jobs</h4><p>${data.counts.jobs}</p></div>
        <div class="profile-block"><h4>Resumes stored</h4><p>${data.counts.resumes}</p></div>
        <div class="profile-block"><h4>Candidate records</h4><p>${data.counts.applications}</p></div>
        <div class="profile-block"><h4>Emails logged</h4><p>${data.counts.emails}</p></div>
      </div>`;
  } catch (error) {
    node.innerHTML = `<p class="muted">${esc(error.message)}</p>`;
  }
}

/* ─── Jobs ───────────────────────────────────────────────────────────── */
async function loadJobs() {
  try {
    const data = await api('/api/jobs');
    state.jobs = data.jobs;
    $('#jobs-count').textContent = `${data.jobs.length}`;
    renderJobs();
    populateJobSelects();
  } catch (error) {
    toast(error.message);
  }
}

function renderJobs() {
  const node = $('#jobs-list');
  node.innerHTML = state.jobs.length
    ? state.jobs.map((job) => `
      <div class="slot-card">
        <div class="slot-head">
          <strong>${esc(job.title || 'Untitled role')}</strong>
          <span class="pill pill-muted">${job.application_count || 0} reviewed</span>
        </div>
        <div class="slot-meta">
          <span>${(job.required_skills || []).slice(0, 4).map(esc).join(', ') || 'No required skills detected'}</span>
        </div>
        <div class="slot-actions">
          <button class="btn btn-primary btn-sm" data-review-job="${esc(job.id)}">Review resumes</button>
          <button class="btn btn-ghost btn-sm" data-edit-job="${esc(job.id)}">Load into form</button>
          <button class="btn btn-danger btn-sm" data-delete-job="${esc(job.id)}">Delete</button>
        </div>
      </div>`).join('')
    : '<p class="muted">No jobs yet. Create one to get started.</p>';
}

function populateJobSelects() {
  const options = '<option value="">Select a job…</option>' + state.jobs.map(
    (job) => `<option value="${esc(job.id)}">${esc(job.title || 'Untitled role')}</option>`
  ).join('');
  ['#comparison-job-select', '#planning-job-select'].forEach((selector) => {
    const node = $(selector);
    if (node) node.innerHTML = options;
  });
  const reviewSelect = $('#review-job-select');
  if (reviewSelect) {
    reviewSelect.innerHTML = '<option value="">— New job / enter below —</option>' + state.jobs.map(
      (job) => `<option value="${esc(job.id)}">${esc(job.title || 'Untitled role')}</option>`
    ).join('');
  }
}

async function submitJob(event) {
  event.preventDefault();
  const errorNode = $('#job-error');
  errorNode.textContent = '';
  try {
    await api('/api/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title: $('#job-title').value.trim(),
        description: $('#job-description').value.trim(),
        required_skills: $('#job-required').value,
        preferred_skills: $('#job-preferred').value,
        min_years: $('#job-min-years').value,
        seniority: $('#job-seniority').value.trim(),
      }),
    });
    $('#job-form').reset();
    toast('Job saved.');
    loadJobs();
  } catch (error) {
    errorNode.textContent = error.message;
  }
}

/* ─── Review: files ──────────────────────────────────────────────────── */
function initDropZone() {
  const zone = $('#drop-zone');
  const input = $('#resume-files');
  if (!zone || !input) return;

  zone.addEventListener('dragover', (event) => {
    event.preventDefault();
    zone.classList.add('dragging');
  });
  zone.addEventListener('dragleave', () => zone.classList.remove('dragging'));
  zone.addEventListener('drop', (event) => {
    event.preventDefault();
    zone.classList.remove('dragging');
    if (event.dataTransfer && event.dataTransfer.files) addFiles(Array.from(event.dataTransfer.files));
  });
  input.addEventListener('change', () => {
    addFiles(Array.from(input.files || []));
    input.value = '';
  });
}

function addFiles(files) {
  const notice = $('#file-notice');
  const rejected = [];
  files.forEach((file) => {
    const extension = (file.name.split('.').pop() || '').toLowerCase();
    if (!['pdf', 'docx', 'txt'].includes(extension)) {
      rejected.push(`${file.name}: only PDF, DOCX or TXT files are supported`);
      return;
    }
    if (file.size > 20 * 1024 * 1024) {
      rejected.push(`${file.name}: larger than 20 MB`);
      return;
    }
    if (state.files.some((existing) => existing.name === file.name && existing.size === file.size)) return;
    if (state.files.length >= 20) {
      rejected.push('No more than 20 resumes can be uploaded at once');
      return;
    }
    state.files.push(file);
  });
  notice.textContent = rejected.join('. ');
  renderFiles();
}

function renderFiles() {
  const node = $('#file-list');
  node.innerHTML = state.files.map((file, index) => `
    <div class="file-item">
      <span>📄</span>
      <span class="file-item-name">${esc(file.name)}</span>
      <span class="file-item-size">${(file.size / 1024).toFixed(0)} KB</span>
      <span class="status status-confirmed">ready</span>
      <button type="button" class="file-item-remove" data-remove-file="${index}" aria-label="Remove">✕</button>
    </div>`).join('');
}

/* ─── Review: job description ────────────────────────────────────────── */
function applyJobToForm(jobId) {
  const job = state.jobs.find((item) => item.id === jobId);
  if (!job) return;
  $('#review-title').value = job.title || '';
  $('#review-description').value = job.description || '';
  $('#review-required').value = (job.required_skills || []).join(', ');
  $('#review-preferred').value = (job.preferred_skills || []).join(', ');
  $('#review-min-years').value = job.min_years || '';
}

async function analyzeDescription() {
  const node = $('#jd-analysis');
  const button = $('#btn-analyze-jd');
  const description = $('#review-description').value.trim();
  node.hidden = false;
  if (description.length < 30) {
    node.innerHTML = '<p class="muted">Add at least 30 characters of job description first.</p>';
    return;
  }
  button.disabled = true;
  button.textContent = 'Analysing…';
  try {
    const data = await api('/api/jobs/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ description, title: $('#review-title').value.trim() }),
    });
    const job = data.job;
    if (!$('#review-required').value) $('#review-required').value = (job.required_skills || []).join(', ');
    if (!$('#review-preferred').value) $('#review-preferred').value = (job.preferred_skills || []).join(', ');
    if (!$('#review-title').value) $('#review-title').value = job.title || '';
    if (!$('#review-min-years').value && job.min_years) $('#review-min-years').value = job.min_years;
    node.innerHTML = `
      <h4>Extracted requirements <span class="pill pill-muted">${esc(data.ai_mode)}</span></h4>
      <div class="analysis-list">
        <div><strong>Required:</strong> ${esc((job.required_skills || []).join(', ') || 'none detected')}</div>
        <div><strong>Preferred:</strong> ${esc((job.preferred_skills || []).join(', ') || 'none detected')}</div>
        <div><strong>Minimum experience:</strong> ${job.min_years ? esc(job.min_years) + ' years' : 'not stated'}</div>
        <div><strong>Competencies:</strong> ${esc((job.competencies || []).join(', ') || 'none detected')}</div>
      </div>`;
  } catch (error) {
    node.innerHTML = `<p class="muted">${esc(error.message)}</p>`;
  } finally {
    button.disabled = false;
    button.textContent = 'Analyse job description';
  }
}

async function loadSampleData() {
  const button = $('#btn-load-sample');
  const notice = $('#file-notice');
  if (button) button.disabled = true;
  if (notice) notice.textContent = '';
  try {
    const sample = await api('/api/sample-data');
    const resumes = await Promise.all(sample.resumes.map(async (filename) => {
      const response = await fetch(`/api/sample-resumes/${encodeURIComponent(filename)}`);
      if (!response.ok) throw new Error(`Could not load ${filename}`);
      return new File([await response.blob()], filename, { type: 'application/pdf' });
    }));
    $('#review-description').value = sample.job_description;
    $('#review-title').value = 'Senior Python Developer';
    state.files = [];
    addFiles(resumes);
    if (notice) notice.textContent = `${resumes.length} demo resumes ready. Press REVIEW RESUME.`;
  } catch (error) {
    if (notice) notice.textContent = error.message;
  } finally {
    if (button) button.disabled = false;
  }
}

/* ─── Review: execution ──────────────────────────────────────────────── */
async function startReview() {
  const errorNode = $('#review-error');
  errorNode.textContent = '';
  const description = $('#review-description').value.trim();
  const jobId = $('#review-job-select').value;

  if (!jobId && description.length < 30) {
    errorNode.textContent = 'Add a job description (30+ characters) or select a saved job.';
    return;
  }
  if (!state.files.length) {
    errorNode.textContent = 'Upload at least one resume (PDF, DOCX or TXT).';
    return;
  }

  const button = $('#btn-review');
  button.disabled = true;
  button.innerHTML = '<span class="btn-ico">◌</span> RUNNING REVIEW…';
  $('#review-progress').hidden = false;
  $('#review-results').hidden = true;
  state.stages = [];
  renderStages();
  setProgress(4);

  const body = new FormData();
  body.append('job_id', jobId);
  body.append('job_title', $('#review-title').value.trim());
  body.append('job_description', description);
  body.append('required_skills', $('#review-required').value);
  body.append('preferred_skills', $('#review-preferred').value);
  body.append('min_years', $('#review-min-years').value);
  state.files.forEach((file) => body.append('resumes', file, file.name));

  try {
    const started = await api('/api/reviews/start', { method: 'POST', body });
    state.review = { id: started.review_id };
    subscribe(started.review_id);
  } catch (error) {
    errorNode.textContent = error.message;
    finishReviewButton();
  }
}

function finishReviewButton() {
  const button = $('#btn-review');
  button.disabled = false;
  button.innerHTML = '<span class="btn-ico">▶</span> REVIEW RESUME';
}

function subscribe(reviewId) {
  if (state.stream) state.stream.close();
  const source = new EventSource(`/api/reviews/${reviewId}/stream`);
  state.stream = source;

  source.onmessage = (event) => {
    let data;
    try { data = JSON.parse(event.data); } catch (error) { return; }
    if (data.type === 'stage') {
      recordStage(data);
    } else if (data.type === 'complete') {
      source.close();
      state.stream = null;
      onReviewComplete(data.result);
    } else if (data.type === 'error') {
      source.close();
      state.stream = null;
      onReviewFailed(data.error);
    }
  };
  source.onerror = () => {
    source.close();
    state.stream = null;
    pollReview(reviewId);
  };
}

async function pollReview(reviewId) {
  let attempts = 0;
  while (attempts < 150) {
    attempts += 1;
    await new Promise((resolve) => setTimeout(resolve, 1800));
    try {
      const data = await api(`/api/reviews/${reviewId}`);
      (data.stages || []).forEach(recordStage);
      if (data.status === 'complete') { onReviewComplete(data.result); return; }
      if (data.status === 'error') { onReviewFailed(data.error); return; }
    } catch (error) {
      if (error.status === 404) { onReviewFailed(error.message); return; }
    }
  }
  onReviewFailed('The review timed out. Please retry.');
}

function recordStage(stage) {
  const key = `${stage.name}`;
  let entry = state.stages.find((item) => item.key === key);
  if (!entry) {
    entry = { key, name: stage.name, status: stage.status, message: stage.message, notes: [] };
    state.stages.push(entry);
  } else {
    if (stage.status === 'progress') {
      if (stage.message && !entry.notes.includes(stage.message)) entry.notes.push(stage.message);
    } else {
      entry.status = stage.status;
      entry.message = stage.message || entry.message;
    }
    if (stage.status === 'progress') entry.status = entry.status === 'complete' ? 'complete' : 'running';
  }
  if (stage.status === 'progress' && stage.message && !entry.notes.includes(stage.message)) {
    entry.notes.push(stage.message);
  }
  renderStages();
}

function renderStages() {
  const icons = { complete: '✓', running: '◌', error: '✕', warning: '!' };
  $('#review-stages').innerHTML = state.stages.map((stage) => `
    <div class="stage-item ${esc(stage.status)}">
      <span class="stage-icon">${icons[stage.status] || '·'}</span>
      <span>
        <span class="stage-name">${esc(stage.name)}</span>
        <div class="stage-msg">${esc(stage.message || '')}</div>
        ${stage.notes && stage.notes.length ? `<div class="stage-msg">${stage.notes.slice(-6).map(esc).join('<br>')}</div>` : ''}
      </span>
    </div>`).join('');

  let percent = 4;
  state.stages.forEach((stage) => {
    if (stage.status === 'complete' && STAGE_PROGRESS[stage.name]) percent = Math.max(percent, STAGE_PROGRESS[stage.name]);
    else if (stage.status === 'running' && STAGE_PROGRESS[stage.name]) percent = Math.max(percent, STAGE_PROGRESS[stage.name] - 6);
  });
  setProgress(percent);
}

function setProgress(percent) {
  const fill = $('#progress-fill');
  if (fill) fill.style.width = `${Math.min(100, percent)}%`;
}

function onReviewFailed(message) {
  finishReviewButton();
  const errorNode = $('#review-error');
  errorNode.textContent = message || 'The review could not be completed.';
  const retry = document.createElement('button');
  retry.className = 'btn btn-ghost btn-sm';
  retry.type = 'button';
  retry.textContent = 'Retry review';
  retry.addEventListener('click', retryReview);
  errorNode.appendChild(document.createElement('br'));
  errorNode.appendChild(retry);
}

async function retryReview() {
  if (!state.review || !state.review.id) return startReview();
  try {
    await api(`/api/reviews/${state.review.id}/retry`, { method: 'POST' });
    state.stages = [];
    $('#review-error').textContent = '';
    $('#review-progress').hidden = false;
    renderStages();
    subscribe(state.review.id);
  } catch (error) {
    toast(error.message);
  }
}

function onReviewComplete(result) {
  finishReviewButton();
  setProgress(100);
  state.review = { id: result.review_id, result };
  state.activeCandidate = 0;
  $('#review-results').hidden = false;
  $('#results-title').textContent = result.job.title || 'Candidate results';
  const screening = result.screening || {};
  $('#results-meta').textContent =
    `${result.candidates.length} candidate(s) · ${screening.shortlisted ?? 0} shortlisted · `
    + `${screening.rejected ?? 0} rejected by the ${screening.threshold ?? 60}/100 rule`;
  renderCandidateTabs();
  renderCandidatePanel();
  $('#review-results').scrollIntoView({ behavior: 'smooth', block: 'start' });
  toast('Review complete.');
  state.files = [];
  renderFiles();
  loadJobs();
}

/* ─── Results rendering ──────────────────────────────────────────────── */
function renderCandidateTabs() {
  const candidates = state.review.result.candidates;
  $('#candidate-tabs').innerHTML = candidates.map((candidate, index) => `
    <button class="candidate-tab ${index === state.activeCandidate ? 'active' : ''}" data-candidate="${index}" role="tab">
      <span class="tab-score">${candidate.score}/100</span>
      <strong>${esc(candidate.name)}</strong>
      <small>Rank #${candidate.rank} · ${esc(candidate.status.replace('_', ' '))}</small>
    </button>`).join('');
}

function renderCandidatePanel() {
  const candidates = state.review.result.candidates;
  const candidate = candidates[state.activeCandidate];
  if (!candidate) return;
  const profile = candidate.profile || {};
  const compatibility = candidate.compatibility || {};
  const screening = candidate.screening || {};
  const questions = candidate.questions || {};
  const scheduled = (state.review.result.scheduled_interviews || []).filter(
    (slot) => slot.application_id === candidate.application_id
  );

  $('#candidate-panels').innerHTML = `
    <div class="panel">
      <div class="panel-head">
        <div>
          <span class="eyebrow">Rank #${candidate.rank} · ${esc(candidate.grade || '')}</span>
          <h2>${esc(candidate.name)}</h2>
          <div class="contact">${esc(candidate.email || 'No email in resume')} · ${esc(candidate.phone || 'No phone')} · ${esc(candidate.filename)}</div>
        </div>
        <div class="shortlist-bar" data-app-id="${esc(candidate.application_id)}">
          <button class="btn btn-ghost btn-sm" data-app-status="under_review">Under review</button>
          <button class="btn btn-sage btn-sm" data-app-status="shortlisted">Shortlist</button>
          <button class="btn btn-ghost btn-sm" data-app-status="interview">Select for interview</button>
          <button class="btn btn-danger btn-sm" data-app-status="rejected">Reject</button>
        </div>
      </div>

      <div class="result-grid">
        <article class="result-card">
          <span class="section-tag">Section A</span>
          <h3>Resume analysis</h3>
          <div class="profile-grid">
            <div class="profile-block"><h4>Contact</h4><p>${esc(profile.email || '—')}</p><p>${esc(profile.phone || '—')}</p></div>
            <div class="profile-block"><h4>Experience</h4><p>${esc(profile.total_years || 0)} years detected</p></div>
          </div>
          <h4>Technical skills</h4>
          <div>${(profile.technical_skills || []).map((skill) => `<span class="tag tag-skill">${esc(skill)}</span>`).join('') || '<p class="muted">None detected</p>'}</div>
          <h4>Soft skills</h4>
          <div>${(profile.soft_skills || []).map((skill) => `<span class="tag">${esc(skill)}</span>`).join('') || '<p class="muted">None detected</p>'}</div>
          <h4>Education</h4>
          ${list((profile.education || []).map((item) => [item.degree, item.institution, item.year].filter(Boolean).join(' · ')), 'No education found')}
          <h4>Experience</h4>
          ${list((profile.experience || []).map((item) => [item.title, item.company, item.duration].filter(Boolean).join(' · ')), 'No work experience found')}
          <h4>Internships</h4>
          ${list((profile.internships || []).map((item) => item.title || item.description || JSON.stringify(item)), 'No internships found')}
          <h4>Projects</h4>
          ${list((profile.projects || []).map((item) => [item.name, item.description].filter(Boolean).join(' — ')), 'No projects found')}
          <h4>Certifications</h4>
          ${list(profile.certifications || [], 'No certifications found')}
          <h4>Achievements</h4>
          ${list(profile.achievements || [], 'No achievements found')}
          <p class="hint">Only information found in the resume is shown. Nothing is invented.</p>
        </article>

        <article class="result-card">
          <span class="section-tag">Section B</span>
          <h3>Job compatibility</h3>
          <div class="score-hero">
            <div class="score-big">${candidate.score}<span>/100</span></div>
            <div class="score-caption"><strong>${esc(candidate.grade || '')}</strong>${esc(compatibility.rationale || '')}</div>
          </div>
          <div class="breakdown">
            ${(compatibility.breakdown || []).filter((part) => part.weight > 0).map((part) => `
              <div class="breakdown-row">
                <span>${esc(part.label)}</span>
                <span class="bar"><i style="width:${Math.round((part.ratio || 0) * 100)}%"></i></span>
                <span class="points">${part.earned}/${part.weight}</span>
              </div>
              <div class="breakdown-detail">${esc(part.detail)}</div>`).join('')}
          </div>
          <h4>Matching skills</h4>
          <div>${(compatibility.matched_skills || []).map((skill) => `<span class="tag tag-matched">${esc(skill)}</span>`).join('') || '<p class="muted">None</p>'}</div>
          <h4>Partially matching</h4>
          <div>${(compatibility.partial_skills || []).map((skill) => `<span class="tag tag-partial">${esc(skill)}</span>`).join('') || '<p class="muted">None</p>'}</div>
          <h4>Missing skills</h4>
          <div>${(compatibility.missing_skills || []).map((skill) => `<span class="tag tag-missing">${esc(skill)}</span>`).join('') || '<p class="muted">None</p>'}</div>
          <h4>Evidence from the resume</h4>
          <div class="evidence-list">
            ${(compatibility.skill_evidence || []).map((item) => `
              <div class="evidence-item"><span><strong>${esc(item.skill)}</strong> — ${esc(item.evidence)}</span><span>${esc(item.level)}</span></div>`).join('') || '<p class="muted">No evidence recorded</p>'}
          </div>
          <h4>Why this score</h4>
          ${list(compatibility.reasons || [])}
          <p class="hint">${esc(compatibility.methodology || '')}</p>
        </article>
      </div>

      <div class="result-grid">
        <article class="result-card">
          <span class="section-tag lav">Section C · AI</span>
          <h3>Screening decision</h3>
          <p>
            <span class="status status-${esc(screening.decision || candidate.status)}">${esc(String(screening.decision || candidate.status).replace('_', ' '))}</span>
            <span class="pill pill-muted">${esc(screening.score ?? candidate.score)}/100 vs ${esc(screening.threshold ?? 60)} threshold</span>
          </p>
          <p>${esc(screening.summary || '')}</p>
          <h4>Why</h4>
          ${list(screening.reasons || [], 'No reasoning recorded')}
          ${(screening.blockers || []).length ? `<h4>Blockers</h4>${list(screening.blockers)}` : ''}
          <h4>Next step</h4>
          <p>${esc(screening.next_step || '')}</p>
          <p class="hint">${esc(screening.rule || '')} Applied by the Screening Decision Agent — no manual shortlisting needed.</p>
        </article>

        <article class="result-card">
          <span class="section-tag lav">Section D</span>
          <h3>Interview planning</h3>
          ${renderQuestionCategories(questions.categories)}
          <h4>Critical areas to verify</h4>
          <div class="stack">
            ${(questions.critical_areas || []).map((area) => `
              <div class="gap-card">
                <div class="gap-head"><strong>${esc(area.area)}</strong><span class="tag tag-partial">verify</span></div>
                <p>${esc(area.claim || '')}</p>
                <div class="gap-meta"><b>Why:</b> ${esc(area.why || '')}</div>
                <div class="gap-meta"><b>Ask:</b> ${esc(area.verify_with || '')}</div>
              </div>`).join('') || '<p class="muted">No critical areas flagged.</p>'}
          </div>
        </article>
      </div>

      <div class="result-grid">
        <article class="result-card">
          <span class="section-tag">Section E</span>
          <h3>Status &amp; override</h3>
          <p class="muted">Current status: <span class="status status-${esc(candidate.status)}">${esc(candidate.status.replace('_', ' '))}</span> — set automatically by the agent. Override only if you disagree with its reasoning.</p>
          <div class="shortlist-bar" data-app-id="${esc(candidate.application_id)}">
            <button class="btn btn-ghost btn-sm" data-app-status="under_review">Mark under review</button>
            <button class="btn btn-sage btn-sm" data-app-status="shortlisted">Shortlist</button>
            <button class="btn btn-ghost btn-sm" data-app-status="interview">Select for interview</button>
            <button class="btn btn-danger btn-sm" data-app-status="rejected">Reject</button>
          </div>
        </article>

        <article class="result-card">
          <span class="section-tag lav">Scheduling Agent</span>
          <h3>Interview slots</h3>
          ${scheduled.length ? `
            <div class="stack">
              ${scheduled.map((slot) => `
                <div class="slot-card">
                  <div class="slot-head">
                    <strong>${esc(slot.date)} · ${esc(slot.start_time)}–${esc(slot.end_time)}</strong>
                    <span class="status status-${esc(slot.status)}">${esc(slot.status)}</span>
                  </div>
                  <div class="slot-meta"><span>${esc(slot.interviewer || '')}</span><span>${esc(slot.mode)}</span><span>${esc(slot.timezone || 'UTC')}</span></div>
                </div>`).join('')}
            </div>
            <div class="slot-actions">
              <button class="btn btn-ghost btn-sm" data-open-interviews="1">Open Interview Management</button>
            </div>
            <p class="hint">Reserved automatically for this shortlisted candidate. Confirm the slot, then preview and send the invitation.</p>`
          : '<p class="muted">No slot was reserved for this candidate because they were rejected by the screening rule.</p>'}
        </article>
      </div>
    </div>`;
}

function renderQuestionCategories(categories) {
  if (!categories) return '<p class="muted">No questions generated.</p>';
  return Object.entries(categories).map(([key, group]) => {
    if (!group || !group.questions || !group.questions.length) return '';
    return `
      <div class="question-group">
        <h4>${esc(group.label || key)}</h4>
        ${group.questions.map((question) => `
          <details class="question-card">
            <summary>${esc(question.question)}</summary>
            <div class="q-body">
              <div><b>Purpose:</b> ${esc(question.purpose || '')}</div>
              <div><b>Expected concepts:</b> ${esc((question.expected_concepts || []).join(', '))}</div>
              <div><b>Evaluation criteria:</b> ${esc((question.evaluation_criteria || []).join(', '))}</div>
              <div><b>Related skill:</b> ${esc(question.related_skill || '')}</div>
            </div>
          </details>`).join('')}
      </div>`;
  }).join('') || '<p class="muted">No questions generated.</p>';
}

/* ─── Shortlisting actions ───────────────────────────────────────────── */
async function setApplicationStatus(applicationId, status) {
  try {
    const data = await api(`/api/applications/${applicationId}/status`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    });
    const candidate = state.review.result.candidates.find((item) => item.application_id === applicationId);
    if (candidate) candidate.status = data.application.status;
    renderCandidateTabs();
    renderCandidatePanel();
    toast(`Candidate marked as ${status.replace('_', ' ')}.`);
  } catch (error) {
    toast(error.message);
  }
}

/* ─── Comparison ─────────────────────────────────────────────────────── */
async function loadComparison() {
  const jobId = $('#comparison-job-select').value;
  const node = $('#comparison-body');
  if (!jobId) { node.innerHTML = '<p class="muted">Select a job first.</p>'; return; }
  node.innerHTML = '<p class="muted">Loading…</p>';
  try {
    const data = await api(`/api/jobs/${jobId}/comparison`);
    const best = data.best || {};
    node.innerHTML = `
      <h3>${esc(data.job.title || 'Role')}</h3>
      <div class="compare-scroll">
        <table class="compare-table">
          <thead><tr><th>Candidate</th><th>Score</th><th>Mandatory skills</th><th>Experience</th><th>Missing</th><th>Status</th></tr></thead>
          <tbody>
            ${data.candidates.map((candidate) => `
              <tr class="${candidate.id === best.score ? 'best' : ''}">
                <td><strong>${esc(candidate.name)}</strong><div class="muted">${esc(candidate.grade || '')}</div></td>
                <td><span class="compare-score">${candidate.score}</span><div class="mini-bar"><i style="width:${candidate.score}%"></i></div></td>
                <td>${(candidate.matched_skills || []).slice(0, 5).map((s) => `<span class="tag tag-matched">${esc(s)}</span>`).join('') || '—'}</td>
                <td>${esc(candidate.experience_years || 0)} yrs</td>
                <td>${(candidate.missing_skills || []).slice(0, 5).map((s) => `<span class="tag tag-missing">${esc(s)}</span>`).join('') || '—'}</td>
                <td><span class="status status-${esc(candidate.status)}">${esc(candidate.status.replace('_', ' '))}</span></td>
              </tr>`).join('')}
          </tbody>
        </table>
      </div>
      <h4>Score breakdown by component</h4>
      <div class="compare-scroll">
        <table class="compare-table">
          <thead><tr><th>Component</th>${data.candidates.map((c) => `<th>${esc(c.name)}</th>`).join('')}</tr></thead>
          <tbody>
            ${['Mandatory skills', 'Preferred skills', 'Experience', 'Project relevance', 'Education & certifications', 'Role competencies'].map((label) => `
              <tr>
                <td>${esc(label)}</td>
                ${data.candidates.map((candidate) => {
                  const part = (candidate.breakdown || []).find((item) => item.label === label);
                  return `<td>${part ? `${part.earned}/${part.weight}` : '—'}</td>`;
                }).join('')}
              </tr>`).join('')}
          </tbody>
        </table>
      </div>`;
  } catch (error) {
    node.innerHTML = `<p class="muted">${esc(error.message)}</p>`;
  }
}

/* ─── Interview planning ─────────────────────────────────────────────── */
async function loadPlanning() {
  const jobId = $('#planning-job-select').value;
  const node = $('#planning-body');
  if (!jobId) { node.innerHTML = '<p class="muted">Select a job first.</p>'; return; }
  node.innerHTML = '<p class="muted">Loading…</p>';
  try {
    const data = await api(`/api/jobs/${jobId}/applications`);
    if (!data.applications.length) {
      node.innerHTML = '<p class="muted">No reviewed candidates for this job yet.</p>';
      return;
    }
    node.innerHTML = data.applications.map((application) => {
      const questions = (application.analysis || {}).questions || {};
      return `
        <article class="card">
          <div class="card-head">
            <h3>${esc(application.name)} <span class="pill pill-muted">${application.score}/100</span></h3>
            <span class="status status-${esc(application.status)}">${esc(application.status.replace('_', ' '))}</span>
          </div>
          ${renderQuestionCategories(questions.categories)}
          <h4>Critical areas to verify</h4>
          ${(questions.critical_areas || []).map((area) => `<div class="gap-card"><div class="gap-head"><strong>${esc(area.area)}</strong></div><p>${esc(area.verify_with || '')}</p></div>`).join('') || '<p class="muted">None flagged.</p>'}
        </article>`;
    }).join('');
  } catch (error) {
    node.innerHTML = `<p class="muted">${esc(error.message)}</p>`;
  }
}

/* ─── Interview management ───────────────────────────────────────────── */
async function loadCandidatesForSlots() {
  if (state.allCandidates.length) return state.allCandidates;
  const jobs = await api('/api/jobs');
  const all = [];
  for (const job of jobs.jobs) {
    try {
      const data = await api(`/api/jobs/${job.id}/applications`);
      data.applications.forEach((application) => {
        all.push({ ...application, job_title: job.title });
      });
    } catch (error) { /* job without applications */ }
  }
  state.allCandidates = all;
  const select = $('#slot-candidate');
  if (select) {
    select.innerHTML = '<option value="">— Leave slot available —</option>' + all.map(
      (candidate) => `<option value="${esc(candidate.id)}">${esc(candidate.name)} — ${esc(candidate.job_title || '')} (${candidate.score}/100)</option>`
    ).join('');
  }
  return all;
}

async function loadSlots() {
  const node = $('#slots-list');
  try {
    const [data] = await Promise.all([api('/api/interview-slots'), loadCandidatesForSlots()]);
    $('#slots-count').textContent = `${data.slots.length}`;
    node.innerHTML = data.slots.length
      ? data.slots.map((slot) => renderSlot(slot)).join('')
      : '<p class="muted">No slots yet.</p>';
  } catch (error) {
    node.innerHTML = `<p class="muted">${esc(error.message)}</p>`;
  }
}

function renderSlot(slot) {
  const cancelled = slot.status === 'cancelled';
  return `
    <div class="slot-card">
      <div class="slot-head">
        <span class="slot-when">${esc(slot.date)} · ${esc(slot.start_time)}–${esc(slot.end_time)}</span>
        <span class="status status-${esc(slot.status)}">${esc(slot.status)}</span>
      </div>
      <div class="slot-meta">
        <span>${esc(slot.candidate_name || 'Unassigned')}</span>
        <span>${esc(slot.candidate_email || 'no email')}</span>
        <span>${esc(slot.mode)}</span>
        <span>${esc(slot.timezone)}</span>
        ${slot.interviewer ? `<span>${esc(slot.interviewer)}</span>` : ''}
      </div>
      <div class="slot-actions">
        ${!cancelled && !slot.candidate_name ? `<select data-assign-select="${esc(slot.id)}"><option value="">Assign candidate…</option>${state.allCandidates.map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('')}</select><button class="btn btn-ghost btn-sm" data-assign="${esc(slot.id)}">Assign</button>` : ''}
        ${!cancelled && slot.candidate_name && slot.status !== 'confirmed' ? `<button class="btn btn-sage btn-sm" data-confirm="${esc(slot.id)}">Confirm</button>` : ''}
        ${!cancelled ? `<button class="btn btn-ghost btn-sm" data-email="${esc(slot.id)}">Preview email</button>` : ''}
        ${!cancelled ? `<button class="btn btn-danger btn-sm" data-cancel="${esc(slot.id)}">Cancel</button>` : ''}
      </div>
    </div>`;
}

async function createSlot(event) {
  event.preventDefault();
  const errorNode = $('#slot-error');
  errorNode.textContent = '';
  const applicationId = $('#slot-candidate').value;
  const candidate = state.allCandidates.find((item) => item.id === applicationId);
  try {
    await api('/api/interview-slots', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        date: $('#slot-date').value,
        start_time: $('#slot-start').value,
        end_time: $('#slot-end').value,
        timezone: $('#slot-timezone').value,
        mode: $('#slot-mode').value,
        interviewer: $('#slot-interviewer').value.trim(),
        location: $('#slot-location').value.trim(),
        instructions: $('#slot-notes').value.trim(),
        application_id: applicationId,
        candidate_name: candidate ? candidate.name : '',
        candidate_email: candidate ? candidate.email : '',
        job_id: candidate ? candidate.job_id : '',
      }),
    });
    $('#slot-form').reset();
    toast('Interview slot created.');
    loadSlots();
  } catch (error) {
    errorNode.textContent = error.message;
  }
}

async function assignSlot(slotId) {
  const select = document.querySelector(`[data-assign-select="${slotId}"]`);
  const applicationId = select ? select.value : '';
  if (!applicationId) { toast('Choose a candidate first.'); return; }
  try {
    await api(`/api/interview-slots/${slotId}/assign`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ application_id: applicationId }),
    });
    toast('Candidate assigned.');
    loadSlots();
  } catch (error) {
    toast(error.message);
  }
}

async function confirmSlot(slotId) {
  try {
    await api(`/api/interview-slots/${slotId}/confirm`, { method: 'POST' });
    toast('Interview confirmed.');
    loadSlots();
  } catch (error) {
    toast(error.message);
  }
}

async function cancelSlot(slotId) {
  if (!window.confirm('Cancel this interview slot?')) return;
  try {
    await api(`/api/interview-slots/${slotId}`, { method: 'DELETE' });
    toast('Slot cancelled.');
    loadSlots();
  } catch (error) {
    toast(error.message);
  }
}

async function openEmailPanel(slotId) {
  state.activeSlot = slotId;
  const panel = $('#email-panel');
  panel.hidden = false;
  $('#email-result').textContent = '';
  panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  try {
    const data = await api(`/api/interview-slots/${slotId}/email-preview`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    $('#email-to').value = data.to || '';
    $('#email-subject').value = data.subject || '';
    $('#email-body').value = data.body || '';
    $('#email-status').textContent = data.already_sent ? `Already ${data.previous_status}` : 'Preview';
    $('#email-configured-note').className = data.email_configured ? 'banner banner-small' : 'banner banner-small banner-warn';
    $('#email-configured-note').textContent = data.email_configured
      ? 'SMTP is configured. Pressing SEND EMAIL will deliver the message.'
      : 'Demo mode: no SMTP credentials are configured, so nothing will actually be sent. The attempt is logged.';
    $('#btn-send-email').disabled = Boolean(data.already_sent);
  } catch (error) {
    $('#email-result').textContent = error.message;
  }
}

async function sendEmail() {
  if (!state.activeSlot) return;
  const button = $('#btn-send-email');
  const result = $('#email-result');
  button.disabled = true;
  result.textContent = 'Sending…';
  try {
    const data = await api(`/api/interview-slots/${state.activeSlot}/send-email`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        to_email: $('#email-to').value.trim(),
        subject: $('#email-subject').value.trim(),
        body: $('#email-body').value,
      }),
    });
    result.textContent = data.message;
    result.className = data.sent ? 'form-notice ok' : 'form-notice';
    if (data.sent) {
      button.textContent = 'SENT';
    } else {
      button.disabled = false;
    }
    loadSlots();
  } catch (error) {
    result.textContent = error.message;
    button.disabled = false;
  }
}

/* ─── Init ───────────────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
  $$('.nav-link').forEach((link) => link.addEventListener('click', () => setView(link.dataset.view)));
  $$('[data-goto]').forEach((button) => button.addEventListener('click', () => setView(button.dataset.goto)));
  $('#menu-toggle').addEventListener('click', () => $('#sidebar').classList.toggle('open'));

  $('#job-form').addEventListener('submit', submitJob);

  initDropZone();
  $('#btn-analyze-jd').addEventListener('click', analyzeDescription);
  $('#btn-load-sample').addEventListener('click', loadSampleData);
  $('#btn-review').addEventListener('click', startReview);
  $('#review-job-select').addEventListener('change', (event) => applyJobToForm(event.target.value));

  $('#file-list').addEventListener('click', (event) => {
    const button = event.target.closest('[data-remove-file]');
    if (!button) return;
    state.files.splice(Number(button.dataset.removeFile), 1);
    renderFiles();
  });

  $('#jobs-list').addEventListener('click', async (event) => {
    const review = event.target.closest('[data-review-job]');
    if (review) {
      setView('review');
      $('#review-job-select').value = review.dataset.reviewJob;
      applyJobToForm(review.dataset.reviewJob);
      return;
    }
    const edit = event.target.closest('[data-edit-job]');
    if (edit) {
      const job = state.jobs.find((item) => item.id === edit.dataset.editJob);
      if (!job) return;
      $('#job-title').value = job.title || '';
      $('#job-description').value = job.description || '';
      $('#job-required').value = (job.required_skills || []).join(', ');
      $('#job-preferred').value = (job.preferred_skills || []).join(', ');
      $('#job-min-years').value = job.min_years || '';
      return;
    }
    const remove = event.target.closest('[data-delete-job]');
    if (remove) {
      if (!window.confirm('Delete this job?')) return;
      try {
        await api(`/api/jobs/${remove.dataset.deleteJob}`, { method: 'DELETE' });
        toast('Job deleted.');
        loadJobs();
      } catch (error) {
        toast(error.message);
      }
    }
  });

  $('#dash-jobs').addEventListener('click', (event) => {
    const button = event.target.closest('[data-review-job]');
    if (!button) return;
    setView('review');
    $('#review-job-select').value = button.dataset.reviewJob;
    applyJobToForm(button.dataset.reviewJob);
  });

  $('#btn-sample-dashboard').addEventListener('click', async () => {
    setView('review');
    await loadSampleData();
  });

  $('#candidate-tabs').addEventListener('click', (event) => {
    const tab = event.target.closest('[data-candidate]');
    if (!tab) return;
    state.activeCandidate = Number(tab.dataset.candidate);
    renderCandidateTabs();
    renderCandidatePanel();
  });

  $('#candidate-panels').addEventListener('click', (event) => {
    const status = event.target.closest('[data-app-status]');
    if (status) {
      const holder = status.closest('[data-app-id]');
      setApplicationStatus(holder.dataset.appId, status.dataset.appStatus);
      return;
    }
    const openSlots = event.target.closest('[data-open-interviews]');
    if (openSlots) setView('interviews');
  });

  $('#btn-export-report').addEventListener('click', () => {
    if (!state.review || !state.review.result) return;
    const blob = new Blob([JSON.stringify(state.review.result, null, 2)], { type: 'application/json' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `veyora-review-${(state.review.result.review_id || 'report').slice(0, 8)}.json`;
    link.click();
    URL.revokeObjectURL(link.href);
  });

  $('#btn-goto-comparison').addEventListener('click', () => {
    setView('comparison');
    const jobId = state.review && state.review.result ? state.review.result.job_id : '';
    if (jobId) {
      $('#comparison-job-select').value = jobId;
      loadComparison();
    }
  });

  $('#btn-comparison').addEventListener('click', loadComparison);
  $('#btn-planning').addEventListener('click', loadPlanning);

  const tomorrow = new Date(Date.now() + 86400000);
  const iso = tomorrow.toISOString().slice(0, 10);
  $('#slot-date').value = iso;
  $('#slot-start').value = '10:00';
  $('#slot-end').value = '11:00';
  $('#slot-form').addEventListener('submit', createSlot);

  $('#slots-list').addEventListener('click', (event) => {
    const assign = event.target.closest('[data-assign]');
    if (assign) return assignSlot(assign.dataset.assign);
    const confirm = event.target.closest('[data-confirm]');
    if (confirm) return confirmSlot(confirm.dataset.confirm);
    const cancel = event.target.closest('[data-cancel]');
    if (cancel) return cancelSlot(cancel.dataset.cancel);
    const email = event.target.closest('[data-email]');
    if (email) return openEmailPanel(email.dataset.email);
  });

  $('#btn-send-email').addEventListener('click', sendEmail);
  $('#btn-close-email').addEventListener('click', () => { $('#email-panel').hidden = true; });

  loadDashboard();
  loadJobs();
  loadSlots();
});
