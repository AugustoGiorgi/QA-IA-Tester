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
  $('sourceSummary').innerHTML = fileChips([...$('collectionFile').files], 'Sin collection seleccionada');
}

function updateCaseSummary() {
  $('caseSummary').innerHTML = fileChips([...$('caseFile').files], 'Sin archivo de casos');
}

function renderLoading() {
  $('resultPanel').className = 'pm-empty';
  $('resultPanel').innerHTML = `
    <div class="pm-loader">
      <span class="pm-spinner"></span>
      <strong>Preparando archivos de Postman...</strong>
      <span class="pm-help">Organizando requests, casos y variables.</span>
    </div>
  `;
}

async function loadDrafts() {
  const list = $('pmDraftList');
  try {
    const res = await authFetch('/api/postman/drafts');
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || 'No se pudieron cargar los borradores.');
    const drafts = data.drafts || [];
    list.innerHTML = drafts.map(draft => `
      <article class="pm-draft-item">
        <span><strong>${escapeHtml(draft.project_name || 'Proyecto API')}</strong><br><small>${(draft.model?.endpoints || []).length} endpoints · ${(draft.model?.test_cases || []).length} casos · ${escapeHtml(new Date(draft.updated_at || draft.created_at).toLocaleString('es-AR'))}</small></span>
        <button class="pm-secondary" type="button" data-open-draft="${escapeHtml(draft.id)}">Retomar</button>
      </article>`).join('') || '<span class="pm-help">Todavia no hay proyectos guardados.</span>';
    list.querySelectorAll('[data-open-draft]').forEach(button => button.addEventListener('click', () => {
      currentDraft = drafts.find(draft => draft.id === button.dataset.openDraft) || null;
      if (!currentDraft) return;
      renderResult(currentDraft);
      document.getElementById('resultPanel').scrollIntoView({ behavior: 'smooth', block: 'start' });
      setStatus('Borrador retomado.');
    }));
  } catch (error) {
    list.innerHTML = `<span class="pm-help">${escapeHtml(error.message)}</span>`;
  }
}

function renderCaseAdjustments(addedCases, extraCases) {
  if (!addedCases.length && !extraCases.length) {
    return '<div class="pm-case-box ok"><strong>Excel de casos OK</strong><span>No se detectaron casos faltantes ni sobrantes.</span></div>';
  }
  return `
    <div class="pm-case-grid">
      <div class="pm-case-box">
        <strong>Casos agregados (${addedCases.length})</strong>
        ${addedCases.slice(0, 8).map(item => `<span>${escapeHtml(item.case || '')}</span>`).join('') || '<span>No se agregaron casos.</span>'}
      </div>
      <div class="pm-case-box warn">
        <strong>Casos sobrantes (${extraCases.length})</strong>
        ${extraCases.slice(0, 8).map(item => `<span>${escapeHtml(item.case || '')}</span>`).join('') || '<span>No se detectaron sobrantes.</span>'}
      </div>
    </div>
  `;
}

