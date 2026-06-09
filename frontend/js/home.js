import { ROLE_LABELS, authFetch, authHeaders, getUser, logout, requireAuth } from './auth.js';

const user = requireAuth();
const sideNav = document.getElementById('sideNav');
const appView = document.getElementById('appView');
const viewTitle = document.getElementById('viewTitle');
const viewSubtitle = document.getElementById('viewSubtitle');

const TASK_ROLES = ['qa', 'lider'];
const canUseTasks = () => TASK_ROLES.includes(getUser()?.role);
let overdueAlertShown = false;
let pendingTaskOpenId = '';

const tools = [
  { id: 'inicio', label: 'Inicio', global: true },
  { id: 'tareas', roles: TASK_ROLES, label: 'Tareas' },
  { id: 'registro-ia', roles: TASK_ROLES, label: 'Registro IA' },
  { id: 'entendimiento', roles: ['qa', 'lider'], label: 'Entendimiento', href: '/app/entendimiento.html' },
  { id: 'calidad', roles: ['funcional', 'lider'], label: 'Calidad Funcional', href: '/app/calidad.html' },
  { id: 'casos', roles: ['qa'], label: 'Casos de Prueba', href: '/app/casos.html' },
  { id: 'playwright', roles: ['qa'], label: 'Playwright', href: '/app/playwright_xlsx.html?v=20260602-1' },
  { id: 'funcional', roles: ['funcional'], label: 'Doc. Funcional', href: '/app/chat-funcional.html' },
  { id: 'importacion', roles: ['lider'], label: 'Importacion', href: '/app/importacion.html' },
  { id: 'usuarios', roles: ['lider'], label: 'Usuarios', href: '/app/usuarios.html?v=20260519-2' },
  { id: 'movimientos', roles: ['lider'], label: 'Movimientos' },
];

const stateLabels = {
  pendiente: 'Pendiente',
  en_progreso: 'En progreso',
  bloqueada: 'Bloqueada',
  en_revision: 'En revision',
  resuelta: 'Resuelta',
  cerrada: 'Cerrada',
};

const priorityLabels = {
  baja: 'Baja',
  media: 'Media',
  alta: 'Alta',
  critica: 'Critica',
};

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
  document.getElementById('userBadge').textContent = `${current.username} · ${ROLE_LABELS[current.role] || current.role}`;
  sideNav.innerHTML = availableTools().map(tool => `
    <button type="button" class="${tool.id === activeId ? 'active' : ''}" data-view="${tool.id}">
      ${tool.label}
    </button>
  `).join('');
  sideNav.querySelectorAll('button').forEach(btn => btn.addEventListener('click', () => navigate(btn.dataset.view)));
}

async function navigate(id) {
  renderSidebar(id);
  const tool = tools.find(item => item.id === id);
  if (!tool) return renderHome();
  if (tool.href) return renderModule(tool);
  if (id === 'tareas') return renderTasks();
  if (id === 'registro-ia') return renderRegistroIA();
  if (id === 'movimientos') return renderActivityLog();
  return renderHome();
}

function renderModule(tool) {
  setHeader(tool.label, 'Modulo integrado');
  appView.innerHTML = `<iframe class="module-frame" src="${tool.href}" title="${tool.label}"></iframe>`;
}

