import { authFetch, authHeaders, getUser } from './auth.js';
import { renderOverview } from './overview.js';

const sideNav = document.getElementById('sideNav');
const appView = document.getElementById('appView');
const viewTitle = document.getElementById('viewTitle');
const viewSubtitle = document.getElementById('viewSubtitle');

const TASK_ROLES = ['qa', 'lider'];

const tools = [
  { id: 'inicio', label: 'Inicio', global: true },
  { id: 'registro-ia', roles: TASK_ROLES, label: 'Registro IA' },
  { id: 'entendimiento', roles: ['qa', 'lider'], label: 'Entendimiento', href: '/app/entendimiento.html' },
  { id: 'casos', roles: ['qa'], label: 'Casos de Prueba', href: '/app/casos.html' },
  { id: 'playwright', roles: ['qa'], label: 'Playwright', href: '/app/playwright_xlsx.html?v=20261005-1' },
  { id: 'postman', roles: ['qa'], label: 'Postman', href: '/app/postman.html?v=20261001-3' },
  { id: 'karate', roles: ['qa'], label: 'Karate', href: '/app/karate.html?v=20260930-1' },
  { id: 'evidencias', roles: ['qa'], label: 'Evidencias QA', href: '/app/evidencias.html?v=20261007-1' },
];


function availableTools() {
  const current = getUser();
  return tools.filter(tool => tool.global || tool.roles?.includes(current.role));
}

function setHeader(title, subtitle = '') {
  viewTitle.textContent = title;
  viewSubtitle.textContent = subtitle;
}

function renderSidebar(activeId = 'inicio') {
  const current = getUser();
  document.getElementById('userBadge').textContent = 'Espacio compartido';
  sideNav.innerHTML = availableTools().map(tool => `
    <button type="button" class="${tool.id === activeId ? 'active' : ''}" data-view="${tool.id}">
      ${tool.label}
    </button>
  `).join('');
  sideNav.querySelectorAll('button').forEach(btn => btn.addEventListener('click', () => navigate(btn.dataset.view)));
}

async function navigate(id) {
  document.body.classList.remove('sidebar-open');
  renderSidebar(id);
  const tool = tools.find(item => item.id === id);
  if (!tool) return renderHome();
  if (tool.href) return renderModule(tool);
  if (id === 'registro-ia') return renderRegistroIA();
  return renderHome();
}

function renderModule(tool) {
  setHeader(tool.label, 'Modulo integrado');
  const frame = document.createElement('iframe');
  frame.className = 'module-frame';
  frame.title = tool.label;
  frame.setAttribute('scrolling', 'no');
  frame.src = tool.href;
  frame.addEventListener('load', () => {
    const doc = frame.contentDocument;
    if (!doc?.documentElement || !doc.body) return;

    const syncHeight = () => {
      const contentHeight = Math.max(doc.documentElement.scrollHeight, doc.body.scrollHeight);
      frame.style.height = `${Math.max(contentHeight, 560)}px`;
    };
    const resizeObserver = window.ResizeObserver ? new ResizeObserver(syncHeight) : null;
    resizeObserver?.observe(doc.documentElement);
    resizeObserver?.observe(doc.body);
    const mutationObserver = new MutationObserver(() => requestAnimationFrame(syncHeight));
    mutationObserver.observe(doc.body, { childList: true, subtree: true, attributes: true, characterData: true });
    doc.defaultView.addEventListener('resize', syncHeight);
    doc.fonts?.ready.then(syncHeight);
    syncHeight();
  });
  appView.replaceChildren(frame);
}

function renderRegistroIA() {
  setHeader('Registro IA', 'Analisis de calidad QA');
  renderQualityRecords();
}

