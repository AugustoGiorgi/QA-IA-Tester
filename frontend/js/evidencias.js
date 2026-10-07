import { authFetch, requireAuth } from './auth.js?v=20261007-1';

requireAuth(['qa']);

const $ = id => document.getElementById(id);
const state = { session: null, sessions: [], filter: 'all', query: '' };
const imageCategories = ['Antes', 'Acción', 'Final', 'Error', 'Request/response', 'General'];

function escapeHtml(value = '') {
  return String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function statusGroup(value = '') {
  const text = String(value).normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim();
  if (['ok', 'aprobado', 'aprobada', 'correcto', 'correcta', 'pass', 'passed', 'exitoso', 'exitosa'].includes(text)) return 'ok';
  if (['error', 'fallido', 'fallida', 'incorrecto', 'incorrecta', 'fail', 'failed', 'rechazado', 'rechazada'].includes(text)) return 'error';
  if (['bloqueado', 'bloqueada', 'blocked', 'bloqueo'].includes(text)) return 'blocked';
  if (['pendiente', 'pending', 'sin ejecutar', 'no ejecutado', 'no ejecutada'].includes(text)) return 'pending';
  return 'other';
}

async function request(url, options = {}) {
  const response = await authFetch(url, options);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || 'No se pudo completar la solicitud.');
  }
  return response;
}

async function jsonRequest(url, options = {}) {
  const response = await request(url, options);
  return response.json();
}

function setStartStatus(message = '', success = false) {
  const status = $('startStatus');
  status.textContent = message;
  status.classList.toggle('success', success);
}

async function loadSessions(selectId = '') {
  const data = await jsonRequest('/api/evidence/sessions');
  state.sessions = data.sessions || [];
  const select = $('savedSessions');
  select.innerHTML = '<option value="">Nueva ejecución</option>' + state.sessions.map(item => {
    const changed = item.updated_at ? new Date(item.updated_at).toLocaleString('es-AR', { dateStyle: 'short', timeStyle: 'short' }) : '';
    return `<option value="${escapeHtml(item.id)}">${escapeHtml(item.project_name)} · ${item.case_count} casos · ${escapeHtml(changed)}</option>`;
  }).join('');
  select.value = selectId || '';
}

async function openSession(id) {
  const { session } = await jsonRequest(`/api/evidence/sessions/${encodeURIComponent(id)}`);
  state.session = session;
  localStorage.setItem('qa_evidence_session_id', session.id);
  $('startView').hidden = true;
  $('sessionView').hidden = false;
  $('sessionTitle').textContent = session.project_name || 'Proyecto';
  $('sessionMeta').textContent = [session.requirement, session.environment, session.source_filename].filter(Boolean).join(' · ') || 'Sin metadatos adicionales';
  $('changeNotice').hidden = true;
  renderMetrics();
  renderCases();
}

function showNewSession() {
  state.session = null;
  localStorage.removeItem('qa_evidence_session_id');
  $('savedSessions').value = '';
  $('sessionView').hidden = true;
  $('startView').hidden = false;
  $('startForm').reset();
  $('excelName').textContent = 'Todavía no seleccionaste una planilla';
  setStartStatus('');
}

function renderMetrics() {
  const cases = state.session?.cases || [];
  const count = key => cases.filter(item => statusGroup(item.status) === key).length;
  const withImages = cases.filter(item => item.images?.length).length;
  const metrics = [
    ['Casos totales', cases.length, ''],
    ['OK', count('ok'), 'ok'],
    ['Error', count('error'), 'error'],
    ['Bloqueados', count('blocked'), 'blocked'],
    ['Con evidencia', `${withImages} / ${cases.length}`, ''],
  ];
  $('metrics').innerHTML = metrics.map(([label, value, group]) => `<div class="ev-metric" data-state="${group}"><span>${label}</span><strong>${value}</strong></div>`).join('');
  const percent = cases.length ? Math.round(withImages * 100 / cases.length) : 0;
  $('evidenceProgress').value = percent;
  $('evidenceProgressText').textContent = `${withImages} de ${cases.length} casos con evidencia`;
}

