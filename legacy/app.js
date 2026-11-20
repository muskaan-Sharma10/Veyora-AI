/**
 * Veyora AI — Recruiter workspace
 * Handles form submission, progress updates, and results.
 */

'use strict';

// ══════════════════════════════════════════════
// State
// ══════════════════════════════════════════════
let currentJobId = null;
let eventSource = null;
let selectedFiles = [];
let emailConfigured = null;

// ══════════════════════════════════════════════
// Init
// ══════════════════════════════════════════════
document.addEventListener('DOMContentLoaded', () => {
  initDropZone();
  initSlider();
  initForm();
  checkEmailSetup();
});

// ══════════════════════════════════════════════
// Drop Zone
// ══════════════════════════════════════════════
function initDropZone() {
  const dropZone = document.getElementById('drop-zone');
  const fileInput = document.getElementById('resume-files');

  if (!dropZone || !fileInput) return;

  dropZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropZone.classList.add('dragging');
  });
  dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragging'));
  dropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropZone.classList.remove('dragging');
    if (e.dataTransfer && e.dataTransfer.files) {
      addFiles(Array.from(e.dataTransfer.files));
    }
  });
  fileInput.addEventListener('change', () => {
    if (fileInput.files) {
      addFiles(Array.from(fileInput.files));
    }
    fileInput.value = '';
  });
}

function addFiles(files) {
  const notice = document.getElementById('file-notice');
  const rejected = [];
  files.forEach(f => {
    const extension = f.name.split('.').pop().toLowerCase();
    if (!['pdf', 'txt'].includes(extension)) {
      rejected.push(`${f.name}: only PDF and UTF-8 text files are supported`);
      return;
    }
    if (f.size > 50 * 1024 * 1024) {
      rejected.push(`${f.name}: file exceeds the 50 MB total upload limit`);
      return;
    }
    if (selectedFiles.find(x => x.name === f.name)) return;
    if (selectedFiles.length >= 20) {
      rejected.push(`${f.name}: no more than 20 resumes can be uploaded`);
      return;
    }
    const currentSize = selectedFiles.reduce((total, file) => total + file.size, 0);
    if (currentSize + f.size > 50 * 1024 * 1024) {
      rejected.push(`${f.name}: total upload size cannot exceed 50 MB`);
      return;
    }
    selectedFiles.push(f);
  });
  if (notice) notice.textContent = rejected.join('. ');
  renderFileList();
}

function renderFileList() {
  const container = document.getElementById('file-list');
  if (!container) return;
  container.innerHTML = '';
  selectedFiles.forEach((file, idx) => {
    const item = document.createElement('div');
    item.className = 'file-item';
    item.innerHTML = `
      <span class="file-item-icon">📄</span>
      <span class="file-item-name" title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</span>
      <span class="file-item-size">${formatFileSize(file.size)}</span>
      <button type="button" class="file-item-remove" onclick="removeFile(${idx})" title="Remove">✕</button>
    `;
    container.appendChild(item);
  });
}

function removeFile(idx) {
  selectedFiles.splice(idx, 1);
  renderFileList();
}
window.removeFile = removeFile;

function formatFileSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

// ══════════════════════════════════════════════
// Slider
// ══════════════════════════════════════════════
function initSlider() {
  const slider = document.getElementById('top-n');
  const display = document.getElementById('top-n-value');
  if (!slider || !display) return;
  const update = () => { display.textContent = `${slider.value} candidates`; };
  slider.addEventListener('input', update);
  update();
}

