import { authFetch, requireAuth } from './auth.js?v=20261001-2';

requireAuth(['qa']);

const $ = id => document.getElementById(id);
let currentDraft = null;

function escapeHtml(value = '') {
  return String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function status(message, ok = false) {
  $('kStatus').textContent = message || '';
  $('kStatus').classList.toggle('ok', ok);
}

function selectedFiles() {
  return [...$('kSources').files];
}

function updateFiles() {
  $('kFiles').innerHTML = selectedFiles().map(file => `<span class="k-file">${/\.(xlsx|csv)$/i.test(file.name) ? 'Casos: ' : ''}${escapeHtml(file.name)}</span>`).join('') || 'Archivos todavía no seleccionados';
  currentDraft = null;
  $('kResult').hidden = true;
}

function renderResult() {
  const readiness = currentDraft?.model?.readiness || {};
  const missing = readiness.missing_data || [];
  const checks = readiness.manual_checks || [];
  $('kResult').hidden = false;
  $('kPending').hidden = !missing.length && !checks.length;
  $('kMissingSummary').textContent = missing.length ? `${missing.length} dato${missing.length === 1 ? '' : 's'} por completar antes de ejecutar` : 'Revisiones pendientes antes de ejecutar';
  const item = entry => `<li><strong>${escapeHtml(entry.key)}</strong> · ${escapeHtml(entry.reason)}${entry.context ? ` · ${escapeHtml(entry.context)}` : ''}</li>`;
  $('kMissingList').innerHTML = missing.slice(0, 6).map(item).join('');
  $('kMoreMissing').hidden = missing.length <= 6;
  $('kMoreMissingSummary').textContent = `Ver los ${missing.length - 6} datos restantes`;
  $('kMoreMissingList').innerHTML = missing.slice(6).map(item).join('');
  $('kManual').hidden = !checks.length;
  $('kManualList').innerHTML = checks.map(check => `<li>${escapeHtml(check)}</li>`).join('');
  $('kQuery').hidden = !readiness.query_request;
  $('kQuery').onclick = () => {
    sessionStorage.setItem('qaSqlPrefillV1', readiness.query_request);
    if (window.parent !== window) {
      window.parent.postMessage({ type: 'qa:navigate', view: 'sql-query' }, window.location.origin);
    } else {
      window.location.assign('/app/sql-query.html');
    }
  };
}

async function downloadProject() {
  if (!currentDraft) return;
  try {
    const response = await authFetch(`/api/karate/drafts/${currentDraft.id}/download`);
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || 'No se pudo descargar el proyecto.');
    }
    const blob = await response.blob();
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `${(currentDraft.project_name || 'karate-api').toLowerCase().replace(/[^a-z0-9]+/g, '-')}.zip`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    status('Proyecto ZIP descargado.', true);
  } catch (error) {
    status(error.message || 'No se pudo descargar el proyecto.');
  }
}

async function generate(event) {
  event.preventDefault();
  const files = selectedFiles();
  const cases = files.filter(file => /\.(xlsx|csv)$/i.test(file.name));
  const sources = files.filter(file => !/\.(xlsx|csv)$/i.test(file.name));
  const unsupported = files.filter(file => !/\.(json|yaml|yml|txt|md|curl|http|docx|pdf|xlsx|csv)$/i.test(file.name));
  if (!sources.length || cases.length > 1 || unsupported.length) {
    status('Seleccioná al menos una fuente de endpoints y, si querés, un único Excel o CSV de casos.');
    return;
  }
  const name = $('kProject').value.trim();
  if (!name) {
    status('Escribí el nombre del proyecto.');
    $('kProject').focus();
    return;
  }
  currentDraft = null;
  $('kResult').hidden = true;
  $('kGenerate').disabled = true;
  status('Generando proyecto Karate...');
  try {
    const form = new FormData();
    form.append('project_name', name);
    form.append('manual_text', $('kNotes').value.trim());
    sources.forEach(file => form.append('files', file));
    if (cases[0]) form.append('cases_file', cases[0]);
    const response = await authFetch('/api/karate/generate', { method: 'POST', body: form });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || 'No se pudo generar el proyecto.');
    currentDraft = data.draft;
    renderResult();
    await downloadProject();
  } catch (error) {
    status(error.message || 'No se pudo generar el proyecto.');
  } finally {
    $('kGenerate').disabled = false;
  }
}

function newProject() {
  currentDraft = null;
  $('kSources').value = '';
  $('kProject').value = '';
  $('kNotes').value = '';
  updateFiles();
  status('');
  $('kSources').click();
}

const dropzone = $('kDropzone');
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
  $('kSources').files = transfer.files;
  updateFiles();
});

$('kSources').addEventListener('change', updateFiles);
$('karateForm').addEventListener('submit', generate);
$('kDownload').addEventListener('click', downloadProject);
$('kNew').addEventListener('click', newProject);
