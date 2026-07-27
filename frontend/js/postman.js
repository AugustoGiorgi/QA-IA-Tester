import { authFetch, requireAuth } from './auth.js';

requireAuth(['qa']);

const $ = id => document.getElementById(id);
let currentDraft = null;

function escapeHtml(value = '') {
  return String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function setStatus(message, ok = false) {
  const box = $('postmanStatus');
  box.textContent = message || '';
  box.classList.toggle('ok', ok);
}

function fileChips(files, emptyText) {
  if (!files.length) return emptyText;
  const visible = files.slice(0, 4).map(file => `<span class="pm-file-chip">${escapeHtml(file.name)}</span>`);
  const rest = files.length - visible.length;
  return `${visible.join('')}${rest > 0 ? `<span class="pm-file-chip">+${rest} archivos</span>` : ''}`;
}

function updateSourceSummary() {
  $('sourceSummary').innerHTML = fileChips([...$('sourceFiles').files], 'Sin archivos seleccionados');
}

function updateCaseSummary() {
  $('caseSummary').innerHTML = fileChips([...$('caseFile').files], 'Sin Excel cargado');
}

function renderLoading() {
  $('resultPanel').className = 'pm-empty';
  $('resultPanel').innerHTML = `
    <div class="pm-loader">
      <span class="pm-spinner"></span>
      <strong>Generando collection...</strong>
      <span class="pm-help">Analizando endpoints, casos, variables y archivos cargados.</span>
    </div>
  `;
}

function warningItems(model) {
  return [
    ...(model.warnings || []).map(item => item.message || ''),
    ...(model.conflicts || []).map(item => item.message || ''),
    ...((model.validation || {}).warnings || []),
    ...((model.validation || {}).errors || []),
  ].filter(Boolean);
}

function renderResult(draft) {
  const model = draft.model || {};
  const endpointCount = (model.endpoints || []).length;
  const caseCount = (model.test_cases || []).length;
  const warnings = warningItems(model);
  const caseSource = model.cases_source?.name || '';
  $('resultPanel').className = '';
  $('resultPanel').innerHTML = `
    <h2>Collection generada</h2>
    <p class="pm-help">${escapeHtml(draft.project_name || 'Proyecto API')}${caseSource ? ` · Excel analizado: ${escapeHtml(caseSource)}` : ''}</p>
    <div class="pm-summary">
      <div class="pm-metric"><span>Endpoints</span><strong>${endpointCount}</strong></div>
      <div class="pm-metric"><span>Casos</span><strong>${caseCount}</strong></div>
      <div class="pm-metric"><span>Alertas</span><strong>${warnings.length}</strong></div>
    </div>
    <div class="pm-actions">
      <button class="pm-primary" type="button" data-download="collection">Descargar collection</button>
    </div>
    <section class="pm-guide">
      <h3>Como cargarlo en Postman</h3>
      <ol>
        <li>Abrir Postman.</li>
        <li>Ir a Import.</li>
        <li>Seleccionar el archivo collection descargado.</li>
        <li>Completar variables vacias antes de ejecutar.</li>
      </ol>
    </section>
    ${warnings.length ? `
      <div class="pm-warning-list">
        ${warnings.slice(0, 8).map(item => `<div class="pm-warning">${escapeHtml(item)}</div>`).join('')}
      </div>
    ` : ''}
  `;
  document.querySelectorAll('[data-download]').forEach(button => {
    button.addEventListener('click', () => downloadFile(button.dataset.download));
  });
}

async function downloadFile(kind) {
  if (!currentDraft) return;
  const filenames = {
    collection: 'collection.json',
  };
  setStatus('Preparando descarga...');
  const res = await authFetch(`/api/postman/drafts/${currentDraft.id}/download/${kind}`);
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    setStatus(data.detail || 'No se pudo descargar.');
    return;
  }
  const blob = await res.blob();
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = filenames[kind] || 'postman_file';
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(link.href);
  setStatus('Descarga generada.', true);
}

async function analyze(event) {
  event.preventDefault();
  const hasSources = $('sourceFiles').files.length > 0;
  const comments = $('manualText').value.trim();
  if (!hasSources && !comments) {
    setStatus('Carga archivos principales o agrega comentarios con endpoints.');
    return;
  }
  setStatus('Generando collection...');
  renderLoading();
  $('analyzeBtn').disabled = true;
  try {
    const fd = new FormData();
    fd.append('project_name', $('projectName').value.trim());
    fd.append('manual_text', comments);
    [...$('sourceFiles').files].forEach(file => fd.append('files', file));
    if ($('caseFile').files[0]) fd.append('test_cases_file', $('caseFile').files[0]);
    const res = await authFetch('/api/postman/analyze', { method: 'POST', body: fd });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || 'No se pudo generar la collection.');
    currentDraft = data.draft;
    setStatus('Collection lista para descargar.', true);
    renderResult(currentDraft);
  } catch (err) {
    currentDraft = null;
    $('resultPanel').className = 'pm-empty';
    $('resultPanel').innerHTML = '<p>No se pudo generar la collection.</p>';
    setStatus(err.message || 'Error al generar.');
  } finally {
    $('analyzeBtn').disabled = false;
  }
}

$('postmanForm').addEventListener('submit', analyze);
$('sourceFiles').addEventListener('change', updateSourceSummary);
$('caseFile').addEventListener('change', updateCaseSummary);