// ══════════════════════════════════════════════
// Sample Data Loader
// ══════════════════════════════════════════════
async function loadSampleData() {
  const button = document.getElementById('btn-sample');
  const notice = document.getElementById('file-notice');
  if (button) button.disabled = true;
  if (notice) notice.textContent = '';

  try {
    const sampleResponse = await fetch('/api/sample-data');
    const sample = await sampleResponse.json();
    if (!sampleResponse.ok) throw new Error(sample.error || 'Unable to load demo data');

    const resumes = await Promise.all(sample.resumes.map(async (filename) => {
      const response = await fetch(`/api/sample-resumes/${encodeURIComponent(filename)}`);
      if (!response.ok) throw new Error(`Could not load ${filename}`);
      return new File([await response.blob()], filename, { type: 'application/pdf' });
    }));

    const textarea = document.getElementById('job-description');
    if (textarea) textarea.value = sample.job_description;
    selectedFiles = [];
    addFiles(resumes);
    if (textarea) textarea.focus();
  } catch (error) {
    if (notice) notice.textContent = error.message;
  } finally {
    if (button) button.disabled = false;
  }
}
window.loadSampleData = loadSampleData;

// ══════════════════════════════════════════════
// Form Submit
// ══════════════════════════════════════════════
function initForm() {
  const form = document.getElementById('pipeline-form');
  if (!form) return;
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    await runPipeline();
  });
}

async function runPipeline() {
  const jdInput = document.getElementById('job-description');
  const jd = jdInput ? jdInput.value.trim() : '';
  const formError = document.getElementById('form-error');
  if (formError) formError.textContent = '';
  if (!jd) {
    if (formError) formError.textContent = 'Please add the role details.';
    return;
  }
  if (selectedFiles.length === 0) {
    if (formError) formError.textContent = 'Please add at least one resume.';
    return;
  }

  // Build form data
  const formData = new FormData();
  formData.append('job_description', jd);
  formData.append('top_n', document.getElementById('top-n').value);
  selectedFiles.forEach(f => formData.append('resumes', f));

  // UI: set running state
  setRunningState(true);
  showPipelineSection();
  resetStages();
  updateBadge('running', 'Reviewing…');

  try {
    const resp = await fetch('/api/run-pipeline', { method: 'POST', body: formData });
    if (!resp.ok) {
      let errMsg = "We couldn't start the review. Please try again.";
      try {
        const err = await resp.json();
        errMsg = [err.error, ...(err.details || [])].filter(Boolean).join(' ');
      } catch (_) {}
      throw new Error(errMsg);
    }
    const { job_id } = await resp.json();
    currentJobId = job_id;
    startStreaming(job_id);
  } catch (err) {
    showError(err.message);
    setRunningState(false);
    updateBadge('error', 'Needs attention');
  }
}

// ══════════════════════════════════════════════
// SSE Streaming
// ══════════════════════════════════════════════
function startStreaming(jobId) {
  if (eventSource) { eventSource.close(); }
  eventSource = new EventSource(`/api/stream/${jobId}`);

  eventSource.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);

      if (data.status === 'complete') {
        eventSource.close();
        onPipelineComplete(data.result);
      } else if (data.status === 'error') {
        eventSource.close();
        showError(data.error || "We couldn't complete this review. Please try again.");
        setRunningState(false);
        updateBadge('error', 'Needs attention');
      } else {
        // Progress update
        updateStage(data.stage, data.status, data.message);
      }
    } catch (e) {
      console.error('Error parsing SSE event data:', e);
    }
  };

  eventSource.onerror = () => {
    // SSE closed or errored — poll for result fallback
    eventSource.close();
    pollForResult(jobId);
  };
}

async function pollForResult(jobId) {
  let attempts = 0;
  const maxAttempts = 180; // 3 mins timeout
  const poll = async () => {
    attempts++;
    if (attempts > maxAttempts) {
      showError("This is taking longer than expected. Please try again.");
      setRunningState(false);
      return;
    }
    try {
      const resp = await fetch(`/api/status/${jobId}`);
      if (!resp.ok) throw new Error('Status check failed');
      const data = await resp.json();

      // Update stages from progress array
      (data.progress || []).forEach(p => updateStage(p.stage, p.status, p.message));

      if (data.status === 'complete') {
        onPipelineComplete(data.result);
      } else if (data.status === 'error') {
        showError(data.error || "We couldn't complete this review. Please try again.");
        setRunningState(false);
        updateBadge('error', 'Needs attention');
      } else {
        setTimeout(poll, 1500);
      }
    } catch {
      setTimeout(poll, 2000);
    }
  };
  poll();
}