function caseMatches(item) {
  const group = statusGroup(item.status);
  if (state.filter === 'no-evidence' && item.images?.length) return false;
  if (['ok', 'error', 'blocked'].includes(state.filter) && group !== state.filter) return false;
  const needle = state.query.trim().toLocaleLowerCase('es-AR');
  return !needle || `${item.case_id} ${item.name}`.toLocaleLowerCase('es-AR').includes(needle);
}

function imageUrl(imageId) {
  return `/api/evidence/sessions/${encodeURIComponent(state.session.id)}/images/${encodeURIComponent(imageId)}`;
}

function renderImage(image, index, caseKey) {
  const url = imageUrl(image.id);
  const options = imageCategories.map(category => `<option value="${category}" ${image.category === category ? 'selected' : ''}>${category}</option>`).join('');
  return `
    <article class="ev-image-row" data-image-id="${escapeHtml(image.id)}" data-case-key="${escapeHtml(caseKey)}">
      <button class="ev-thumb-button" type="button" data-preview-src="${escapeHtml(url)}" data-preview-alt="${escapeHtml(image.title || image.filename)}" title="Ampliar imagen">
        <img src="${escapeHtml(url)}" alt="${escapeHtml(image.title || image.filename)}" loading="lazy" />
      </button>
      <div class="ev-image-fields">
        <label>Título<input type="text" maxlength="180" data-image-title value="${escapeHtml(image.title || '')}" /></label>
        <label>Tipo<select data-image-category>${options}</select></label>
        <label>Descripción<textarea maxlength="1200" data-image-caption placeholder="Descripción opcional">${escapeHtml(image.caption || '')}</textarea></label>
        <div class="ev-image-controls">
          <button class="ev-icon-button" type="button" data-image-up aria-label="Mover evidencia arriba" title="Mover arriba" ${index === 0 ? 'disabled' : ''}>↑</button>
          <button class="ev-icon-button" type="button" data-image-down aria-label="Mover evidencia abajo" title="Mover abajo" ${index === (state.session.cases.find(item => item.case_key === caseKey)?.images?.length || 0) - 1 ? 'disabled' : ''}>↓</button>
          <button class="ev-button secondary" type="button" data-suggest>✨ Sugerir con IA</button>
          <button class="ev-icon-button danger" type="button" data-image-delete aria-label="Eliminar evidencia" title="Eliminar">×</button>
        </div>
        <p class="ev-ai-disclaimer">La sugerencia se envía a OpenAI y solo propone título, tipo y descripción. No evalúa ni modifica el resultado.</p>
        <div class="ev-ai-suggestion" data-ai-suggestion hidden></div>
      </div>
    </article>`;
}

function renderCase(item) {
  const group = statusGroup(item.status);
  const sourceFields = Object.entries(item.source_fields || {});
  const fieldsMarkup = sourceFields.map(([label, value]) => `
    <div class="ev-source-field"><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value || 'No informado')}</dd></div>`).join('');
  const images = item.images || [];
  const evidencePrompt = group === 'ok'
    ? 'Adjuntá una captura que muestre el resultado observado.'
    : group === 'error'
      ? 'Adjuntá la captura del error; el estado y el detalle siguen siendo los informados en Excel.'
      : group === 'blocked'
        ? 'La evidencia es opcional para los casos bloqueados.'
        : 'Agregá evidencia cuando ayude a entender la ejecución.';
  return `
    <details class="ev-case" data-case-key="${escapeHtml(item.case_key)}">
      <summary>
        <span class="ev-case-id">${escapeHtml(item.case_id || 'Caso')}</span>
        <span class="ev-case-title">${escapeHtml(item.name || 'No informado')}</span>
        <span class="ev-case-status" data-state="${group}">${escapeHtml(item.status || 'Estado no informado')}</span>
        <span class="ev-case-evidence-count">${images.length} ${images.length === 1 ? 'imagen' : 'imágenes'}</span>
      </summary>
      <div class="ev-case-content">
        <dl class="ev-source-fields">${fieldsMarkup || '<div class="ev-source-field"><dt>Planilla</dt><dd>No informado</dd></div>'}</dl>
        <section class="ev-case-evidence">
          <div class="ev-evidence-head"><h3>Evidencias</h3><label class="ev-button secondary ev-add-image" for="images-${escapeHtml(item.case_key)}">+ Agregar capturas</label><input class="ev-image-input" id="images-${escapeHtml(item.case_key)}" type="file" accept="image/png,image/jpeg,image/webp,.png,.jpg,.jpeg,.webp" multiple data-add-images /></div>
          <div class="ev-image-drop" data-image-drop>${evidencePrompt}<br />Soltá capturas acá o usá “Agregar capturas”. PNG, JPG, JPEG y WEBP.</div>
          <div class="ev-images">${images.map((image, index) => renderImage(image, index, item.case_key)).join('') || '<p class="ev-muted ev-no-images">Todavía no hay capturas para este caso.</p>'}</div>
        </section>
      </div>
    </details>`;
}