async function renderQualityRecords() {
  const current = getUser();
  appView.innerHTML = `<div class="panel-block">Cargando registro IA...</div>`;
  try {
    const data = await getJson('/api/quality-records');
    const records = data.records || [];
    const canCreate = current.role === 'qa';
    appView.innerHTML = `
      <section class="panel-block quality-panel">
        <div class="quality-head">
          <div>
            <h2>Tabla de calidad QA</h2>
          </div>
          ${canCreate ? '<button id="btnNewQualityRow" type="button">Agregar linea</button>' : ''}
        </div>
        <div id="qualityMsg" class="field-error"></div>
        <div class="quality-table-wrap">
          <table class="quality-table">
            <thead>
              <tr>
                <th>ID REQ</th>
                <th>Nombre Requerimiento</th>
                <th>Responsable QA</th>
                <th>Tiempo de Diseno</th>
                <th>Casos Generados</th>
                <th>Casos OK</th>
                <th>% de Calidad IA</th>
                <th>Casos Adicionales QA</th>
                <th>% de Calidad Post Revision QA</th>
                <th>Casos Adicionales Funcional</th>
                <th>% de Calidad Post Revision Funcional</th>
                <th>Acciones</th>
              </tr>
            </thead>
            <tbody id="qualityBody">
              ${records.map(record => qualityRow(record)).join('') || '<tr><td colspan="12" class="empty-cell">Sin registros cargados.</td></tr>'}
            </tbody>
          </table>
        </div>
        <div class="quality-footer">
          <button id="btnExportQuality" type="button" class="btn-secondary">Exportar Tabla</button>
        </div>
      </section>
    `;
    document.getElementById('btnNewQualityRow')?.addEventListener('click', () => openQualityModal());
    document.getElementById('btnExportQuality')?.addEventListener('click', exportQualityTable);
    wireQualityRows(records);
  } catch (err) {
    appView.innerHTML = `<div class="panel-block bad">${escapeHtml(err.message)}</div>`;
  }
}

function qualityRow(record) {
  const current = getUser();
  const canManage = true;
  return `
    <tr data-quality-id="${record.id}">
      <td>${escapeHtml(record.id_req)}</td>
      <td>${escapeHtml(record.requirement_name)}</td>
      <td>${escapeHtml(record.qa_responsible_display || record.qa_responsible)}</td>
      <td>${formatDuration(record.design_time_seconds ?? Math.round(Number(record.design_time || 0) * 60))}</td>
      <td>${escapeHtml(record.generated_cases)}</td>
      <td>${escapeHtml(record.ok_cases)}</td>
      <td class="calc-cell">${formatPercent(record.ai_quality_percent)}</td>
      <td>${escapeHtml(record.additional_qa_cases)}</td>
      <td class="calc-cell">${formatPercent(record.post_qa_review_quality_percent ?? record.post_review_quality_percent)}</td>
      <td>${escapeHtml(record.additional_functional_cases ?? 0)}</td>
      <td class="calc-cell">${formatPercent(record.post_functional_review_quality_percent)}</td>
      <td class="quality-actions">
        ${canManage ? `
          <button type="button" class="btn-secondary" data-quality-edit>Editar</button>
          <button type="button" class="btn-danger" data-quality-delete>Eliminar</button>
        ` : '<span class="small muted">Solo lectura</span>'}
      </td>
    </tr>
  `;
}

function wireQualityRows(records) {
  appView.querySelectorAll('[data-quality-id]').forEach(row => {
    const id = row.dataset.qualityId;
    const record = records.find(item => item.id === id);
    row.querySelector('[data-quality-edit]')?.addEventListener('click', () => openQualityModal(record));
    row.querySelector('[data-quality-delete]')?.addEventListener('click', () => openQualityDelete(record));
  });
}