// ══════════════════════════════════════════════
// Stage UI Updates
// ══════════════════════════════════════════════
const STAGE_MAP = {
  'Parsing Resumes':       'parsing',
  'Matching Candidates':   'matching',
  'Ranking Candidates':    'ranking',
  'Preparing Interview Guidance': 'guidance',
  'Bias Review':           'bias',
  'Scheduling Interviews': 'scheduling',
};
const STAGE_LABELS = {
  parsing: 'Reading resumes',
  matching: 'Comparing experience with the role',
  ranking: 'Preparing candidate summaries',
  guidance: 'Preparing interview questions and skill-gap notes',
  bias: 'Checking review notes',
  scheduling: 'Suggesting interview times',
};
const stageProgress = { parsing: 10, matching: 30, ranking: 50, guidance: 68, bias: 82, scheduling: 95 };

function resetStages() {
  setProgress(0);
  const subtitle = document.getElementById('pipeline-subtitle');
  if (subtitle) subtitle.textContent = 'Getting things ready…';
  const results = document.getElementById('results-section');
  if (results) results.style.display = 'none';
  const cards = document.getElementById('summary-cards');
  if (cards) cards.innerHTML = '';
}

function updateStage(stageName, status, message) {
  const key = STAGE_MAP[stageName];
  if (!key) return;

  // Update progress bar
  if (status === 'running') setProgress(stageProgress[key]);
  if (status === 'complete') setProgress(Math.min(100, stageProgress[key] + 15));

  const subtitle = document.getElementById('pipeline-subtitle');
  if (!subtitle) return;
  if (status === 'running') subtitle.textContent = `${STAGE_LABELS[key]}…`;
  if (status === 'complete') subtitle.textContent = `${STAGE_LABELS[key]} complete.`;
  if (status === 'error') subtitle.textContent = `We couldn't finish: ${STAGE_LABELS[key].toLowerCase()}.`;
}

function setProgress(pct) {
  const bar = document.getElementById('progress-bar');
  if (bar) bar.style.width = Math.min(100, pct) + '%';
}

// ══════════════════════════════════════════════
// Results Rendering
// ══════════════════════════════════════════════
function onPipelineComplete(result) {
  setProgress(100);
  setRunningState(false);
  updateBadge('complete', 'Ready to review');
  const subtitle = document.getElementById('pipeline-subtitle');
  if (subtitle) subtitle.textContent = 'Your candidate review is ready.';

  if (!result) return;

  // Render all result views
  renderSummaryCards(result);
  renderCandidates(result.ranked || []);
  renderBiasReport(result.bias_report);
  renderSchedule(result.schedule || []);
  const downloadLink = document.getElementById('download-report');
  if (downloadLink && currentJobId) {
    downloadLink.href = `/api/download/${encodeURIComponent(currentJobId)}`;
  }

  // Display results section & scroll
  const resultsSec = document.getElementById('results-section');
  if (resultsSec) {
    resultsSec.style.display = 'block';
    resultsSec.scrollIntoView({ behavior: 'smooth' });
  }
}

