import { authFetch, requireAuth } from './auth.js';

requireAuth(['qa']);

const $ = id => document.getElementById(id);
let currentDraft = null;
let activeTab = 'endpoints';

function escapeHtml(value = '') {
  return String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function setStatus(message, ok = false) {
  const box = $('postmanStatus');
  box.textContent = message || '';
  box.classList.toggle('ok', ok);
}

function model() {
  return currentDraft?.model || {};
}

function renderAll() {
  renderSummary();
  renderTab(activeTab);
}

function renderSummary() {
  const m = model();
  $('summary').innerHTML = `
    <div class="pm-metric"><span>Endpoints</span><strong>${(m.endpoints || []).length}</strong></div>
    <div class="pm-metric"><span>Casos</span><strong>${(m.test_cases || []).length}</strong></div>
    <div class="pm-metric"><span>Asociaciones</span><strong>${(m.associations || []).filter(a => a.endpoint_id).length}</strong></div>
    <div class="pm-metric"><span>Advertencias</span><strong>${(m.warnings || []).length + (m.conflicts || []).length}</strong></div>
  `;
}

function renderTab(tab) {
  activeTab = tab;
  document.querySelectorAll('[data-tab]').forEach(btn => btn.classList.toggle('active', btn.dataset.tab === tab));
  const content = $('tabContent');
  const m = model();
  if (!currentDraft) {
    content.innerHTML = '<div class="pm-empty"><p>Carga fuentes para iniciar el analisis.</p></div>';
    return;
  }
  if (tab === 'endpoints') content.innerHTML = renderEndpoints(m.endpoints || []);
  if (tab === 'cases') content.innerHTML = renderCases(m.test_cases || []);
  if (tab === 'trace') content.innerHTML = renderTrace(m.associations || [], m.test_cases || [], m.endpoints || []);
  if (tab === 'variables') content.innerHTML = renderVariables(m.variables || []);
  if (tab === 'warnings') content.innerHTML = renderWarnings(m);
  if (tab === 'downloads') content.innerHTML = renderDownloads(currentDraft);
  wireTab(tab);
}

function renderEndpoints(endpoints) {
  return endpoints.map((endpoint, index) => `
    <article class="pm-card ${endpoint.status === 'blocked' ? 'bad' : ''}" data-endpoint-index="${index}">
      <div class="pm-card-head">
        <div>
          <strong>${escapeHtml(endpoint.name || 'Request sin nombre')}</strong>
          <p class="small muted"><code>${escapeHtml(endpoint.method)}</code> ${escapeHtml(endpoint.base_url || '')}${escapeHtml(endpoint.path || '')}</p>
        </div>
        <span class="small muted">${escapeHtml(endpoint.source_refs?.[0]?.source || '')}</span>
      </div>
      <div class="pm-editor three">
        <label>Metodo<select data-field="method">${['GET','POST','PUT','PATCH','DELETE','HEAD','OPTIONS'].map(m => `<option ${m === endpoint.method ? 'selected' : ''}>${m}</option>`).join('')}</select></label>
        <label>Base URL<input data-field="base_url" value="${escapeHtml(endpoint.base_url || '')}" /></label>
        <label>Path<input data-field="path" value="${escapeHtml(endpoint.path || '')}" /></label>
      </div>
      <div class="pm-editor two">
        <label>Nombre<input data-field="name" value="${escapeHtml(endpoint.name || '')}" /></label>
        <label>Estado<select data-field="status">${['active','stand-by','blocked','disabled'].map(v => `<option value="${v}" ${v === (endpoint.status || 'active') ? 'selected' : ''}>${v}</option>`).join('')}</select></label>
      </div>
      <label>Descripcion<textarea data-field="description" rows="2">${escapeHtml(endpoint.description || '')}</textarea></label>
    </article>
  `).join('') || '<div class="pm-empty"><p>Sin endpoints detectados.</p></div>';
}

function renderCases(cases) {
  return cases.map(testCase => `
    <article class="pm-card">
      <strong>${escapeHtml(testCase.case_id)} - ${escapeHtml(testCase.name)}</strong>
      <p class="small muted">${escapeHtml(testCase.test_type || 'funcional')} · ${escapeHtml(testCase.source_refs?.[0]?.source || '')}</p>
      <p>${escapeHtml((testCase.description || '').slice(0, 480))}</p>
    </article>
  `).join('') || '<div class="pm-empty"><p>Sin casos detectados.</p></div>';
}

function renderTrace(associations, cases, endpoints) {
  const endpointOptions = ['<option value="">Sin coincidencia</option>'].concat(endpoints.map(endpoint => (
    `<option value="${escapeHtml(endpoint.id)}">${escapeHtml(endpoint.method)} ${escapeHtml(endpoint.path || endpoint.name)}</option>`
  ))).join('');
  const caseById = Object.fromEntries(cases.map(item => [item.id, item]));
  return `
    <div class="pm-table-wrap">
      <table class="pm-table">
        <thead><tr><th>Caso</th><th>Request asociado</th><th>Confianza</th><th>Evidencia</th></tr></thead>
        <tbody>
          ${associations.map((assoc, index) => `
            <tr data-association-index="${index}">
              <td>${escapeHtml(caseById[assoc.case_id]?.name || assoc.case_id)}</td>
              <td><select data-field="endpoint_id">${endpointOptions}</select></td>
              <td>
                <select data-field="confidence">
                  ${['Alta','Media','Baja','Sin coincidencia'].map(v => `<option value="${v}" ${v === assoc.confidence ? 'selected' : ''}>${v}</option>`).join('')}
                </select>
              </td>
              <td>${escapeHtml((assoc.evidence || []).join('; ') || assoc.explanation || '')}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>
  `;
}

function renderVariables(variables) {
  return variables.map((variable, index) => `
    <article class="pm-card ${variable.sensitive ? 'warn' : 'ok'}" data-variable-index="${index}">
      <div class="pm-editor three">
        <label>Key<input data-field="key" value="${escapeHtml(variable.key || '')}" /></label>
        <label>Scope<select data-field="scope">${['environment','collection','request','data-file'].map(v => `<option value="${v}" ${v === variable.scope ? 'selected' : ''}>${v}</option>`).join('')}</select></label>
        <label>Activa<select data-field="enabled"><option value="true" ${variable.enabled !== false ? 'selected' : ''}>Si</option><option value="false" ${variable.enabled === false ? 'selected' : ''}>No</option></select></label>
      </div>
      <label>Valor<input data-field="value" value="${escapeHtml(variable.value || '')}" placeholder="${variable.sensitive ? 'Secreto omitido por seguridad' : ''}" /></label>
      <p class="small muted">${escapeHtml(variable.reason || '')} ${variable.sensitive ? '· Posible sensible' : ''}</p>
    </article>
  `).join('') || '<div class="pm-empty"><p>Sin variables detectadas.</p></div>';
}

function renderWarnings(m) {
  const validation = m.validation || {};
  const items = [
    ...(m.warnings || []).map(w => ({ kind: w.severity || 'medium', text: w.message, source: w.source })),
    ...(m.conflicts || []).map(c => ({ kind: 'high', text: c.message, source: c.proposed_priority })),
    ...(validation.errors || []).map(text => ({ kind: 'high', text, source: 'validacion' })),
    ...(validation.warnings || []).map(text => ({ kind: 'medium', text, source: 'validacion' })),
  ];
  return items.map(item => `
    <article class="pm-card ${item.kind === 'high' ? 'bad' : 'warn'}">
      <strong>${escapeHtml(item.kind)}</strong>
      <p>${escapeHtml(item.text || '')}</p>
      <p class="small muted">${escapeHtml(item.source || '')}</p>
    </article>
  `).join('') || '<div class="pm-empty"><p>Sin advertencias activas.</p></div>';
}

function renderDownloads(draft) {
  const base = `/api/postman/drafts/${draft.id}/download`;
  return `
    <article class="pm-card pm-downloads">
      <strong>Archivos generados</strong>
      <p class="small muted">Primero guarda tus cambios. Despues descarga individual o ZIP.</p>
      <div class="pm-actions">
        <button id="saveDraft" type="button">Guardar revision</button>
        <button id="validateDraft" type="button" class="btn-secondary">Validar</button>
      </div>
      <div class="pm-actions">
        <button type="button" data-download="${base}/collection" data-filename="collection.json">Collection</button>
        <button type="button" data-download="${base}/environment" data-filename="environment.json">Environment</button>
        <button type="button" data-download="${base}/readme" data-filename="README.md">README</button>
        <button type="button" data-download="${base}/traceability" data-filename="traceability.md">Trazabilidad</button>
        <button type="button" data-download="${base}/zip" data-filename="qa_postman_package.zip">ZIP completo</button>
      </div>
    </article>
  `;
}

function wireTab(tab) {
  if (tab === 'endpoints') {
    document.querySelectorAll('[data-endpoint-index]').forEach(card => {
      card.querySelectorAll('[data-field]').forEach(input => {
        input.addEventListener('input', () => {
          const endpoint = model().endpoints[Number(card.dataset.endpointIndex)];
          endpoint[input.dataset.field] = input.value;
        });
        input.addEventListener('change', () => {
          const endpoint = model().endpoints[Number(card.dataset.endpointIndex)];
          endpoint[input.dataset.field] = input.value;
        });
      });
    });
  }
  if (tab === 'trace') {
    document.querySelectorAll('[data-association-index]').forEach(row => {
      row.querySelector('[data-field="endpoint_id"]').value = model().associations[Number(row.dataset.associationIndex)].endpoint_id || '';
      row.querySelectorAll('[data-field]').forEach(input => input.addEventListener('change', () => {
        const assoc = model().associations[Number(row.dataset.associationIndex)];
        assoc[input.dataset.field] = input.value;
        if (input.dataset.field === 'endpoint_id' && !input.value) assoc.confidence = 'Sin coincidencia';
      }));
    });
  }
  if (tab === 'variables') {
    document.querySelectorAll('[data-variable-index]').forEach(card => {
      card.querySelectorAll('[data-field]').forEach(input => input.addEventListener('input', () => {
        const variable = model().variables[Number(card.dataset.variableIndex)];
        variable[input.dataset.field] = input.dataset.field === 'enabled' ? input.value === 'true' : input.value;
      }));
    });
  }
  $('saveDraft')?.addEventListener('click', saveDraft);
  $('validateDraft')?.addEventListener('click', validateDraft);
  document.querySelectorAll('[data-download]').forEach(button => {
    button.addEventListener('click', () => downloadFile(button.dataset.download, button.dataset.filename));
  });
}

async function downloadFile(url, filename) {
  setStatus('Preparando descarga...');
  const res = await authFetch(url);
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    setStatus(data.detail || 'No se pudo descargar.');
    return;
  }
  const blob = await res.blob();
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = filename || 'postman_file';
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(link.href);
  setStatus('Descarga generada.', true);
}

async function saveDraft() {
  if (!currentDraft) return;
  setStatus('Guardando revision...');
  const payload = {
    endpoints: model().endpoints || [],
    test_cases: model().test_cases || [],
    associations: model().associations || [],
    variables: model().variables || [],
    warnings: model().warnings || [],
    conflicts: model().conflicts || [],
    folders: model().folders || [],
  };
  const res = await authFetch(`/api/postman/drafts/${currentDraft.id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    setStatus(data.detail || 'No se pudo guardar.');
    return;
  }
  currentDraft = data.draft;
  setStatus('Revision guardada.', true);
  renderAll();
}

async function validateDraft() {
  if (!currentDraft) return;
  await saveDraft();
  const res = await authFetch(`/api/postman/drafts/${currentDraft.id}/validate`, { method: 'POST' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    setStatus(data.detail || 'No se pudo validar.');
    return;
  }
  currentDraft.model.validation = data.validation;
  setStatus(data.validation.valid ? 'Validacion OK.' : 'Validacion con errores.', data.validation.valid);
  activeTab = 'warnings';
  renderAll();
}

async function analyze(event) {
  event.preventDefault();
  setStatus('Analizando fuentes...');
  $('analyzeBtn').disabled = true;
  try {
    const fd = new FormData();
    fd.append('project_name', $('projectName').value.trim());
    fd.append('manual_text', $('manualText').value);
    [...$('sourceFiles').files].forEach(file => fd.append('files', file));
    const res = await authFetch('/api/postman/analyze', { method: 'POST', body: fd });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || 'No se pudo analizar.');
    currentDraft = data.draft;
    activeTab = 'endpoints';
    setStatus('Analisis generado. Revisa y ajusta antes de descargar.', true);
    renderAll();
  } catch (err) {
    setStatus(err.message || 'Error al analizar.');
  } finally {
    $('analyzeBtn').disabled = false;
  }
}

async function loadDrafts() {
  setStatus('Cargando biblioteca...');
  const res = await authFetch('/api/postman/drafts');
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    setStatus(data.detail || 'No se pudo cargar biblioteca.');
    return;
  }
  $('tabContent').innerHTML = (data.drafts || []).map(draft => `
    <article class="pm-card" data-draft-id="${escapeHtml(draft.id)}">
      <strong>${escapeHtml(draft.project_name)}</strong>
      <p class="small muted">${escapeHtml(draft.created_by)} · ${(draft.model?.endpoints || []).length} endpoints · ${(draft.model?.test_cases || []).length} casos</p>
      <button type="button" class="btn-secondary">Abrir</button>
    </article>
  `).join('') || '<div class="pm-empty"><p>Sin drafts guardados.</p></div>';
  $('summary').innerHTML = '';
  document.querySelectorAll('[data-draft-id] button').forEach(btn => btn.addEventListener('click', async () => {
    const id = btn.closest('[data-draft-id]').dataset.draftId;
    const detail = await (await authFetch(`/api/postman/drafts/${id}`)).json();
    currentDraft = detail.draft;
    activeTab = 'endpoints';
    setStatus('Draft cargado.', true);
    renderAll();
  }));
}

$('postmanForm').addEventListener('submit', analyze);
$('refreshDrafts').addEventListener('click', loadDrafts);
document.querySelectorAll('[data-tab]').forEach(btn => btn.addEventListener('click', () => renderTab(btn.dataset.tab)));
$('sourceFiles').addEventListener('change', () => {
  const files = [...$('sourceFiles').files].map(file => file.name);
  if (!files.length) {
    $('fileSummary').textContent = 'JSON, YAML, MD, TXT, DOCX, PDF, cURL, HTTP o Postman';
    return;
  }
  const visibleFiles = files.slice(0, 4).map(name => `<span class="postman-file-pill">${escapeHtml(name)}</span>`);
  const remaining = files.length - visibleFiles.length;
  $('fileSummary').innerHTML = `${visibleFiles.join('')}${remaining > 0 ? `<span class="postman-file-pill">+${remaining} archivos</span>` : ''}`;
});