function openQualityModal(record = null) {
  const isEdit = Boolean(record);
  const designSeconds = Number(record?.design_time_seconds ?? Math.round(Number(record?.design_time || 0) * 60));
  const designMinutes = Math.floor(designSeconds / 60);
  const remainingSeconds = designSeconds % 60;
  const modal = ensureModal();
  document.getElementById('modalBody').innerHTML = `
    <h2>${isEdit ? 'Editar linea' : 'Crear linea'}</h2>
    <form id="qualityForm" class="quality-form">
      <label>ID REQ<input id="qualityIdReq" type="text" value="${escapeHtml(record?.id_req || '')}" required /></label>
      <label>Nombre Requerimiento<input id="qualityName" type="text" value="${escapeHtml(record?.requirement_name || '')}" required /></label>
      <label>Responsable QA<input id="qualityQa" type="text" maxlength="120" placeholder="Nombre y apellido" value="${escapeHtml(record?.qa_responsible_display || record?.qa_responsible || '')}" required /></label>
      <div class="form-grid two">
        <label>Tiempo de Diseno (minutos)<input id="qualityDesignMinutes" type="number" min="0" step="1" value="${isEdit ? designMinutes : ''}" required /></label>
        <label>Segundos<input id="qualityDesignSeconds" type="number" min="0" max="59" step="1" value="${isEdit ? remainingSeconds : 0}" required /></label>
      </div>
      <div class="form-grid two">
        <label>Casos Generados<input id="qualityGenerated" type="number" min="0" step="1" value="${escapeHtml(record?.generated_cases ?? '')}" required /></label>
        <label>Casos OK<input id="qualityOk" type="number" min="0" step="1" value="${escapeHtml(record?.ok_cases ?? '')}" required /></label>
      </div>
      <div class="form-grid two">
        <label>Casos Adicionales QA<input id="qualityAdditional" type="number" min="0" step="1" value="${escapeHtml(record?.additional_qa_cases ?? '')}" required /></label>
        <label>Casos Adicionales Funcional<input id="qualityAdditionalFunctional" type="number" min="0" step="1" value="${escapeHtml(record?.additional_functional_cases ?? 0)}" required /></label>
      </div>
      <div class="form-grid two">
        <label>% de Calidad IA<input id="qualityAiPercent" class="readonly-calc" type="text" disabled /></label>
        <label>% de Calidad Post Revision QA<input id="qualityPostPercent" class="readonly-calc" type="text" disabled /></label>
      </div>
      <div class="form-grid two">
        <label>% de Calidad Post Revision Funcional<input id="qualityPostFunctionalPercent" class="readonly-calc" type="text" disabled /></label>
      </div>
      <div id="qualityModalMsg" class="field-error"></div>
      <button type="submit">${isEdit ? 'Guardar cambios' : 'Crear linea'}</button>
    </form>
  `;
  const form = document.getElementById('qualityForm');
  ['qualityGenerated', 'qualityOk', 'qualityAdditional', 'qualityAdditionalFunctional'].forEach(id => document.getElementById(id).addEventListener('input', updateQualityPreview));
  updateQualityPreview();
  form.addEventListener('submit', event => saveQualityRecord(event, record));
  modal.classList.remove('hidden');
}

function updateQualityPreview() {
  const generated = Number(document.getElementById('qualityGenerated')?.value || 0);
  const ok = Number(document.getElementById('qualityOk')?.value || 0);
  const additional = Number(document.getElementById('qualityAdditional')?.value || 0);
  const additionalFunctional = Number(document.getElementById('qualityAdditionalFunctional')?.value || 0);
  document.getElementById('qualityAiPercent').value = generated > 0 ? formatPercent((ok / generated) * 100) : '';
  document.getElementById('qualityPostPercent').value = ok + additional > 0 ? formatPercent((ok / (ok + additional)) * 100) : '';
  document.getElementById('qualityPostFunctionalPercent').value = ok + additional + additionalFunctional > 0 ? formatPercent((ok / (ok + additional + additionalFunctional)) * 100) : '';
}

async function saveQualityRecord(event, record) {
  event.preventDefault();
  const msg = document.getElementById('qualityModalMsg');
  msg.textContent = '';
  const payload = readQualityForm();
  const error = validateQualityPayload(payload);
  if (error) {
    msg.textContent = error;
    return;
  }
  try {
    const url = record ? `/api/quality-records/${record.id}` : '/api/quality-records';
    await sendJson(url, record ? 'PUT' : 'POST', payload);
    closeModal();
    await renderQualityRecords();
  } catch (err) {
    msg.textContent = err.message;
  }
}

function readQualityForm() {
  const designMinutes = Number(document.getElementById('qualityDesignMinutes').value);
  const designSeconds = Number(document.getElementById('qualityDesignSeconds').value);
  const generated = Number(document.getElementById('qualityGenerated').value);
  const ok = Number(document.getElementById('qualityOk').value);
  const additional = Number(document.getElementById('qualityAdditional').value);
  const additionalFunctional = Number(document.getElementById('qualityAdditionalFunctional').value);
  return {
    id_req: document.getElementById('qualityIdReq').value.trim(),
    requirement_name: document.getElementById('qualityName').value.trim(),
    qa_responsible: document.getElementById('qualityQa').value.trim(),
    design_time_seconds: (designMinutes * 60) + designSeconds,
    generated_cases: generated,
    ok_cases: ok,
    additional_qa_cases: additional,
    additional_functional_cases: additionalFunctional,
    ai_quality_percent: generated > 0 ? Number(((ok / generated) * 100).toFixed(2)) : 0,
    post_qa_review_quality_percent: ok + additional > 0 ? Number(((ok / (ok + additional)) * 100).toFixed(2)) : 0,
    post_functional_review_quality_percent: ok + additional + additionalFunctional > 0 ? Number(((ok / (ok + additional + additionalFunctional)) * 100).toFixed(2)) : 0,
  };
}

