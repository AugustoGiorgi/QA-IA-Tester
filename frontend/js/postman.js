import { authFetch, requireAuth } from './auth.js';

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
  const collection = files.find(file => file.name.toLowerCase().endsWith('.json'));
  const cases = files.find(file => /\.(xlsx|csv)$/i.test(file.name));
  const parts = [];
  if (collection) parts.push(`<span class="pm-file-chip">Collection: ${escapeHtml(collection.name)}</span>`);
  if (cases) parts.push(`<span class="pm-file-chip">Casos: ${escapeHtml(cases.name)}</span>`);
  $('sourceSummary').innerHTML = parts.join('') || 'Collection y casos todavía no seleccionados';
  $('resultPanel').hidden = true;
}

function escapeHtml(value = '') {
  return String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function renderResult() {
  $('resultPanel').hidden = false;
  $('downloadPostmanCollection').onclick = () => downloadFile('collection');
  $('downloadPostmanEnvironment').onclick = () => downloadFile('environment');
  $('newPostmanCollection').onclick = startNewCollection;
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
  const fileName = kind === 'environment' ? 'environment.json' : 'collection.json';
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
  const collections = files.filter(file => file.name.toLowerCase().endsWith('.json'));
  const caseFiles = files.filter(file => /\.(xlsx|csv)$/i.test(file.name));
  const unsupported = files.filter(file => !file.name.toLowerCase().endsWith('.json') && !/\.(xlsx|csv)$/i.test(file.name));
  if (collections.length !== 1 || caseFiles.length > 1 || unsupported.length) {
    setStatus('Seleccioná una collection JSON y, si hace falta, un único Excel o CSV de casos.');
    return;
  }

  const collection = collections[0];
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
    fd.append('files', collection);
    if (caseFiles[0]) fd.append('test_cases_file', caseFiles[0]);

    const res = await authFetch('/api/postman/analyze', { method: 'POST', body: fd });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || 'No se pudo generar la collection.');
    currentDraft = data.draft;
    renderResult();
    await downloadFile('collection');
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