async function renderActivityLog(filters = {}) {
  setHeader('Movimientos', 'Auditoria de acciones');
  appView.innerHTML = `<div class="panel-block">Cargando movimientos...</div>`;
  try {
    const params = new URLSearchParams();
    if (filters.username) params.set('username', filters.username);
    if (filters.module) params.set('module', filters.module);
    if (filters.q) params.set('q', filters.q);
    const data = await getJson(`/api/activity${params.toString() ? `?${params}` : ''}`);
    appView.innerHTML = `
      <section class="panel-block activity-panel">
        <div class="quality-head">
          <div>
            <h2>Registro de movimientos</h2>
            <p class="small muted">Consulta por usuario, modulo o texto y entra al detalle de cada accion.</p>
          </div>
          <button id="refreshActivity" type="button" class="btn-secondary">Actualizar</button>
        </div>
        <div class="activity-filters">
          <label>Usuario
            <select id="activityUser">
              <option value="">Todos</option>
              ${(data.users || []).map(username => `<option value="${escapeHtml(username)}" ${filters.username === username ? 'selected' : ''}>${escapeHtml(username)}</option>`).join('')}
            </select>
          </label>
          <label>Modulo
            <select id="activityModule">
              <option value="">Todos</option>
              ${(data.modules || []).map(module => `<option value="${escapeHtml(module)}" ${filters.module === module ? 'selected' : ''}>${escapeHtml(module)}</option>`).join('')}
            </select>
          </label>
          <label>Buscar
            <input id="activitySearch" type="search" value="${escapeHtml(filters.q || '')}" placeholder="Accion, detalle o usuario" />
          </label>
          <button id="applyActivityFilters" type="button">Consultar</button>
        </div>
        <div class="activity-list">
          ${(data.items || []).map(activityItem).join('') || '<p class="small muted">Sin movimientos registrados.</p>'}
        </div>
      </section>
    `;
    document.getElementById('refreshActivity')?.addEventListener('click', () => renderActivityLog(filters));
    document.getElementById('applyActivityFilters')?.addEventListener('click', () => {
      renderActivityLog({
        username: document.getElementById('activityUser').value,
        module: document.getElementById('activityModule').value,
        q: document.getElementById('activitySearch').value.trim(),
      });
    });
    appView.querySelectorAll('[data-activity-id]').forEach(btn => {
      btn.addEventListener('click', () => openActivityDetail(btn.dataset.activityId));
    });
  } catch (err) {
    appView.innerHTML = `<div class="panel-block bad">${escapeHtml(err.message)}</div>`;
  }
}

function activityItem(item) {
  return `
    <article class="activity-item">
      <div>
        <strong>${escapeHtml(item.action)}</strong>
        <p class="small muted">${escapeHtml(item.username)} · ${escapeHtml(item.module)} · ${formatDate(item.created_at)}</p>
        <p>${escapeHtml(item.detail || '')}</p>
      </div>
      <button type="button" class="btn-secondary" data-activity-id="${escapeHtml(item.id)}">Ver detalle</button>
    </article>
  `;
}

