import { authFetch, requireAuth } from './auth.js?v=20261001-2';

requireAuth(['qa']);

const $ = id => document.getElementById(id);
let currentDraft = null;

function setStatus(message, ok = false) {
  const box = $('postmanStatus');
  box.textContent = message || '';
  box.classList.toggle('ok', ok);
}

function selectedFiles() {
  return [...$('sourceFiles').files];
}

function updateSourceSummary() {
  const files = selectedFiles();
  $('sourceSummary').innerHTML = files.map(file => `<span class="pm-file-chip">${/\.(xlsx|csv)$/i.test(file.name) ? 'Casos: ' : ''}${escapeHtml(file.name)}</span>`).join('') || 'Archivos todavía no seleccionados';
  currentDraft = null;
  $('resultPanel').hidden = true;
}

function escapeHtml(value = '') {
  return String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function renderResult() {
  $('resultPanel').hidden = false;
  $('downloadPostmanBundle').onclick = () => downloadFile('bundle');
  $('newPostmanCollection').onclick = startNewCollection;
  const readiness = currentDraft?.model?.readiness || {};
  const missing = readiness.missing_data || [];
  const checks = readiness.manual_checks || [];
  $('missingPanel').hidden = !missing.length && !checks.length;
  $('missingSummary').textContent = missing.length ? `${missing.length} dato${missing.length === 1 ? '' : 's'} por completar en el environment antes de ejecutar` : 'Revisiones pendientes antes de ejecutar';
  const missingLine = item => `<li><strong>${escapeHtml(item.key)}</strong> · ${escapeHtml(item.reason)}${item.context ? ` · ${escapeHtml(item.context)}` : ''}</li>`;
  $('missingList').innerHTML = missing.slice(0, 6).map(missingLine).join('');
  $('moreMissing').hidden = missing.length <= 6;
  $('moreMissingSummary').textContent = `Ver los ${missing.length - 6} datos restantes`;
  $('moreMissingList').innerHTML = missing.slice(6).map(missingLine).join('');
  $('manualDetails').hidden = !checks.length;
  $('manualList').innerHTML = checks.map(item => `<li>${escapeHtml(item)}</li>`).join('');
  $('getDataQuery').hidden = !readiness.query_request;
  $('getDataQuery').onclick = () => {
    sessionStorage.setItem('qaSqlPrefillV1', readiness.query_request);
    if (window.parent !== window) {
      window.parent.postMessage({ type: 'qa:navigate', view: 'sql-query' }, window.location.origin);
    } else {
      window.location.assign('/app/sql-query.html');
    }
  };
}

function startNewCollection() {
  currentDraft = null;
  $('sourceFiles').value = '';
  $('projectName').value = '';
  $('manualText').value = '';
  $('resultPanel').hidden = true;
  updateSourceSummary();
  setStatus('');
  $('sourceFiles').click();
}

async function downloadFile(kind) {
  if (!currentDraft) return;
  const fileName = kind === 'bundle' ? 'postman_collection_environment.zip' : kind === 'environment' ? 'environment.json' : 'collection.json';
  const res = await authFetch(`/api/postman/drafts/${currentDraft.id}/download/${kind}`);
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    setStatus(data.detail || `No se pudo descargar ${fileName}.`);
    return;
  }
  const blob = await res.blob();
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  setStatus(`${fileName} descargado.`, true);
}

async function analyze(event) {
  event.preventDefault();
  const files = selectedFiles();
  const caseFiles = files.filter(file => /\.(xlsx|csv)$/i.test(file.name));
  const sources = files.filter(file => !/\.(xlsx|csv)$/i.test(file.name));
  const unsupported = files.filter(file => !/\.(json|yaml|yml|txt|md|curl|http|docx|pdf|xlsx|csv)$/i.test(file.name));
  if (!sources.length || caseFiles.length > 1 || unsupported.length) {
    setStatus('Seleccioná al menos una fuente de endpoints y, si querés, un único Excel o CSV de casos.');
    return;
  }

  const projectName = $('projectName').value.trim();
  if (!projectName) {
    setStatus('Escribí el nombre de la collection antes de generarla.');
    $('projectName').focus();
    return;
  }
  const comments = $('manualText').value.trim();
  currentDraft = null;
  $('resultPanel').hidden = true;
  $('analyzeBtn').disabled = true;
  setStatus('Generando collection...');

  try {
    const fd = new FormData();
    fd.append('project_name', projectName);
    fd.append('manual_text', comments);
    sources.forEach(file => fd.append('files', file));
    if (caseFiles[0]) fd.append('test_cases_file', caseFiles[0]);

    const res = await authFetch('/api/postman/analyze', { method: 'POST', body: fd });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || 'No se pudo generar la collection.');
    currentDraft = data.draft;
    renderResult();
    await downloadFile('bundle');
  } catch (error) {
    setStatus(error.message || 'No se pudo generar la collection.');
  } finally {
    $('analyzeBtn').disabled = false;
  }
}

const dropzone = $('postmanDropzone');
dropzone.addEventListener('dragover', event => {
  event.preventDefault();
  dropzone.classList.add('dragover');
});
dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
dropzone.addEventListener('drop', event => {
  event.preventDefault();
  dropzone.classList.remove('dragover');
  if (!event.dataTransfer?.files?.length) return;
  const transfer = new DataTransfer();
  [...event.dataTransfer.files].forEach(file => transfer.items.add(file));
  $('sourceFiles').files = transfer.files;
  updateSourceSummary();
});

$('sourceFiles').addEventListener('change', updateSourceSummary);
$('postmanForm').addEventListener('submit', analyze);