function renderResult(draft) {
  const model = draft.model || {};
  const endpointCount = (model.endpoints || []).length;
  const caseCount = (model.test_cases || []).length;
  const caseSource = model.cases_source?.name || '';
  const addedCases = model.case_adjustments?.added || [];
  const extraCases = model.case_adjustments?.extra || [];
  const adjustmentCount = addedCases.length + extraCases.length;
  const endpoints = model.endpoints || [];
  const cases = model.test_cases || [];
  const associations = model.associations || [];
  const associationFor = id => associations.find(item => item.case_id === id) || {};
  const endpointOptions = endpointId => `<option value="">Sin asociar</option>${endpoints.map(endpoint => `<option value="${escapeHtml(endpoint.id)}" ${endpoint.id === endpointId ? 'selected' : ''}>${escapeHtml(endpoint.method)} ${escapeHtml(endpoint.path)}</option>`).join('')}`;
  const pending = cases.filter(item => !item.expected_status && !/\b[1-5]\d\d\b/.test(item.expected_result || '')).length;
  $('resultPanel').className = '';
  $('resultPanel').innerHTML = `
    <h2>Borrador para revisar</h2>
    <p class="pm-help">${escapeHtml(draft.project_name || 'Proyecto API')}${caseSource ? ` · Excel: ${escapeHtml(caseSource)}` : ''}</p>
    <div class="pm-summary">
      <div class="pm-metric"><span>Endpoints</span><strong>${endpointCount}</strong></div>
      <div class="pm-metric"><span>Casos</span><strong>${caseCount}</strong></div>
      <div class="pm-metric"><span>Pendientes de expectativa</span><strong>${pending}</strong></div>
    </div>
    ${caseSource ? renderCaseAdjustments(addedCases, extraCases) : ''}
    <section class="pm-review">
      <h3>Revisar casos y asociacion</h3>
      <p class="pm-help">Cada caso exportado se convierte en una request. Corregi la asociacion y el resultado esperado antes de compartir.</p>
      <div class="pm-case-list">${cases.map((item, index) => {
        const association = associationFor(item.id);
        return `<article class="pm-review-case">
          <label class="pm-include"><input type="checkbox" data-case-include="${escapeHtml(item.id)}" ${association.included === false ? '' : 'checked'}> Incluir en la collection</label>
          <strong>${escapeHtml(item.case_id || `Caso ${index + 1}`)} · ${escapeHtml(item.name || '')}</strong>
          <span class="pm-help">${escapeHtml(item.description || 'Sin descripcion en la fuente.')}</span>
          <label>Endpoint<select data-case-endpoint="${escapeHtml(item.id)}">${endpointOptions(association.endpoint_id || item.related_request_ids?.[0])}</select></label>
          <label>Resultado esperado<textarea data-case-expected="${escapeHtml(item.id)}" rows="2" placeholder="Completar si la fuente no lo define">${escapeHtml(item.expected_result || '')}</textarea></label>
          <div class="pm-assertion-grid"><label>Status HTTP esperado<input data-case-status="${escapeHtml(item.id)}" value="${escapeHtml(item.expected_status || '')}" inputmode="numeric" maxlength="3" placeholder="200"></label><label>Body JSON esperado<input data-case-response="${escapeHtml(item.id)}" value="${escapeHtml(typeof item.expected_response === 'string' ? item.expected_response : item.expected_response ? JSON.stringify(item.expected_response) : '')}" placeholder='{"estado":"OK"}'></label><label>Body JSON de request<textarea data-case-body="${escapeHtml(item.id)}" rows="2" placeholder="Solo si corresponde">${escapeHtml(typeof item.request_body === 'string' ? item.request_body : item.request_body ? JSON.stringify(item.request_body, null, 2) : '')}</textarea></label></div>
        </article>`;
      }).join('') || '<p class="pm-help">No se detectaron casos en las fuentes. Se generaran los casos base que permita la documentacion.</p>'}</div>
      <h3>Variables y datos de ambiente</h3>
      <div class="pm-variable-list">${(model.variables || []).map(variable => `<label>${escapeHtml(variable.key)}${variable.sensitive ? ' · secreto' : ''}<input data-pm-variable="${escapeHtml(variable.key)}" ${variable.sensitive ? 'disabled' : ''} value="${escapeHtml(variable.sensitive ? '' : variable.value || '')}" placeholder="${variable.sensitive ? 'Completar directamente en Postman' : 'Valor opcional'}"></label>`).join('') || '<span class="pm-help">No se detectaron variables.</span>'}</div>
      ${(model.validation?.warnings || []).length ? `<details class="pm-review-warnings"><summary>Advertencias tecnicas (${model.validation.warnings.length})</summary><ul>${model.validation.warnings.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul></details>` : ''}
      <div class="pm-actions"><button class="pm-secondary" id="savePostmanReview" type="button">Guardar revision</button></div>
    </section>
    <div class="pm-actions">
      <button class="pm-primary" id="downloadPostmanCollection" type="button">Descargar collection.json</button>
      <button class="pm-secondary" id="downloadPostmanEnvironment" type="button">Descargar environment.json</button>
    </div>
    <section class="pm-guide">
      <h3>Como cargarlo en Postman</h3>
      <ol>
        <li>Abrir Postman.</li>
        <li>Ir a Import.</li>
        <li>En Postman, pulsar Import y seleccionar ambos archivos JSON.</li>
        <li>Elegir el environment importado en el selector de ambientes.</li>
        <li>Completar las variables vacias y los secretos en Postman.</li>
      </ol>
    </section>
  `;
  $('downloadPostmanCollection').addEventListener('click', async () => {
    if (!(await saveReview())) return;
    await downloadFile('collection');
  });
  $('downloadPostmanEnvironment').addEventListener('click', async () => {
    if (!(await saveReview())) return;
    await downloadFile('environment');
  });
  $('savePostmanReview').addEventListener('click', saveReview);
}

async function saveReview() {
  if (!currentDraft) return false;
  const model = currentDraft.model || {};
  const cases = (model.test_cases || []).map(item => ({
    ...item,
    expected_result: document.querySelector(`[data-case-expected="${CSS.escape(item.id)}"]`)?.value.trim() || '',
    expected_status: document.querySelector(`[data-case-status="${CSS.escape(item.id)}"]`)?.value.trim() || '',
    request_body: document.querySelector(`[data-case-body="${CSS.escape(item.id)}"]`)?.value.trim() || '',
    expected_response: document.querySelector(`[data-case-response="${CSS.escape(item.id)}"]`)?.value.trim() || '',
  }));
  const oldAssociations = model.associations || [];
  const associations = cases.map(item => {
    const previous = oldAssociations.find(entry => entry.case_id === item.id) || {};
    return {
      ...previous,
      case_id: item.id,
      endpoint_id: document.querySelector(`[data-case-endpoint="${CSS.escape(item.id)}"]`)?.value || '',
      included: document.querySelector(`[data-case-include="${CSS.escape(item.id)}"]`)?.checked ?? true,
      confidence: 'Confirmada por QA',
      confirmed: true,
    };
  });
  const variables = (model.variables || []).map(item => ({
    ...item,
    value: document.querySelector(`[data-pm-variable="${CSS.escape(item.key)}"]`)?.value || '',
  }));
  for (const item of cases) {
    if (item.expected_status && !/^[1-5]\d\d$/.test(item.expected_status)) {
      setStatus(`El status HTTP de ${item.case_id || item.name} debe tener tres digitos.`);
      return false;
    }
    if (item.request_body) {
      try { JSON.parse(item.request_body); } catch { setStatus(`El body de ${item.case_id || item.name} no es JSON valido.`); return false; }
    }
    if (item.expected_response) {
      try { JSON.parse(item.expected_response); } catch { setStatus(`El body esperado de ${item.case_id || item.name} no es JSON valido.`); return false; }
    }
  }
  setStatus('Guardando revision...');
  const res = await authFetch(`/api/postman/drafts/${currentDraft.id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ test_cases: cases, associations, variables }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    setStatus(data.detail || 'No se pudo guardar la revision.');
    return false;
  }
  currentDraft = data.draft;
  setStatus('Revision guardada. El archivo sigue siendo un borrador hasta que completes las expectativas pendientes.', true);
  renderResult(currentDraft);
  await loadDrafts();
  return true;
}

async function downloadFile(kind) {
  if (!currentDraft) return;
  const filenames = {
    collection: 'collection.json',
    environment: 'environment.json',
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
  const collection = $('collectionFile').files[0];
  const comments = $('manualText').value.trim();
  if (!collection) {
    setStatus('Selecciona la collection JSON de Postman.');
    return;
  }
  setStatus('Preparando collection y environment...');
  renderLoading();
  $('analyzeBtn').disabled = true;
  try {
    const fd = new FormData();
    fd.append('project_name', collection.name.replace(/\.json$/i, '').replace(/[_-]+/g, ' ').trim());
    fd.append('manual_text', comments);
    fd.append('files', collection);
    if ($('caseFile').files[0]) fd.append('test_cases_file', $('caseFile').files[0]);
    const res = await authFetch('/api/postman/analyze', { method: 'POST', body: fd });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || 'No se pudo generar la collection.');
    currentDraft = data.draft;
    setStatus('Analisis listo. Revisa asociaciones, expectativas y variables antes de descargar.', true);
    renderResult(currentDraft);
    await loadDrafts();
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
$('collectionFile').addEventListener('change', updateSourceSummary);
$('caseFile').addEventListener('change', updateCaseSummary);
loadDrafts();