async function openActivityDetail(id) {
  const data = await getJson(`/api/activity/${id}`);
  const item = data.item;
  const meta = item.metadata || {};
  const hasGeneratedFile = Boolean(meta.resultado_url && meta.archivo_generado);
  const isTask = Boolean(meta.task_id);
  const hasAiOutput = Boolean(meta.devolucion_ia);
  const modal = ensureModal();
  document.getElementById('modalBody').innerHTML = `
    <h2>Detalle del movimiento</h2>
    <div class="activity-detail">
      <div class="activity-summary-grid">
        <div><span>Usuario</span><strong>${escapeHtml(item.username)} (${escapeHtml(item.role || '')})</strong></div>
        <div><span>Modulo</span><strong>${escapeHtml(item.module)}</strong></div>
        <div><span>Accion</span><strong>${escapeHtml(item.action)}</strong></div>
        <div><span>Fecha</span><strong>${formatDate(item.created_at)}</strong></div>
      </div>
      <p class="activity-detail-main">${escapeHtml(item.detail || '')}</p>
      ${meta.titulo ? `<p><b>Tarea:</b> ${escapeHtml(meta.titulo)}</p>` : ''}
      ${meta.estado_actual || meta.estado ? `<p><b>Estado:</b> ${escapeHtml(meta.estado_actual || meta.estado)}</p>` : ''}
      ${meta.qa_responsible ? `<p><b>QA responsable:</b> ${escapeHtml(meta.qa_responsible)}</p>` : ''}
      ${meta.archivo_cargado ? `<p><b>Archivo cargado:</b> ${escapeHtml(meta.archivo_cargado)}</p>` : ''}
      ${hasGeneratedFile ? `<p><b>Archivo generado:</b> ${escapeHtml(meta.archivo_generado)}</p>` : ''}
      ${meta.cantidad_casos !== undefined ? `<p><b>Casos generados:</b> ${escapeHtml(meta.cantidad_casos)}</p>` : ''}
      ${meta.score !== undefined ? `<p><b>Score:</b> ${escapeHtml(meta.score)}</p>` : ''}
      ${Array.isArray(meta.cambios) && meta.cambios.length ? `
        <div class="activity-changes">
          <b>Cambios:</b>
          ${meta.cambios.map(change => `<p>${escapeHtml(change)}</p>`).join('')}
        </div>
      ` : ''}
      ${hasAiOutput ? `
        <div class="activity-ai-output">
          <div class="activity-section-head">
            <b>Devolucion IA</b>
          </div>
          <div class="activity-ai-text">${escapeHtml(meta.devolucion_ia)}</div>
        </div>
      ` : ''}
      <div class="activity-detail-actions">
        ${hasGeneratedFile ? `<a class="activity-action-button" href="${escapeHtml(meta.resultado_url)}" download>${hasAiOutput ? 'Descargar devolucion IA' : 'Descargar archivo generado'}</a>` : ''}
        ${isTask ? `<button type="button" class="activity-action-button" id="openActivityTask">Ir a tarea</button>` : ''}
      </div>
    </div>
  `;
  document.getElementById('openActivityTask')?.addEventListener('click', async () => {
    closeModal();
    await navigate('tareas');
    setTimeout(() => {
      const card = appView.querySelector(`[data-task-id="${meta.task_id}"]`);
      card?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      card?.classList.add('task-highlight');
      setTimeout(() => card?.classList.remove('task-highlight'), 2200);
    }, 250);
  });
  modal.classList.remove('hidden');
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
            <p class="small muted">Registro editable persistido en base de datos.</p>
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
  const canManage = current.role === 'qa' && record.created_by === current.username;
  return `
    <tr data-quality-id="${record.id}">
      <td>${escapeHtml(record.id_req)}</td>
      <td>${escapeHtml(record.requirement_name)}</td>
      <td>${escapeHtml(record.qa_responsible)}</td>
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
      <label>Responsable QA<input id="qualityQa" type="text" value="${escapeHtml(record?.qa_responsible || getUser().username)}" disabled /></label>
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
  setHeader('Inicio', 'Resumen operativo');
  if (!canUseTasks()) {
    appView.innerHTML = `
      <section class="panel-block dashboard-wide empty-panel">
        <h2>Bienvenido</h2>
        <p class="small muted">Selecciona una opcion del sidebar para comenzar.</p>
      </section>
    `;
    return;
  }

  appView.innerHTML = `<div class="panel-block">Cargando dashboard...</div>`;
  try {
    const data = await getJson('/api/internal-tasks/summary');
    appView.innerHTML = `
      <div class="dashboard-grid">
        <section class="panel-block">
          <h2>Tareas asignadas</h2>
          <div class="summary-grid compact-summary">
            ${Object.entries(data.counts || {}).map(([key, value]) => `
              <div class="metric"><span>${stateLabels[key] || key}</span><strong>${value}</strong></div>
            `).join('')}
          </div>
        </section>
        <section class="panel-block">
          <h2>Ultimas notificaciones</h2>
          ${renderNotifications(data.notifications || [])}
        </section>
        <section class="panel-block dashboard-wide">
          <h2>Ultimas tareas</h2>
          <div class="task-list">${(data.tasks || []).map(task => taskCard(task)).join('') || '<p class="small muted">Sin tareas por ahora.</p>'}</div>
        </section>
      </div>
    `;
    wireTaskCards();
  } catch (err) {
    appView.innerHTML = `<div class="panel-block bad">${escapeHtml(err.message)}</div>`;
  }
}

async function renderTasks() {
  setHeader('Tareas', 'Gestion interna');
  appView.innerHTML = `<div class="panel-block">Cargando tareas...</div>`;
  try {
    const [taskData, usersData] = await Promise.all([
      getJson('/api/internal-tasks/tasks'),
      getJson('/api/internal-tasks/users'),
    ]);
    const users = usersData.users || [];
    const tasks = taskData.tasks || [];
    appView.innerHTML = `
      <section class="panel-block tasks-board">
        <div class="tasks-board-head">
          <div>
            <h2>Listado de tareas</h2>
            <p class="small muted">Vista operativa con filtros y detalle completo por tarea.</p>
          </div>
          <button id="btnOpenCreateTask" type="button">Crear tarea</button>
        </div>
        <div class="task-filters">
          <label>Buscar<input id="taskSearch" type="search" placeholder="Titulo, QA, desarrollador o funcional" /></label>
          <label>Estado<select id="taskFilterStatus"><option value="">Todos</option>${statusOptions('')}</select></label>
          <label>Prioridad<select id="taskFilterPriority"><option value="">Todas</option>${priorityOptions('')}</select></label>
          <label>QA<select id="taskFilterQa"><option value="">Todos</option>${users.filter(u => u.role === 'qa').map(optionUser).join('')}</select></label>
        </div>
        <div class="task-list-header">
          <span>Tarea</span>
          <span>Responsables</span>
          <span>Fechas</span>
          <span>Estado</span>
        </div>
        <div id="taskList" class="task-preview-list">
          ${tasks.map(task => taskCard(task, users)).join('') || '<p class="small muted empty-task-list">No hay tareas internas.</p>'}
        </div>
      </section>
    `;
    document.getElementById('btnOpenCreateTask')?.addEventListener('click', () => openCreateTaskModal(users));
    wireTaskFilters(tasks, users);
    wireTaskCards(users);
    if (pendingTaskOpenId) {
      const taskId = pendingTaskOpenId;
      pendingTaskOpenId = '';
      setTimeout(() => openTaskFromList(taskId, users), 80);
    }
  } catch (err) {
    appView.innerHTML = `<div class="panel-block bad">${escapeHtml(err.message)}</div>`;
  }
}

function renderTaskForm(users, prefix = 'task', task = {}) {
  const qaUsers = users.filter(u => u.role === 'qa');
  return `
      <form id="${prefix}Form" class="task-edit-form">
        <label>Titulo<input id="${prefix}Title" type="text" value="${escapeHtml(task.title || '')}" required /></label>
        <label>Descripcion<textarea id="${prefix}Description" rows="4">${escapeHtml(task.description || '')}</textarea></label>
        <label>QA responsable<select id="${prefix}QaResponsible" required>${qaUsers.map(u => `<option value="${escapeHtml(u.username)}" ${u.username === (task.qa_responsible || task.assigned_to) ? 'selected' : ''}>${escapeHtml(u.full_name || u.username)} · ${escapeHtml(u.username)}</option>`).join('')}</select></label>
        <label>Nombre del desarrollador<input id="${prefix}DeveloperName" type="text" value="${escapeHtml(task.developer_name || '')}" /></label>
        <label>Nombre del funcional<input id="${prefix}FunctionalName" type="text" value="${escapeHtml(task.functional_name || '')}" /></label>
        <div class="form-grid two">
          <label>Fecha desde<input id="${prefix}DateFrom" type="date" value="${escapeHtml(task.date_from || '')}" /></label>
          <label>Fecha estimada hasta<input id="${prefix}EstimatedUntil" type="date" value="${escapeHtml(task.estimated_until || '')}" /></label>
        </div>
        <div class="form-grid two">
          <label>Prioridad<select id="${prefix}Priority">${priorityOptions(task.priority || 'media')}</select></label>
          <label>Estado<select id="${prefix}Status">${statusOptions(task.status || 'pendiente')}</select></label>
        </div>
        <button type="submit">${task.id ? 'Guardar cambios' : 'Crear tarea'}</button>
        <div id="${prefix}FormMsg" class="field-error"></div>
      </form>
  `;
}

function optionUser(user) {
  return `<option value="${escapeHtml(user.username)}">${escapeHtml(user.full_name || user.username)} · ${escapeHtml(user.username)}</option>`;
}

function statusOptions(selected = 'pendiente') {
  return Object.entries(stateLabels).map(([value, label]) => `<option value="${value}" ${value === selected ? 'selected' : ''}>${label}</option>`).join('');
}

function priorityOptions(selected = 'media') {
  return Object.entries(priorityLabels).map(([value, label]) => `<option value="${value}" ${value === selected ? 'selected' : ''}>${label}</option>`).join('');
}

function taskCard(task, users = []) {
  const status = task.status || 'pendiente';
  const priority = task.priority || 'media';
  return `
    <article class="task-card task-preview-row status-${status} priority-${priority}" data-task-id="${task.id}">
      <div class="task-preview-main">
        <div>
          <strong>${escapeHtml(task.title)}</strong>
          <p class="small muted">${escapeHtml(truncateText(task.description || 'Sin descripcion.', 150))}</p>
        </div>
      </div>
      <div class="task-preview-people">
        <span><small>QA</small><b>${escapeHtml(task.qa_responsible || task.assigned_to || '-')}</b></span>
        <span><small>Dev</small><b>${escapeHtml(task.developer_name || '-')}</b></span>
        <span><small>Funcional</small><b>${escapeHtml(task.functional_name || '-')}</b></span>
      </div>
      <div class="task-preview-dates">
        <span><small>Desde</small><b>${escapeHtml(task.date_from || '-')}</b></span>
        <span><small>Hasta</small><b>${escapeHtml(task.estimated_until || '-')}</b></span>
      </div>
      <div class="task-preview-state">
        <span class="task-status ${status === 'bloqueada' ? 'bad' : status === 'cerrada' ? 'ok' : 'warn'}">${stateLabels[status] || status}</span>
        <small>${escapeHtml(priorityLabels[priority] || priority)}</small>
      </div>
    </article>
  `;
}

function truncateText(value, max = 140) {
  const text = String(value || '').trim();
  return text.length > max ? `${text.slice(0, max - 1)}...` : text;
}

function historyItem(actor, action, detail, at) {
  return `
    <div class="history-item">
      <strong>${escapeHtml(actor || '-')}</strong>
      <span>${escapeHtml(action || '')} · ${formatDate(at)}</span>
      ${detail ? `<p>${escapeHtml(detail)}</p>` : ''}
    </div>
  `;
}

function renderNotifications(notifications) {
  if (!notifications.length) return '<p class="small muted">Sin notificaciones recientes.</p>';
  return `<div class="notification-list">${notifications.map(n => `
    <article class="notification ${n.read ? '' : 'unread'}">
      <strong>${escapeHtml(n.title)}</strong>
      <p class="small muted">${escapeHtml(n.action || '')} · ${formatDate(n.created_at)}</p>
    </article>
  `).join('')}</div>`;
}

function readTaskForm(prefix = 'task') {
  return {
    title: document.getElementById(`${prefix}Title`).value.trim(),
    description: document.getElementById(`${prefix}Description`).value.trim(),
    qa_responsible: document.getElementById(`${prefix}QaResponsible`).value,
    developer_name: document.getElementById(`${prefix}DeveloperName`).value.trim(),
    functional_name: document.getElementById(`${prefix}FunctionalName`).value.trim(),
    date_from: document.getElementById(`${prefix}DateFrom`).value,
    estimated_until: document.getElementById(`${prefix}EstimatedUntil`).value,
    priority: document.getElementById(`${prefix}Priority`).value,
    status: document.getElementById(`${prefix}Status`).value,
  };
}

function wireTaskFilters(tasks, users) {
  const apply = () => {
    const q = document.getElementById('taskSearch').value.trim().toLowerCase();
    const status = document.getElementById('taskFilterStatus').value;
    const priority = document.getElementById('taskFilterPriority').value;
    const qa = document.getElementById('taskFilterQa').value;
    const filtered = tasks.filter(task => {
      const haystack = [
        task.title,
        task.description,
        task.qa_responsible,
        task.assigned_to,
        task.developer_name,
        task.functional_name,
      ].join(' ').toLowerCase();
      return (!q || haystack.includes(q))
        && (!status || task.status === status)
        && (!priority || task.priority === priority)
        && (!qa || (task.qa_responsible || task.assigned_to) === qa);
    });
    document.getElementById('taskList').innerHTML = filtered.map(task => taskCard(task, users)).join('') || '<p class="small muted empty-task-list">No hay tareas con esos filtros.</p>';
    wireTaskCards(users);
  };
  ['taskSearch', 'taskFilterStatus', 'taskFilterPriority', 'taskFilterQa'].forEach(id => {
    document.getElementById(id)?.addEventListener('input', apply);
    document.getElementById(id)?.addEventListener('change', apply);
  });
}

function openCreateTaskModal(users) {
  const modal = ensureModal();
  document.getElementById('modalBody').innerHTML = `
    <h2>Crear tarea</h2>
    ${renderTaskForm(users, 'task')}
  `;
  document.getElementById('taskForm')?.addEventListener('submit', async event => {
    event.preventDefault();
    const msg = document.getElementById('taskFormMsg');
    msg.textContent = '';
    try {
      await sendJson('/api/internal-tasks/tasks', 'POST', readTaskForm());
      closeModal();
      await renderTasks();
    } catch (err) {
      msg.textContent = err.message;
    }
  });
  modal.classList.remove('hidden');
}

function wireTaskCards(users = []) {
  appView.querySelectorAll('.task-card').forEach(card => {
    const taskId = card.dataset.taskId;
    card.addEventListener('click', () => openTaskDetailModal(taskId, users));
  });
}

function openTaskFromList(taskId, users = []) {
  const card = appView.querySelector(`[data-task-id="${CSS.escape(taskId)}"]`);
  if (card) {
    card.scrollIntoView({ behavior: 'smooth', block: 'center' });
    card.classList.add('task-highlight');
    setTimeout(() => card.classList.remove('task-highlight'), 1800);
  }
  openTaskDetailModal(taskId, users);
}

async function openTaskDetailModal(taskId, users) {
  const task = (await getJson('/api/internal-tasks/tasks')).tasks.find(item => item.id === taskId);
  if (!task) return;
  const status = task.status || 'pendiente';
  const priority = task.priority || 'media';
  const history = [...(task.history || [])].reverse();
  const comments = [...(task.comments || [])].reverse();
  const modal = ensureModal();
  modal.querySelector('.modal-card')?.classList.add('task-modal-card');
  document.getElementById('modalBody').innerHTML = `
    <div class="task-detail-modal">
      <div class="task-detail-head">
        <div>
          <h2>${escapeHtml(task.title)}</h2>
          <p class="small muted">${escapeHtml(task.description || 'Sin descripcion.')}</p>
        </div>
        <span class="task-status ${status === 'bloqueada' ? 'bad' : status === 'cerrada' ? 'ok' : 'warn'}">${stateLabels[status] || status}</span>
      </div>
      <div class="task-meta detail-meta">
        <span><small>QA responsable</small><b>${escapeHtml(task.qa_responsible || task.assigned_to || '-')}</b></span>
        <span><small>Desarrollador</small><b>${escapeHtml(task.developer_name || '-')}</b></span>
        <span><small>Funcional</small><b>${escapeHtml(task.functional_name || '-')}</b></span>
        <span><small>Desde</small><b>${escapeHtml(task.date_from || '-')}</b></span>
        <span><small>Hasta estimado</small><b>${escapeHtml(task.estimated_until || '-')}</b></span>
        <span><small>Prioridad</small><b>${escapeHtml(priorityLabels[priority] || priority)}</b></span>
      </div>
      <div class="task-actions detail-actions">
        <button type="button" class="btn-edit-task">Editar tarea</button>
        <button type="button" class="btn-comment">Agregar comentario</button>
      </div>
      <section class="task-detail-section">
        <h3>Comentarios</h3>
        <div class="history-list">${comments.map(item => historyItem(item.author, 'comentario', item.text, item.at)).join('') || '<p class="small muted">Sin comentarios.</p>'}</div>
      </section>
      <section class="task-detail-section">
        <h3>Historial</h3>
        <div class="history-list">${history.map(item => historyItem(item.actor, item.action, item.detail, item.at)).join('') || '<p class="small muted">Sin historial.</p>'}</div>
      </section>
    </div>
  `;
  document.querySelector('#modalBody .btn-edit-task')?.addEventListener('click', () => openEditTaskModal(taskId, users));
  document.querySelector('#modalBody .btn-comment')?.addEventListener('click', () => openCommentModal(taskId));
  modal.classList.remove('hidden');
}

async function openEditTaskModal(taskId, users) {
  const task = (await getJson('/api/internal-tasks/tasks')).tasks.find(item => item.id === taskId);
  const qaUsers = users.filter(u => u.role === 'qa');
  const modal = ensureModal();
  document.getElementById('modalBody').innerHTML = `
    <h2>Editar tarea</h2>
    <form id="editTaskForm" class="task-edit-form">
      <label>Titulo<input id="editTitle" type="text" value="${escapeHtml(task.title || '')}" required /></label>
      <label>Descripcion<textarea id="editDescription" rows="4">${escapeHtml(task.description || '')}</textarea></label>
      <label>QA responsable<select id="editQaResponsible" required>${qaUsers.map(u => `<option value="${escapeHtml(u.username)}" ${u.username === (task.qa_responsible || task.assigned_to) ? 'selected' : ''}>${escapeHtml(u.full_name || u.username)} · ${escapeHtml(u.username)}</option>`).join('')}</select></label>
      <label>Nombre del desarrollador<input id="editDeveloperName" type="text" value="${escapeHtml(task.developer_name || '')}" /></label>
      <label>Nombre del funcional<input id="editFunctionalName" type="text" value="${escapeHtml(task.functional_name || '')}" /></label>
      <div class="form-grid two">
        <label>Fecha desde<input id="editDateFrom" type="date" value="${escapeHtml(task.date_from || '')}" /></label>
        <label>Fecha estimada hasta<input id="editEstimatedUntil" type="date" value="${escapeHtml(task.estimated_until || '')}" /></label>
      </div>
      <div class="form-grid two">
        <label>Prioridad<select id="editPriority">${priorityOptions(task.priority || 'media')}</select></label>
        <label>Estado<select id="editStatus">${statusOptions(task.status || 'pendiente')}</select></label>
      </div>
      <button type="submit">Guardar cambios</button>
      <div id="editTaskMsg" class="field-error"></div>
    </form>
  `;
  document.getElementById('editTaskForm').addEventListener('submit', async event => {
    event.preventDefault();
    const msg = document.getElementById('editTaskMsg');
    msg.textContent = '';
    try {
      await sendJson(`/api/internal-tasks/tasks/${taskId}`, 'PUT', readTaskForm('edit'));
      closeModal();
      await renderTasks();
    } catch (err) {
      msg.textContent = err.message;
    }
  });
  modal.classList.remove('hidden');
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

function openCommentModal(taskId) {
  const modal = ensureModal();
  document.getElementById('modalBody').innerHTML = `
    <h2>Agregar comentario</h2>
    <textarea id="commentText" rows="5"></textarea>
    <label class="inline-check"><input id="commentImportant" type="checkbox" /> Importante</label>
    <button id="saveComment" type="button">Guardar comentario</button>
  `;
  document.getElementById('saveComment').onclick = async () => {
    await sendJson(`/api/internal-tasks/tasks/${taskId}/comments`, 'POST', {
      text: document.getElementById('commentText').value,
      important: document.getElementById('commentImportant').checked,
    });
    closeModal();
    await renderTasks();
  };
  modal.classList.remove('hidden');
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

async function checkOverdueTasks() {
  const current = getUser();
  if (overdueAlertShown || current?.role !== 'qa') return;
  overdueAlertShown = true;
  try {
    const data = await getJson('/api/internal-tasks/overdue');
    const tasks = data.tasks || [];
    if (tasks.length) openOverdueTasksModal(tasks);
  } catch {
    // La alerta no debe bloquear el ingreso al sistema si falla la consulta.
  }
}

function openOverdueTasksModal(tasks) {
  const modal = ensureModal();
  modal.querySelector('.modal-card')?.classList.add('overdue-modal-card');
  const title = tasks.length === 1 ? 'Tenes una tarea vencida' : `Tenes ${tasks.length} tareas vencidas`;
  document.getElementById('modalBody').innerHTML = `
    <section class="overdue-alert">
      <div class="overdue-alert-head">
        <span class="overdue-icon">!</span>
        <div>
          <h2>${title}</h2>
          <p class="small muted">Revisa el estado de la tarea o alarga la fecha estimada de finalizacion.</p>
        </div>
      </div>
      <div class="overdue-task-list">
        ${tasks.map(task => `
          <article class="overdue-task-item">
            <div>
              <strong>${escapeHtml(task.title || 'Tarea sin titulo')}</strong>
              <p class="small muted">Vencio el ${escapeHtml(task.estimated_until || '-')} · Estado: ${escapeHtml(stateLabels[task.status] || task.status || '-')}</p>
            </div>
            <button type="button" data-overdue-task="${escapeHtml(task.id)}">Ir a tarea</button>
          </article>
        `).join('')}
      </div>
    </section>
  `;
  document.querySelectorAll('[data-overdue-task]').forEach(button => {
    button.addEventListener('click', async () => {
      pendingTaskOpenId = button.dataset.overdueTask;
      closeModal();
      await navigate('tareas');
    });
  });
  modal.classList.remove('hidden');
}

function formatDate(value) {
  if (!value) return '';
  const raw = String(value);
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(raw) ? raw : `${raw}Z`;
  const d = new Date(normalized);
  if (Number.isNaN(d.getTime())) return '';
  const parts = new Intl.DateTimeFormat('es-AR', {
    timeZone: 'America/Argentina/Buenos_Aires',
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(d).reduce((result, part) => ({ ...result, [part.type]: part.value }), {});
  return `${parts.day}/${parts.month}/${parts.year} · ${parts.hour}:${parts.minute}`;
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

if (user) {
  renderSidebar('inicio');
  renderHome().then(checkOverdueTasks);
}

document.getElementById('logoutBtn')?.addEventListener('click', logout);
document.getElementById('mobileMenu')?.addEventListener('click', () => document.body.classList.toggle('sidebar-open'));