function validateQualityPayload(payload) {
  if (!payload.qa_responsible) return 'Completa el nombre del responsable QA.';
  if (!payload.id_req || !payload.requirement_name) return 'ID REQ y Nombre Requerimiento son obligatorios.';
  const minutes = Number(document.getElementById('qualityDesignMinutes')?.value);
  const seconds = Number(document.getElementById('qualityDesignSeconds')?.value);
  if (!Number.isInteger(minutes) || minutes < 0) return 'Los minutos deben ser un numero entero mayor o igual a 0.';
  if (!Number.isInteger(seconds) || seconds < 0 || seconds > 59) return 'Los segundos deben estar entre 0 y 59.';
  const nums = ['design_time_seconds', 'generated_cases', 'ok_cases', 'additional_qa_cases', 'additional_functional_cases'];
  if (nums.some(key => Number.isNaN(payload[key]) || payload[key] < 0)) return 'Los valores numericos no pueden ser negativos.';
  if (payload.generated_cases <= 0) return 'Casos Generados debe ser mayor a 0.';
  if (payload.ok_cases + payload.additional_qa_cases <= 0) return 'Casos OK + Casos Adicionales QA debe ser mayor a 0.';
  if (payload.ok_cases + payload.additional_qa_cases + payload.additional_functional_cases <= 0) return 'El total post revision funcional debe ser mayor a 0.';
  if (payload.ok_cases > payload.generated_cases) return 'Casos OK no puede ser mayor a Casos Generados.';
  return '';
}

function openQualityDelete(record) {
  const modal = ensureModal();
  document.getElementById('modalBody').innerHTML = `
    <h2>Eliminar linea</h2>
    <p>¿Eliminar el registro ${escapeHtml(record.id_req)}?</p>
    <div id="qualityModalMsg" class="field-error"></div>
    <button id="confirmQualityDelete" type="button" class="btn-danger">Eliminar</button>
  `;
  document.getElementById('confirmQualityDelete').addEventListener('click', async () => {
    try {
      await sendJson(`/api/quality-records/${record.id}`, 'DELETE', {});
      closeModal();
      await renderQualityRecords();
    } catch (err) {
      document.getElementById('qualityModalMsg').textContent = err.message;
    }
  });
  modal.classList.remove('hidden');
}

async function exportQualityTable() {
  const msg = document.getElementById('qualityMsg');
  msg.textContent = '';
  try {
    const res = await authFetch('/api/quality-records/export');
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || 'No se pudo exportar.');
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'registro_ia.xlsx';
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (err) {
    msg.textContent = err.message;
  }
}

async function getJson(url) {
  const res = await authFetch(url);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || 'No se pudo cargar la informacion.');
  return data;
}

async function renderHome() {
  setHeader('Inicio', 'Registro IA del equipo');
  return renderOverview(appView);
}

async function sendJson(url, method, payload) {
  const res = await authFetch(url, { method, headers: authHeaders({ 'Content-Type': 'application/json' }), body: JSON.stringify(payload) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || 'Operacion fallida.');
  return data;
}

function renderModal() {
  return `
    <div id="taskModal" class="modal hidden">
      <div class="modal-card">
        <button id="modalClose" type="button" class="modal-close">×</button>
        <div id="modalBody"></div>
      </div>
    </div>
  `;
}

function ensureModal() {
  let modal = document.getElementById('taskModal');
  if (!modal) {
    appView.insertAdjacentHTML('beforeend', renderModal());
    modal = document.getElementById('taskModal');
  }
  modal.querySelector('.modal-card')?.classList.remove('task-modal-card', 'overdue-modal-card');
  document.getElementById('modalClose').onclick = closeModal;
  modal.onclick = event => {
    if (event.target === modal) closeModal();
  };
  return modal;
}

function closeModal() {
  const modal = document.getElementById('taskModal');
  modal?.classList.add('hidden');
  const body = document.getElementById('modalBody');
  if (body) body.innerHTML = '';
}

function formatDuration(value) {
  const totalSeconds = Math.max(0, Math.round(Number(value) || 0));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes} min ${String(seconds).padStart(2, '0')} seg`;
}

function formatPercent(value) {
  const num = Number(value);
  if (Number.isNaN(num)) return '';
  return `${Number(num.toFixed(2))}%`;
}

function escapeHtml(value = '') {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

renderSidebar('inicio');
renderHome();
document.getElementById('mobileMenu')?.addEventListener('click', () => document.body.classList.toggle('sidebar-open'));