// ─── Summary Cards ───
function renderSummaryCards(result) {
  const container = document.getElementById('summary-cards');
  if (!container) return;

  const totalCandidates = (result.ranked && result.ranked.length) || (result.bias_report && result.bias_report.reviewed_candidates) || 0;
  const topCandidate = result.ranked && result.ranked.length > 0 ? result.ranked[0] : null;
  const topScore = topCandidate ? Number(topCandidate.score).toFixed(1) : '—';
  const slotsCount = (result.schedule && result.schedule.length) || 0;
  const biasPassed = result.bias_report ? result.bias_report.passed : true;
  const flagsCount = result.bias_report && result.bias_report.flags ? result.bias_report.flags.length : 0;

  container.innerHTML = `
    <div class="summary-card">
      <div class="summary-card-label">Candidates reviewed</div>
      <div class="summary-card-value">${totalCandidates}</div>
      <div class="summary-card-sub">Resume details organized</div>
    </div>
    <div class="summary-card">
      <div class="summary-card-label">Strongest role fit</div>
      <div class="summary-card-value">${topScore}%</div>
      <div class="summary-card-sub">${topCandidate ? escapeHtml(topCandidate.name) : 'No review results yet'}</div>
    </div>
    <div class="summary-card">
      <div class="summary-card-label">Suggested times</div>
      <div class="summary-card-value">${slotsCount}</div>
      <div class="summary-card-sub">For interview planning</div>
    </div>
    <div class="summary-card ${biasPassed ? 'card-bias-pass' : 'card-bias-fail'}">
      <div class="summary-card-label">Fairness check</div>
      <div class="summary-card-value">${biasPassed ? 'NO FLAGS' : 'REVIEW'}</div>
      <div class="summary-card-sub">${flagsCount === 0 ? 'No concerns were flagged' : `${flagsCount} note(s) need review`}</div>
    </div>
  `;
}

