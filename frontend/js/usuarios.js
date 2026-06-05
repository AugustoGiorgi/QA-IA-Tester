import { authFetch, authHeaders, getUser } from './auth.js';

const usersBox = document.getElementById('usersBox');
const usersCount = document.getElementById('usersCount');
const btnReload = document.getElementById('btnReload');
const btnOpenCreate = document.getElementById('btnOpenCreate');
const searchInput = document.getElementById('userSearch');

const currentUser = getUser();
let allUsers = [];
let modal = null;
let modalBody = null;

const roleLabels = {
  qa: 'QA',
  funcional: 'Funcional',
  lider: 'Lider',
};

async function requestJson(url, options = {}) {
  const res = await authFetch(url, {
    ...options,
    headers: authHeaders({ 'Content-Type': 'application/json', ...(options.headers || {}) }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || 'Operacion fallida.');
  return data;
}

async function loadUsers() {
  usersBox.textContent = 'Cargando usuarios...';
  usersCount.textContent = 'Cargando usuarios...';
  try {
    const data = await requestJson('/api/auth/users');
    allUsers = data.users || [];
    renderUsers();
  } catch (err) {
    usersBox.textContent = err.message;
    usersCount.textContent = 'No se pudo cargar el listado.';
  }
}

function renderUsers() {
  const term = searchInput.value.trim().toLowerCase();
  const users = allUsers.filter(user => {
    const haystack = `${user.username || ''} ${user.full_name || ''} ${user.email || ''} ${roleLabels[user.role] || user.role || ''}`.toLowerCase();
    return haystack.includes(term);
  });

  usersCount.textContent = `${users.length} de ${allUsers.length} usuarios`;

  if (!users.length) {
    usersBox.className = 'users-list small muted';
    usersBox.textContent = term ? 'No se encontraron usuarios para esa busqueda.' : 'No hay usuarios cargados.';
    return;
  }

  usersBox.className = 'users-list';
  usersBox.innerHTML = users.map(user => `
    <article class="user-card" data-user="${escapeHtml(user.username)}">
      <div class="user-main">
        <div class="user-avatar">${escapeHtml(initials(user))}</div>
        <div>
          <strong>${escapeHtml(user.username)}</strong>
          <span>${escapeHtml(user.full_name || 'Sin nombre cargado')}</span>
          <span>${escapeHtml(user.email || 'Sin email')}</span>
        </div>
      </div>
      <div class="user-role">
        <span class="pill">${escapeHtml(roleLabels[user.role] || user.role)}</span>
        <span class="${user.active ? 'ok' : 'bad'}">${user.active ? 'Activo' : 'Inactivo'}</span>
      </div>
      <div class="user-actions">
        <button type="button" class="btn-secondary" data-action="edit">Editar</button>
        <button type="button" class="btn-danger" data-action="delete" ${user.username === currentUser?.username ? 'disabled' : ''}>Eliminar</button>
      </div>
    </article>
  `).join('');

  usersBox.querySelectorAll('.user-card').forEach(card => {
    const username = card.dataset.user;
    const user = allUsers.find(item => item.username === username);
    card.querySelector('[data-action="edit"]')?.addEventListener('click', () => openUserModal('edit', user));
    card.querySelector('[data-action="delete"]')?.addEventListener('click', () => openDeleteModal(user));
  });
}

function openUserModal(mode, user = null) {
  const isCreate = mode === 'create';
  ensureModal();
  modalBody.innerHTML = `
    <div class="modal-head">
      <h2>${isCreate ? 'Crear usuario' : 'Editar usuario'}</h2>
      <p class="small muted">${isCreate ? 'Completá los datos del nuevo acceso.' : `Editando ${escapeHtml(user.username)}.`}</p>
    </div>
    <form id="modalUserForm" class="modal-user-form" autocomplete="off">
      <label>
        Usuario
        <input id="modalUsername" name="modal_user_${Date.now()}" type="text" value="${escapeHtml(user?.username || '')}" ${isCreate ? 'required' : 'disabled'} autocomplete="off" />
      </label>
      <label>
        Nombre
        <input id="modalFullName" name="modal_name_${Date.now()}" type="text" value="${escapeHtml(user?.full_name || '')}" autocomplete="off" />
      </label>
      <label>
        Email
        <input id="modalEmail" name="modal_email_${Date.now()}" type="text" value="${escapeHtml(user?.email || '')}" required autocomplete="off" />
      </label>
      <label>
        Rol
        <select id="modalRole" required>${roleOptions(user?.role || 'qa')}</select>
      </label>
      <label class="inline-check modal-active">
        <input id="modalActive" type="checkbox" ${user?.active !== false ? 'checked' : ''} />
        Activo
      </label>
      <label>
        Contraseña
        <input id="modalPassword" name="modal_pass_${Date.now()}" type="password" ${isCreate ? 'required' : ''} placeholder="${isCreate ? '' : 'Dejar vacía para no cambiar'}" autocomplete="new-password" />
      </label>
      <div id="modalMsg" class="field-error"></div>
      <div class="modal-actions">
        <button type="button" class="btn-secondary" data-modal-cancel>Cancelar</button>
        <button type="submit">${isCreate ? 'Crear usuario' : 'Guardar cambios'}</button>
      </div>
    </form>
  `;

  showModal();
  clearAutofill();

  modalBody.querySelector('[data-modal-cancel]').addEventListener('click', closeModal);
  modalBody.querySelector('#modalUserForm').addEventListener('submit', event => saveModalUser(event, mode, user));
}

async function saveModalUser(event, mode, user) {
  event.preventDefault();
  const msg = document.getElementById('modalMsg');
  msg.textContent = '';
  const password = document.getElementById('modalPassword').value;
  const payload = {
    username: document.getElementById('modalUsername').value.trim(),
    email: document.getElementById('modalEmail').value.trim(),
    full_name: document.getElementById('modalFullName').value.trim(),
    role: document.getElementById('modalRole').value,
    active: document.getElementById('modalActive').checked,
  };
  if (password) payload.password = password;

  try {
    if (mode === 'create') {
      await requestJson('/api/auth/users', { method: 'POST', body: JSON.stringify(payload) });
    } else {
      delete payload.username;
      await requestJson(`/api/auth/users/${encodeURIComponent(user.username)}`, { method: 'PUT', body: JSON.stringify(payload) });
    }
    closeModal();
    await loadUsers();
  } catch (err) {
    msg.textContent = err.message;
  }
}

function openDeleteModal(user) {
  ensureModal();
  modalBody.innerHTML = `
    <div class="modal-head">
      <h2>Eliminar usuario</h2>
      <p class="small muted">Esta acción elimina el acceso de forma permanente.</p>
    </div>
    <div class="delete-summary">
      <strong>${escapeHtml(user.username)}</strong>
      <span>${escapeHtml(user.email || '')}</span>
    </div>
    <div id="modalMsg" class="field-error"></div>
    <div class="modal-actions">
      <button type="button" class="btn-secondary" data-modal-cancel>Cancelar</button>
      <button type="button" class="btn-danger" id="confirmDelete">Eliminar usuario</button>
    </div>
  `;

  showModal();
  modalBody.querySelector('[data-modal-cancel]').addEventListener('click', closeModal);
  modalBody.querySelector('#confirmDelete').addEventListener('click', async () => {
    const msg = document.getElementById('modalMsg');
    msg.textContent = '';
    try {
      await requestJson(`/api/auth/users/${encodeURIComponent(user.username)}`, { method: 'DELETE' });
      closeModal();
      await loadUsers();
    } catch (err) {
      msg.textContent = err.message;
    }
  });
}

function showModal() {
  ensureModal();
  modal.classList.remove('hidden');
  modal.setAttribute('aria-hidden', 'false');
}

function closeModal() {
  modal.classList.add('hidden');
  modal.setAttribute('aria-hidden', 'true');
  modalBody.innerHTML = '';
}

function ensureModal() {
  if (modal && modalBody) return;
  document.body.insertAdjacentHTML('beforeend', `
    <div id="userModal" class="modal hidden" aria-hidden="true">
      <div class="modal-card user-modal-card">
        <button id="modalClose" type="button" class="modal-close">×</button>
        <div id="modalBody"></div>
      </div>
    </div>
  `);
  modal = document.getElementById('userModal');
  modalBody = document.getElementById('modalBody');
  document.getElementById('modalClose').addEventListener('click', closeModal);
  modal.addEventListener('click', event => {
    if (event.target === modal) closeModal();
  });
}

function clearAutofill() {
  setTimeout(() => {
    ['modalUsername', 'modalFullName', 'modalEmail', 'modalPassword'].forEach(id => {
      const el = document.getElementById(id);
      if (el && !el.defaultValue) el.value = '';
    });
    document.getElementById('modalUsername')?.focus();
  }, 80);
}

function roleOptions(selected) {
  return Object.entries(roleLabels).map(([value, label]) => (
    `<option value="${value}" ${value === selected ? 'selected' : ''}>${label}</option>`
  )).join('');
}

function initials(user) {
  const source = user.full_name || user.username || '?';
  return source.split(/\s+/).filter(Boolean).slice(0, 2).map(part => part[0]).join('').toUpperCase();
}

function escapeHtml(value = '') {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

btnOpenCreate.addEventListener('click', () => openUserModal('create'));
btnReload.addEventListener('click', loadUsers);
searchInput.addEventListener('input', renderUsers);

loadUsers();