function renderCases() {
  const filtered = (state.session?.cases || []).filter(caseMatches);
  $('caseList').innerHTML = filtered.map(renderCase).join('');
  $('emptyCases').hidden = filtered.length > 0;
  bindCaseControls();
}

function findCase(caseKey) {
  return state.session?.cases?.find(item => item.case_key === caseKey);
}

function refreshSessionView() {
  renderMetrics();
  renderCases();
}

async function uploadImages(caseKey, files) {
  const valid = [...files].filter(file => {
    const okType = /\.(png|jpe?g|webp)$/i.test(file.name) || ['image/png', 'image/jpeg', 'image/webp'].includes(file.type);
    if (!okType) window.alert(`${file.name}: formato no admitido.`);
    else if (file.size > 12 * 1024 * 1024) window.alert(`${file.name}: supera el máximo de 12 MB por imagen.`);
    return okType && file.size <= 12 * 1024 * 1024;
  });
  try {
    for (const file of valid) {
      const form = new FormData();
      form.append('file', file);
      const result = await jsonRequest(`/api/evidence/sessions/${encodeURIComponent(state.session.id)}/cases/${encodeURIComponent(caseKey)}/images`, { method: 'POST', body: form });
      const item = findCase(caseKey);
      item.images ||= [];
      item.images.push(result.image);
    }
  } finally {
    refreshSessionView();
  }
}