// ─── Candidates List ───
function renderCandidates(ranked) {
  const container = document.getElementById('candidates-list');
  if (!container) return;

  if (!ranked || ranked.length === 0) {
    container.innerHTML = '<div style="padding:30px; text-align:center; color:var(--text-secondary)">No candidate details are ready yet.</div>';
    return;
  }

  container.innerHTML = ranked.map((c, idx) => {
    const rank = c.rank || (idx + 1);
    const scoreVal = Math.round(Number(c.score || 0));
    const scoreGradient = scoreVal >= 80
      ? 'linear-gradient(90deg, #10b981, #059669)'
      : scoreVal >= 60
      ? 'linear-gradient(90deg, #f59e0b, #d97706)'
      : 'linear-gradient(90deg, #ef4444, #dc2626)';

    const scoreColor = scoreVal >= 80 ? 'var(--success)' : scoreVal >= 60 ? 'var(--warning)' : 'var(--danger)';
    const hasFlags = c.bias_flags && c.bias_flags.length > 0;

    // Skills
    const skillsHtml = (c.skills || []).map(s => `<span class="tag tag-skill">⚡ ${escapeHtml(s)}</span>`).join('');
    const matchedHtml = (c.matched_requirements || []).map(m => `<span class="tag tag-matched">✓ Mentioned: ${escapeHtml(m)}</span>`).join('');
    const missingHtml = (c.missing_requirements || []).map(m => `<span class="tag tag-missing">Not shown: ${escapeHtml(m)}</span>`).join('');

    // Per-requirement justification
    let justListHtml = '';
    if (Array.isArray(c.per_requirement_justification)) {
      justListHtml = c.per_requirement_justification.map(j => {
        const isMet = j.met !== false;
        return `
          <div class="justification-item ${isMet ? 'met' : 'unmet'}">
            <div class="just-icon">${isMet ? '✅' : '❌'}</div>
            <div class="just-content">
              <div class="just-req">${escapeHtml(j.requirement || 'Role need')}</div>
              <div class="just-evidence">${escapeHtml(j.evidence || 'No resume detail noted')}</div>
              ${j.score_contribution != null ? `<div class="just-score">Fit points: +${j.score_contribution}</div>` : ''}
            </div>
          </div>
        `;
      }).join('');
    } else if (c.per_requirement_justification && typeof c.per_requirement_justification === 'object') {
      justListHtml = Object.entries(c.per_requirement_justification).map(([req, val]) => {
        const isMet = (c.matched_requirements || []).includes(req);
        const evidence = typeof val === 'object' && val !== null ? (val.evidence || JSON.stringify(val)) : String(val);
        return `
          <div class="justification-item ${isMet ? 'met' : 'unmet'}">
            <div class="just-icon">${isMet ? '✅' : '❌'}</div>
            <div class="just-content">
              <div class="just-req">${escapeHtml(req)}</div>
              <div class="just-evidence">${escapeHtml(evidence)}</div>
            </div>
          </div>
        `;
      }).join('');
    }

    // Bias flags inline
    let biasHtml = '';
    if (hasFlags) {
      biasHtml = `
        <div class="bias-flags-inline">
          ${c.bias_flags.map(f => `
            <div class="bias-flag-item">
              <span>⚠️</span>
              <span><strong>Review note:</strong> ${escapeHtml(typeof f === 'string' ? f : (f.explanation || f.text || JSON.stringify(f)))}</span>
            </div>
          `).join('')}
        </div>
      `;
    }

    const expText = c.experience_years != null ? `${c.experience_years} years experience` : '';
    const guidance = c.guidance || {};
    const questionsHtml = Array.isArray(guidance.interview_questions) && guidance.interview_questions.length
      ? `<div class="candidate-guidance"><h4>Interview questions to consider</h4><ul>${guidance.interview_questions.map(q => `<li>${escapeHtml(q)}</li>`).join('')}</ul></div>`
      : '';
    const gapHtml = guidance.gap_summary
      ? `<div class="candidate-guidance"><h4>Skills to build or clarify</h4><p>${escapeHtml(guidance.gap_summary)}</p></div>`
      : '';
    const learningHtml = Array.isArray(guidance.learning_steps) && guidance.learning_steps.length
      ? `<div class="candidate-guidance"><h4>Possible development steps</h4><ul>${guidance.learning_steps.map(step => `<li>${escapeHtml(step)}</li>`).join('')}</ul></div>`
      : '';
    const resumeHtml = Array.isArray(guidance.resume_improvements) && guidance.resume_improvements.length
      ? `<div class="candidate-guidance"><h4>Resume suggestions</h4><ul>${guidance.resume_improvements.map(tip => `<li>${escapeHtml(tip)}</li>`).join('')}</ul></div>`
      : '';

    return `
      <div class="candidate-card ${hasFlags ? 'has-flags' : ''}">
        <div class="candidate-header">
          <div class="candidate-rank">#${rank}</div>
          <div class="candidate-info">
            <div class="candidate-name">${escapeHtml(c.name || 'Candidate')}</div>
            <div class="candidate-email">${escapeHtml(c.email || 'No email provided')}</div>
            ${expText ? `<div class="candidate-exp">💼 ${escapeHtml(expText)}</div>` : ''}
          </div>
          <div class="candidate-score-block">
            <div class="score-number" style="color: ${scoreColor}">${scoreVal}%</div>
            <div class="score-label">ROLE FIT</div>
          </div>
        </div>

        <div class="score-bar-container">
          <div class="score-bar" style="width: ${scoreVal}%; background: ${scoreGradient};"></div>
        </div>

        ${c.rationale ? `<div class="candidate-rationale">"${escapeHtml(c.rationale)}"</div>` : ''}

        <div class="candidate-tags">
          ${skillsHtml}
          ${matchedHtml}
          ${missingHtml}
        </div>

        ${justListHtml ? `
          <div class="candidate-justifications">
            <button type="button" class="justification-toggle" onclick="toggleJustifications(this)">
              <span>🔍 Why this score?</span> ▾
            </button>
            <div class="justification-list">
              ${justListHtml}
            </div>
          </div>
        ` : ''}

        ${gapHtml}${learningHtml}${questionsHtml}${resumeHtml}
        ${biasHtml}
      </div>
    `;
  }).join('');
}

function toggleJustifications(btn) {
  const list = btn.nextElementSibling;
  if (!list) return;
  const isOpen = list.classList.toggle('open');
  const arrow = isOpen ? '▴' : '▾';
  const label = isOpen ? 'Hide details' : 'Why this score?';
  btn.innerHTML = `<span>🔍 ${label}</span> ${arrow}`;
}
window.toggleJustifications = toggleJustifications;

