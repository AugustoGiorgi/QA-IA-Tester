import { authFetch, requireAuth } from './auth.js?v=20261005-1';

requireAuth(['qa']);

const $ = id => document.getElementById(id);
let selectedFiles = [];
let currentDraft = null;

function escapeHtml(value = '') {
  return String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function errorMessage(data, fallback) {
  if (typeof data?.detail === 'string') return data.detail;
  if (Array.isArray(data?.detail)) return data.detail.map(item => item?.msg || '').filter(Boolean).join(' ');
  return fallback;
}

function setFiles(files) {
  const allowed = /\.(docx|pdf|xlsx|csv|txt|md|json|yaml|yml|mp4|webm|mov|mkv)$/i;
  const count = [...files].length;
  selectedFiles = [...files].filter(file => allowed.test(file.name)).slice(0, 8);
  $('filesInput').value = '';
  $('fileList').innerHTML = selectedFiles.length
    ? selectedFiles.map(file => `<span class="pw-file">${escapeHtml(file.name)}</span>`).join('')
    : '<span class="pw-muted">Todavía no seleccionaste archivos</span>';
  if (count > 8) $('formStatus').textContent = 'Se tomaron los primeros 8 archivos.';
}

function countPending() {
  const selectors = [...document.querySelectorAll('[data-kind="selector"]')];
  const data = [...document.querySelectorAll('[data-kind="data"]')];
  return [...selectors, ...data].filter(input => !input.value.trim() || /TODO|completar|pendiente/i.test(input.value)).length
    + (currentDraft?.manual_checks?.length || 0);
}

function renderVariables(draft) {
  const selectorEntries = Object.entries(draft.selectors || {});
  const dataEntries = Object.entries(draft.test_data || {});
  const group = (title, kind, entries) => `
    <details ${entries.some(([, value]) => !value || /TODO|completar|pendiente/i.test(String(value))) ? 'open' : ''}>
      <summary>${title} (${entries.length})</summary>
      <div class="pw-variable-list">${entries.length ? entries.map(([key, value]) => `
        <div class="pw-variable"><label for="var-${kind}-${escapeHtml(key)}">${escapeHtml(key)}</label>
          <input id="var-${kind}-${escapeHtml(key)}" data-kind="${kind}" data-key="${escapeHtml(key)}" class="${!value || /TODO|completar|pendiente/i.test(String(value)) ? 'todo' : ''}" value="${escapeHtml(value)}" autocomplete="off" />
        </div>`).join('') : '<span class="pw-muted">No se detectaron variables.</span>'}</div>
    </details>`;
  $('variablesSection').innerHTML = `${group('Selectores a confirmar', 'selector', selectorEntries)}${group('Datos de prueba', 'data', dataEntries)}`;
  $('variablesSection').querySelectorAll('input').forEach(input => input.addEventListener('input', () => {
    input.classList.toggle('todo', !input.value.trim() || /TODO|completar|pendiente/i.test(input.value));
    updatePendingCount();
  }));
}

function updatePendingCount() {
  const count = countPending();
  $('pendingCount').textContent = count;
  $('resultNote').textContent = count
    ? 'Completá los campos pendientes antes de usar el borrador. Los selectores no presentes en las fuentes quedan marcados para revisar.'
    : 'Variables completas. Revisá la trazabilidad y validá el borrador en el ambiente real antes de ejecutarlo.';
}

function renderDraft(draft) {
  currentDraft = draft;
  $('result').classList.remove('pw-hidden');
  $('resultTitle').textContent = draft.project_name || 'Proyecto generado';
  $('caseCount').textContent = draft.cases?.length || 0;
  $('caseList').innerHTML = (draft.cases || []).map(item => `
    <li class="pw-case"><span class="pw-case-id">${escapeHtml(item.id)}</span><div><div class="pw-case-title">${escapeHtml(item.title)}</div><div class="pw-case-source">${escapeHtml(item.source_reference || 'Origen no identificado')} · ${item.steps?.length || 0} pasos</div></div></li>
  `).join('');
  renderVariables(draft);
  const pending = draft.manual_checks || [];
  $('manualSection').innerHTML = pending.length
    ? `<details open><summary>Revisión manual (${pending.length})</summary><ul class="pw-pending-list">${pending.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul></details>`
    : '<p class="pw-muted">No se detectaron dudas adicionales. Igual validá selectores y flujo contra la aplicación real.</p>';
  $('downloadStatus').textContent = '';
  updatePendingCount();
  $('result').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function generate(event) {
  event.preventDefault();
  $('formStatus').className = 'pw-status';
  if (!selectedFiles.length) {
    $('formStatus').textContent = 'Seleccioná al menos un documento o video.';
    return;
  }
  const button = $('generateButton');
  button.disabled = true;
  button.textContent = 'Analizando fuentes…';
  $('formStatus').textContent = 'La IA está identificando casos y pendientes.';
  $('result').classList.add('pw-hidden');
  try {
    const form = new FormData();
    form.append('project_name', $('projectName').value.trim());
    form.append('initial_url', $('initialUrl').value.trim());
    form.append('comments', $('comments').value.trim());
    selectedFiles.forEach(file => form.append('files', file));
    const response = await authFetch('/api/playwright/generate', { method: 'POST', body: form });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(errorMessage(data, 'No se pudo generar el proyecto.'));
    renderDraft(data.draft);
    $('formStatus').className = 'pw-status ok';
    $('formStatus').textContent = `Listo: ${data.draft.cases.length} caso(s) preparados para revisar.`;
  } catch (error) {
    $('formStatus').textContent = error.name === 'AbortError' ? 'Se canceló la espera. El servidor podría seguir procesando.' : (error.message || 'Error al generar.');
  } finally {
    button.disabled = false;
    button.textContent = 'Generar proyecto';
  }
}

async function download() {
  if (!currentDraft) return;
  const button = $('downloadButton');
  button.disabled = true;
  $('downloadStatus').textContent = 'Preparando ZIP…';
  try {
    const selectors = Object.fromEntries([...document.querySelectorAll('[data-kind="selector"]')].map(input => [input.dataset.key, input.value]));
    const testData = Object.fromEntries([...document.querySelectorAll('[data-kind="data"]')].map(input => [input.dataset.key, input.value]));
    const response = await authFetch('/api/playwright/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...currentDraft, selectors, test_data: testData }),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(errorMessage(data, 'No se pudo preparar el ZIP.'));
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `${(currentDraft.project_name || 'playwright').toLowerCase().replace(/[^a-z0-9]+/g, '-')}-playwright.zip`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    $('downloadStatus').className = 'pw-status ok';
    $('downloadStatus').textContent = 'Proyecto descargado.';
  } catch (error) {
    $('downloadStatus').textContent = error.message || 'No se pudo descargar el proyecto.';
  } finally {
    button.disabled = false;
  }
}

function newProject() {
  currentDraft = null;
  selectedFiles = [];
  $('pwForm').reset();
  $('fileList').innerHTML = '<span class="pw-muted">Todavía no seleccionaste archivos</span>';
  $('formStatus').textContent = '';
  $('result').classList.add('pw-hidden');
  $('pwForm').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

$('pwForm').addEventListener('submit', generate);
$('downloadButton').addEventListener('click', download);
$('newButton').addEventListener('click', newProject);
$('filesInput').addEventListener('change', event => setFiles(event.target.files));
['dragenter', 'dragover'].forEach(name => $('dropZone').addEventListener(name, event => {
  event.preventDefault();
  $('dropZone').classList.add('dragging');
}));
['dragleave', 'drop'].forEach(name => $('dropZone').addEventListener(name, event => {
  event.preventDefault();
  $('dropZone').classList.remove('dragging');
  if (name === 'drop') setFiles(event.dataTransfer.files);
}));