async function saveImage(imageRow) {
  const caseKey = imageRow.dataset.caseKey;
  const imageId = imageRow.dataset.imageId;
  const payload = {
    title: imageRow.querySelector('[data-image-title]').value,
    caption: imageRow.querySelector('[data-image-caption]').value,
    category: imageRow.querySelector('[data-image-category]').value,
  };
  const result = await jsonRequest(`/api/evidence/sessions/${encodeURIComponent(state.session.id)}/cases/${encodeURIComponent(caseKey)}/images/${encodeURIComponent(imageId)}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  });
  const image = findCase(caseKey).images.find(item => item.id === imageId);
  Object.assign(image, result.image);
}

function bindCaseControls() {
  $('caseList').querySelectorAll('[data-add-images]').forEach(input => input.addEventListener('change', async () => {
    const caseKey = input.closest('[data-case-key]').dataset.caseKey;
    try { await uploadImages(caseKey, input.files); } catch (error) { window.alert(error.message); }
  }));
  $('caseList').querySelectorAll('[data-image-drop]').forEach(zone => {
    const details = zone.closest('.ev-case');
    const input = details.querySelector('[data-add-images]');
    zone.addEventListener('dragover', event => { event.preventDefault(); zone.classList.add('dragover'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
    zone.addEventListener('drop', async event => {
      event.preventDefault();
      zone.classList.remove('dragover');
      try { await uploadImages(details.dataset.caseKey, event.dataTransfer.files); } catch (error) { window.alert(error.message); }
    });
    input.addEventListener('click', () => { input.value = ''; });
  });
  $('caseList').querySelectorAll('.ev-image-row').forEach(row => {
    row.querySelectorAll('[data-image-title], [data-image-caption], [data-image-category]').forEach(field => {
      field.addEventListener('change', () => saveImage(row).catch(error => window.alert(error.message)));
    });
    row.querySelector('[data-image-delete]').addEventListener('click', async () => {
      if (!window.confirm('¿Eliminar esta evidencia?')) return;
      try {
        await request(`/api/evidence/sessions/${encodeURIComponent(state.session.id)}/cases/${encodeURIComponent(row.dataset.caseKey)}/images/${encodeURIComponent(row.dataset.imageId)}`, { method: 'DELETE' });
        const item = findCase(row.dataset.caseKey);
        item.images = item.images.filter(image => image.id !== row.dataset.imageId);
        refreshSessionView();
      } catch (error) { window.alert(error.message); }
    });
    row.querySelector('[data-image-up]').addEventListener('click', () => moveImage(row, -1));
    row.querySelector('[data-image-down]').addEventListener('click', () => moveImage(row, 1));
    row.querySelector('[data-suggest]').addEventListener('click', () => suggestImage(row));
    row.querySelector('[data-preview-src]').addEventListener('click', event => showPreview(event.currentTarget.dataset.previewSrc, event.currentTarget.dataset.previewAlt));
  });
}

async function moveImage(row, direction) {
  const caseItem = findCase(row.dataset.caseKey);
  const images = caseItem.images;
  const index = images.findIndex(image => image.id === row.dataset.imageId);
  const target = index + direction;
  if (target < 0 || target >= images.length) return;
  [images[index], images[target]] = [images[target], images[index]];
  try {
    await jsonRequest(`/api/evidence/sessions/${encodeURIComponent(state.session.id)}/cases/${encodeURIComponent(caseItem.case_key)}/images/order`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ image_ids: images.map(image => image.id) }),
    });
    renderCases();
  } catch (error) {
    [images[index], images[target]] = [images[target], images[index]];
    window.alert(error.message);
  }
}

async function suggestImage(row) {
  const button = row.querySelector('[data-suggest]');
  button.disabled = true;
  button.textContent = 'Analizando…';
  try {
    const result = await jsonRequest(`/api/evidence/sessions/${encodeURIComponent(state.session.id)}/images/${encodeURIComponent(row.dataset.imageId)}/suggest`, { method: 'POST' });
    const suggestion = result.suggestion || {};
    const panel = row.querySelector('[data-ai-suggestion]');
    panel.innerHTML = `<span><strong>${escapeHtml(suggestion.title || 'Sugerencia')}</strong> · ${escapeHtml(suggestion.category || 'General')}<br />${escapeHtml(suggestion.caption || '')}</span><button class="ev-button secondary" type="button" data-accept-suggestion>Usar sugerencia</button>`;
    panel.hidden = false;
    panel.querySelector('[data-accept-suggestion]').addEventListener('click', async event => {
      const accept = event.currentTarget;
      accept.disabled = true;
      row.querySelector('[data-image-title]').value = suggestion.title || '';
      row.querySelector('[data-image-category]').value = imageCategories.includes(suggestion.category) ? suggestion.category : 'General';
      row.querySelector('[data-image-caption]').value = suggestion.caption || '';
      try {
        await saveImage(row);
        panel.hidden = true;
      } catch (error) {
        accept.disabled = false;
        window.alert(error.message);
      }
    });
  } catch (error) {
    window.alert(error.message);
  } finally {
    button.disabled = false;
    button.textContent = '✨ Sugerir con IA';
  }
}

function showPreview(src, alt) {
  $('previewImage').src = src;
  $('previewImage').alt = alt || 'Vista ampliada de evidencia';
  $('previewCaption').textContent = alt || '';
  $('imagePreview').showModal();
}

async function createSession(event) {
  event.preventDefault();
  const file = $('excelFile').files[0];
  if (!file) return setStartStatus('Seleccioná la planilla XLS o XLSX.');
  const form = new FormData();
  form.append('file', file);
  form.append('project_name', $('projectName').value.trim());
  form.append('requirement', $('requirement').value.trim());
  form.append('environment', $('environment').value.trim());
  $('loadWorkbook').disabled = true;
  setStartStatus('Leyendo la planilla…');
  try {
    const result = await jsonRequest('/api/evidence/sessions', { method: 'POST', body: form });
    state.session = result.session;
    await loadSessions(state.session.id);
    await openSession(state.session.id);
  } catch (error) {
    setStartStatus(error.message);
  } finally {
    $('loadWorkbook').disabled = false;
  }
}

async function replaceSpreadsheet(file) {
  if (!file || !state.session) return;
  const form = new FormData();
  form.append('file', file);
  $('replaceExcel').disabled = true;
  $('changeNotice').hidden = true;
  try {
    const result = await jsonRequest(`/api/evidence/sessions/${encodeURIComponent(state.session.id)}/spreadsheet`, { method: 'POST', body: form });
    state.session = result.session;
    const changes = result.changes;
    $('changeNotice').textContent = `Planilla actualizada. ${changes.added} casos nuevos, ${changes.removed} retirados${changes.restored ? ` y ${changes.restored} recuperados` : ''}. Las evidencias de los casos coincidentes se conservaron.`;
    $('changeNotice').hidden = false;
    await loadSessions(state.session.id);
    await openSession(state.session.id);
    $('changeNotice').textContent = `Planilla actualizada. ${changes.added} casos nuevos, ${changes.removed} retirados${changes.restored ? ` y ${changes.restored} recuperados` : ''}. Las evidencias de los casos coincidentes se conservaron.`;
    $('changeNotice').hidden = false;
  } catch (error) {
    window.alert(error.message);
  } finally {
    $('replaceExcel').disabled = false;
    $('updatedExcel').value = '';
  }
}

async function generateReport() {
  const cases = state.session?.cases || [];
  const withoutEvidence = cases.filter(item => !item.images?.length).length;
  if (withoutEvidence && !window.confirm(`${withoutEvidence} casos no tienen evidencia. El informe los marcará como “Sin evidencia gráfica adjunta”. ¿Generar igual?`)) return;
  $('generateReport').disabled = true;
  $('generateReport').textContent = 'Preparando Word…';
  try {
    const response = await request(`/api/evidence/sessions/${encodeURIComponent(state.session.id)}/report`, { method: 'POST' });
    const blob = await response.blob();
    const disposition = response.headers.get('Content-Disposition') || '';
    const filename = disposition.match(/filename="?([^";]+)"?/i)?.[1] || 'Evidencias_QA.docx';
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  } catch (error) {
    window.alert(error.message);
  } finally {
    $('generateReport').disabled = false;
    $('generateReport').textContent = 'Generar informe Word';
  }
}

function bindDropzone() {
  const zone = $('excelDrop');
  zone.addEventListener('dragover', event => { event.preventDefault(); zone.classList.add('dragover'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
  zone.addEventListener('drop', event => {
    event.preventDefault();
    zone.classList.remove('dragover');
    const file = event.dataTransfer.files[0];
    if (!file) return;
    const transfer = new DataTransfer();
    transfer.items.add(file);
    $('excelFile').files = transfer.files;
    $('excelName').textContent = file.name;
  });
}

$('excelFile').addEventListener('change', () => { $('excelName').textContent = $('excelFile').files[0]?.name || 'Todavía no seleccionaste una planilla'; });
$('startForm').addEventListener('submit', createSession);
$('newSession').addEventListener('click', showNewSession);
$('savedSessions').addEventListener('change', async event => {
  if (!event.target.value) return showNewSession();
  try { await openSession(event.target.value); } catch (error) { window.alert(error.message); }
});
$('caseFilter').addEventListener('change', event => { state.filter = event.target.value; renderCases(); });
$('caseSearch').addEventListener('input', event => { state.query = event.target.value; renderCases(); });
$('replaceExcel').addEventListener('click', () => $('updatedExcel').click());
$('updatedExcel').addEventListener('change', event => replaceSpreadsheet(event.target.files[0]));
$('generateReport').addEventListener('click', generateReport);
$('closePreview').addEventListener('click', () => $('imagePreview').close());
$('imagePreview').addEventListener('click', event => { if (event.target === $('imagePreview')) $('imagePreview').close(); });
bindDropzone();

try {
  const lastSession = localStorage.getItem('qa_evidence_session_id');
  await loadSessions();
  if (lastSession && state.sessions.some(item => item.id === lastSession)) await openSession(lastSession);
  else showNewSession();
} catch (error) {
  setStartStatus(error.message);
}