// ─── Bias Report Panel ───
function renderBiasReport(bias) {
  const container = document.getElementById('bias-container');
  if (!container) return;

  if (!bias) {
    container.innerHTML = '<div style="padding:20px; color:var(--text-secondary)">Fairness notes are not ready yet.</div>';
    return;
  }

  const isPassed = bias.passed !== false && (!bias.flags || bias.flags.length === 0);
  const flags = bias.flags || [];

  let flagsHtml = '';
  if (flags.length > 0) {
    flagsHtml = `
      <div class="bias-flags-list">
        ${flags.map(f => {
          const sev = (f.severity || 'MEDIUM').toUpperCase();
          const attr = f.protected_attribute || f.attribute || 'Protected Attribute';
          const text = f.rationale_text || f.text || '';
          const expl = f.explanation || f.reason || '';
          const cand = f.candidate_name ? ` · Candidate: ${escapeHtml(f.candidate_name)}` : '';
          return `
            <div class="bias-flag-card">
              <div class="bias-flag-header">
                <span class="bias-attr ${sev}">${sev} RISK: ${escapeHtml(attr)}</span>
                <span style="font-size:0.75rem; color:var(--text-muted);">${cand}</span>
              </div>
              ${text ? `<div class="bias-text">"${escapeHtml(text)}"</div>` : ''}
              <div class="bias-explanation">${escapeHtml(expl)}</div>
            </div>
          `;
        }).join('')}
      </div>
    `;
  } else {
    flagsHtml = `
      <div style="padding: 28px; text-align: center; color: var(--text-secondary); background: var(--glass-bg); border-radius: var(--radius-md); border: 1px solid var(--glass-border);">
        <div style="font-size: 2.2rem; margin-bottom: 8px;">🛡️</div>
        <div style="font-size: 1.05rem; font-weight: 700; color: var(--success); margin-bottom: 4px;">No concerns were flagged</div>
        <div style="font-size: 0.85rem; color: var(--text-muted); max-width: 500px; margin: 0 auto;">
          This check can miss things. Please review the resume and notes yourself.
        </div>
      </div>
    `;
  }

  const reviewed = bias.reviewed_candidates || 0;
  const assessment = bias.overall_assessment || (isPassed
    ? 'No concerns were flagged. This check can miss things, so please review the resume and notes yourself.'
    : 'Some notes may need attention. Please review them before deciding on next steps.');

  container.innerHTML = `
    <div class="bias-overall ${isPassed ? 'passed' : 'failed'}">
      <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; flex-wrap: wrap; gap: 6px;">
        <div style="display: flex; align-items: center; gap: 8px;">
          <span style="font-size: 1.3rem;">${isPassed ? 'ℹ️' : '⚠️'}</span>
          <strong>${isPassed ? 'NO CONCERNS FLAGGED' : 'PLEASE REVIEW THESE NOTES'}</strong>
        </div>
        <span style="font-size: 0.8rem; opacity: 0.9;">Resumes checked: ${reviewed}</span>
      </div>
      <div>${escapeHtml(assessment)}</div>
    </div>
    ${flagsHtml}
  `;
}

// ─── Schedule Panel ───
function renderSchedule(schedule) {
  const container = document.getElementById('schedule-container');
  if (!container) return;

  if (!schedule || schedule.length === 0) {
    container.innerHTML = '<div style="padding:30px; text-align:center; color:var(--text-secondary)">No suggested times are available yet.</div>';
    return;
  }

  container.innerHTML = schedule.map((slot, idx) => {
    const mode = slot.mode || 'Video';
    const cleanMode = mode.replace(/\s+/g, '-');
    const dt = slot.slot_datetime || `${slot.slot_date || ''} ${slot.slot_time || ''}`.trim() || 'TBD';
    const duration = slot.duration_minutes || 60;
    const interviewer = slot.interviewer || 'Hiring Lead';

    return `
      <div class="schedule-card">
        <div class="schedule-rank">#${idx + 1}</div>
        <div class="schedule-info">
          <div class="schedule-name">${escapeHtml(slot.candidate_name)}</div>
          <div class="schedule-meta">
            <span>📅 ${escapeHtml(dt)}</span>
            <span>👤 Host: ${escapeHtml(interviewer)}</span>
            <span>⏱️ ${duration} min</span>
          </div>
        </div>
        <div class="schedule-mode">
          <span class="mode-badge mode-${cleanMode}">${escapeHtml(mode)}</span>
          ${slot.meeting_link ? `<a href="${escapeHtml(slot.meeting_link)}" target="_blank" rel="noopener noreferrer" class="meeting-link">🔗 Join Meeting</a>` : ''}
          ${slot.candidate_email && slot.candidate_id ? `<button type="button" class="invitation-button" data-candidate-id="${escapeHtml(slot.candidate_id)}" data-email="${escapeHtml(slot.candidate_email)}">Send interview email</button>` : '<div class="invitation-status">No email address found in this resume.</div>'}
          <div class="invitation-status" aria-live="polite"></div>
        </div>
      </div>
    `;
  }).join('');
  container.querySelectorAll('.invitation-button').forEach(button => {
    button.disabled = emailConfigured === false;
    button.addEventListener('click', () => sendInvitation(button));
  });
}

