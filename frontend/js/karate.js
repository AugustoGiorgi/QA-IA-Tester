import { authFetch, requireAuth } from './auth.js?v=20261001-2';

requireAuth(['qa']);

const $ = id => document.getElementById(id);
let currentDraft = null;

function esc(value = '') {
  return String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function status(message, ok = false) {
  $('kStatus').textContent = message || '';
  $('kStatus').classList.toggle('ok', ok);
}

async function loadDrafts() {
  const list = $('kDrafts');
  try {
    const response = await authFetch('/api/karate/drafts');
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || 'No se pudieron cargar los proyectos.');
    const drafts = data.drafts || [];
    list.innerHTML = drafts.map(draft => `<article class="k-draft"><span><strong>${esc(draft.project_name || 'Proyecto API')}</strong><br><small>${(draft.model?.endpoints || []).length} endpoints · ${(draft.model?.test_cases || []).length} casos</small></span><button class="k-btn k-secondary" type="button" data-k-open="${esc(draft.id)}">Retomar</button></article>`).join('') || '<span class="k-muted">Todavia no hay proyectos guardados.</span>';
    list.querySelectorAll('[data-k-open]').forEach(button => button.addEventListener('click', () => {
      const draft = drafts.find(item => item.id === button.dataset.kOpen);
      if (!draft) return;
      $('kProject').value = draft.project_name || '';
      render(draft);
      status('Borrador retomado.', true);
      $('kResult').scrollIntoView({ behavior: 'smooth', block: 'start' });
    }));
  } catch (error) {
    list.innerHTML = `<span class="k-muted">${esc(error.message)}</span>`;
  }
}

function render(draft) {
  currentDraft = draft;
  const model = draft.model || {};
  const endpoints = model.endpoints || [];
  const cases = model.test_cases || [];
  const associations = model.associations || [];
  const associationFor = id => associations.find(item => item.case_id === id) || {};
  const endpointOptions = selected => `<option value="">Sin endpoint asociado</option>${endpoints.map(endpoint => `<option value="${esc(endpoint.id)}" ${endpoint.id === selected ? 'selected' : ''}>${esc(endpoint.method)} ${esc(endpoint.path)}</option>`).join('')}`;
  const pending = cases.filter(item => !item.expected_status && !/\b[1-5]\d\d\b/.test(item.expected_result || '')).length;
  $('kResult').className = '';
  $('kResult').innerHTML = `
    <h2>${esc(draft.project_name || 'Proyecto API')}</h2>
    <div class="k-metrics"><div class="k-metric"><span>Endpoints detectados</span><strong>${endpoints.length}</strong></div><div class="k-metric"><span>Casos analizados</span><strong>${cases.length}</strong></div><div class="k-metric"><span>Expectativas pendientes</span><strong>${pending}</strong></div></div>
    <div class="k-state">Borrador no ejecutado. Los escenarios sin datos o expectativa confirmada quedaran omitidos (@ignore) hasta completar la revision.</div>
    <h3>Casos y asociacion</h3><div class="k-case-list">${cases.map((item, index) => {
      const assoc = associationFor(item.id);
      let body = item.request_body || '';
      if (!body && assoc.endpoint_id) body = endpoints.find(endpoint => endpoint.id === assoc.endpoint_id)?.body?.raw || '';
      return `<article class="k-case">
        <strong>${esc(item.case_id || `Caso ${index + 1}`)} · ${esc(item.name || '')}</strong>
        <p>${esc(item.description || 'Caso base propuesto desde el endpoint; revisar con QA.')}</p>
        <label class="k-inline"><input type="checkbox" data-case-include="${esc(item.id)}" ${assoc.included === false ? '' : 'checked'}> Incluir escenario</label>
        <label>Endpoint<select data-case-endpoint="${esc(item.id)}">${endpointOptions(assoc.endpoint_id || item.related_request_ids?.[0])}</select></label>
        <label>Status esperado<input data-case-status="${esc(item.id)}" inputmode="numeric" maxlength="3" placeholder="200" value="${esc(item.expected_status || '')}"></label>
        <label>Respuesta JSON esperada<textarea data-case-response="${esc(item.id)}" placeholder='{"estado":"OK"}'>${esc(typeof item.expected_response === 'string' ? item.expected_response : item.expected_response ? JSON.stringify(item.expected_response, null, 2) : '')}</textarea></label>
        <label>Body JSON de request<textarea data-case-body="${esc(item.id)}" placeholder="Dejar vacio si no corresponde">${esc(typeof body === 'string' ? body : JSON.stringify(body, null, 2))}</textarea></label>
      </article>`;
    }).join('') || '<p class="k-muted">No se detectaron casos. Verifica que los documentos incluyan endpoints o escenarios.</p>'}</div>
    <h3>Variables de ambiente</h3><div class="k-var-list">${(model.variables || []).map(variable => `<label>${esc(variable.key)}${variable.sensitive ? ' · secreto' : ''}<input data-k-variable="${esc(variable.key)}" ${variable.sensitive ? 'disabled' : ''} value="${esc(variable.sensitive ? '' : variable.value || '')}" placeholder="${variable.sensitive ? 'Se toma de una variable local de entorno' : 'Valor opcional'}"></label>`).join('') || '<span class="k-muted">Base URL y token se pueden definir al ejecutar.</span>'}</div>
    ${(model.warnings || []).length ? `<details class="k-warnings"><summary>Advertencias (${model.warnings.length})</summary><ul>${model.warnings.map(item => `<li>${esc(item.message || '')}</li>`).join('')}</ul></details>` : ''}
    <div class="k-actions"><button id="kSave" class="k-btn k-secondary" type="button">Guardar revision</button><button id="kDownload" class="k-btn" type="button">Descargar proyecto ZIP</button></div>
    <section class="k-panel" style="margin-top:18px"><h3>Al abrir el proyecto</h3><ol><li>Instala Java 11+ y Maven.</li><li>Abre una terminal en la carpeta y ejecuta <code>mvn test</code>.</li><li>Define <code>baseUrl</code> y <code>authToken</code> como propiedades de ambiente.</li><li>Revisa el reporte Maven; escenarios ignorados requieren completar datos en esta pantalla.</li></ol></section>
  `;
  $('kSave').addEventListener('click', saveReview);
  $('kDownload').addEventListener('click', downloadProject);
}