async function checkEmailSetup() {
  const note = document.getElementById('email-setup-note');
  if (!note) return;
  try {
    const response = await fetch('/api/email-settings');
    const settings = await response.json();
    if (!response.ok) throw new Error(settings.error || 'Unable to check email setup.');
    emailConfigured = settings.configured;
    note.textContent = emailConfigured
      ? 'Email is ready. Suggested times are not booked; an invitation is sent only after you confirm.'
      : 'Email is not set up yet. Add SMTP settings to .env before sending interview invitations.';
    document.querySelectorAll('.invitation-button').forEach(button => { button.disabled = !emailConfigured; });
  } catch (error) {
    note.textContent = error.message || 'Unable to check email setup.';
  }
}

async function sendInvitation(button) {
  const candidateId = button.dataset.candidateId;
  const email = button.dataset.email;
  if (!candidateId || !window.confirm(`Send an interview invitation to ${email}?`)) return;
  const status = button.parentElement.querySelector('.invitation-status');
  button.disabled = true;
  if (status) status.textContent = 'Sending…';
  try {
    const response = await fetch(`/api/invitations/${encodeURIComponent(currentJobId)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ candidate_id: candidateId }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'The email could not be sent.');
    if (status) status.textContent = data.message;
    button.textContent = 'Invitation sent';
  } catch (error) {
    if (status) status.textContent = error.message || 'The email could not be sent.';
    button.disabled = emailConfigured === false;
  }
}

// ─── Tabs Switching ───
function switchTab(tabName) {
  const tabs = document.querySelectorAll('.tab');
  const panels = document.querySelectorAll('.tab-panel');

  tabs.forEach(t => t.classList.remove('active'));
  panels.forEach(p => p.classList.remove('active'));

  const activeTab = document.getElementById(`tab-${tabName}`);
  const activePanel = document.getElementById(`panel-${tabName}`);

  if (activeTab) activeTab.classList.add('active');
  if (activePanel) activePanel.classList.add('active');
}
window.switchTab = switchTab;

// ══════════════════════════════════════════════
// Helpers & Utilities
// ══════════════════════════════════════════════
function setRunningState(isRunning) {
  const btn = document.getElementById('btn-run');
  if (btn) {
    btn.disabled = isRunning;
    btn.innerHTML = isRunning
      ? '<span class="spin">⚡</span><span>Reviewing resumes…</span>'
      : '<span class="btn-icon">▶</span><span>Review candidates</span>';
  }
}

function showPipelineSection() {
  const welcome = document.getElementById('welcome-screen');
  const pipeline = document.getElementById('pipeline-section');
  if (welcome) welcome.style.display = 'none';
  if (pipeline) pipeline.style.display = 'block';
}

function updateBadge(type, text) {
  const badge = document.getElementById('pipeline-status-badge');
  if (!badge) return;
  badge.className = `status-badge ${type}`;
  badge.textContent = text;
}

function showError(message) {
  const subtitle = document.getElementById('pipeline-subtitle');
  if (subtitle) subtitle.textContent = `We couldn't finish this review: ${message}`;
}

function escapeHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}