async function analyze(event) {
  event.preventDefault();
  if (!$('kSources').files.length && !$('kCases').files.length && !$('kNotes').value.trim()) {
    status('Carga documentos, un Excel de casos o agrega contexto.');
    return;
  }
  $('kGenerate').disabled = true;
  $('kResult').className = 'k-empty';
  $('kResult').textContent = 'Analizando fuentes y preparando los escenarios...';
  status('Analizando documentos...');
  try {
    const form = new FormData();
    form.append('project_name', $('kProject').value.trim());
    form.append('manual_text', $('kNotes').value.trim());
    [...$('kSources').files].forEach(file => form.append('files', file));
    if ($('kCases').files[0]) form.append('cases_file', $('kCases').files[0]);
    const response = await authFetch('/api/karate/generate', { method: 'POST', body: form });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || 'No se pudo analizar las fuentes.');
    render(data.draft);
    status('Borrador preparado. Confirma asociaciones, expectativas y variables.', true);
    await loadDrafts();
  } catch (error) {
    $('kResult').className = 'k-empty';
    $('kResult').textContent = error.message || 'Error al analizar.';
    status(error.message || 'Error al analizar.');
  } finally {
    $('kGenerate').disabled = false;
  }
}

function collectReview() {
  const model = currentDraft.model || {};
  const cases = (model.test_cases || []).map(item => {
    const expectedStatus = document.querySelector(`[data-case-status="${CSS.escape(item.id)}"]`)?.value.trim() || '';
    if (expectedStatus && !/^[1-5]\d\d$/.test(expectedStatus)) {
      throw new Error(`El status esperado de ${item.case_id || item.name} debe estar entre 100 y 599.`);
    }
    const bodyText = document.querySelector(`[data-case-body="${CSS.escape(item.id)}"]`)?.value.trim() || '';
    let requestBody = '';
    if (bodyText) {
      try { JSON.parse(bodyText); requestBody = bodyText; }
      catch { throw new Error(`El body de ${item.case_id || item.name} no es JSON valido.`); }
    }
    const responseText = document.querySelector(`[data-case-response="${CSS.escape(item.id)}"]`)?.value.trim() || '';
    let expectedResponse = '';
    if (responseText) {
      try { JSON.parse(responseText); expectedResponse = responseText; }
      catch { throw new Error(`La respuesta esperada de ${item.case_id || item.name} no es JSON valido.`); }
    }
    return {
      ...item,
      expected_status: expectedStatus,
      request_body: requestBody,
      expected_response: expectedResponse,
    };
  });
  const oldAssociations = model.associations || [];
  const associations = cases.map(item => ({
    ...(oldAssociations.find(entry => entry.case_id === item.id) || {}),
    case_id: item.id,
    endpoint_id: document.querySelector(`[data-case-endpoint="${CSS.escape(item.id)}"]`)?.value || '',
    included: document.querySelector(`[data-case-include="${CSS.escape(item.id)}"]`)?.checked ?? true,
    confirmed: true,
    confidence: 'Confirmada por QA',
  }));
  const variables = (model.variables || []).map(item => ({
    ...item,
    value: item.sensitive ? '' : (document.querySelector(`[data-k-variable="${CSS.escape(item.key)}"]`)?.value || ''),
  }));
  return { test_cases: cases, associations, variables };
}

async function saveReview() {
  if (!currentDraft) return false;
  try {
    const response = await authFetch(`/api/karate/drafts/${currentDraft.id}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(collectReview()),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || 'No se pudo guardar la revision.');
    render(data.draft);
    status('Revision guardada. Los casos sin status esperado quedan pendientes.', true);
    await loadDrafts();
    return true;
  } catch (error) {
    status(error.message || 'No se pudo guardar.');
    return false;
  }
}

async function downloadProject() {
  if (!(await saveReview())) return;
  const response = await authFetch(`/api/karate/drafts/${currentDraft.id}/download`);
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    status(data.detail || 'No se pudo descargar el proyecto.');
    return;
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `${(currentDraft.project_name || 'karate-api').toLowerCase().replace(/[^a-z0-9]+/g, '-')}.zip`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  status('Proyecto descargado. La ejecucion queda a cargo del QA en su ambiente.', true);
}

$('karateForm').addEventListener('submit', analyze);
loadDrafts();
