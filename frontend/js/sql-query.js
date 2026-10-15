import { authFetch } from './auth.js';

const $ = id => document.getElementById(id);
const state = { threadId: '', threads: [], messages: [], catalogLoaded: false };

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
}

async function request(url, options = {}) {
  const response = await authFetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `Error ${response.status}`);
  return data;
}

function showNotice(message = '') { $('chatNotice').textContent = message; }

function renderMessages() {
  const host = $('messages');
  if (!state.messages.length) {
    host.innerHTML = `<div class="sql-empty">Escribí lo que necesitás consultar. Si falta una regla de negocio, el chat te va a pedir esa aclaración.</div>`;
    return;
  }
  host.innerHTML = state.messages.map((message, index) => {
    const role = message.role === 'user' ? 'user' : 'assistant';
    const sql = role === 'assistant' && message.sql ? `<pre>${escapeHtml(message.sql)}</pre><button class="sql-button secondary sql-copy" type="button" data-copy="${index}">Copiar SELECT</button>` : '';
    const assumptions = message.assumptions?.length ? `<ul class="sql-assumptions">${message.assumptions.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>` : '';
    return `<article class="sql-message" data-role="${role}"><strong>${role === 'user' ? 'Vos' : message.status === 'ready' ? 'SELECT listo para revisar' : 'Asistente QA'}</strong><p>${escapeHtml(message.content)}</p>${sql}${assumptions}</article>`;
  }).join('');
  host.querySelectorAll('[data-copy]').forEach(button => button.addEventListener('click', async () => {
    const sql = state.messages[Number(button.dataset.copy)]?.sql;
    if (!sql) return;
    try { await navigator.clipboard.writeText(sql); button.textContent = 'Copiado'; }
    catch { showNotice('No se pudo copiar automáticamente. Seleccioná el SQL y copialo.'); }
  }));
}

function renderThreadOptions() {
  $('threadSelect').innerHTML = state.threads.map(thread => `<option value="${escapeHtml(thread.id)}">${escapeHtml(thread.title || 'Nueva consulta')}</option>`).join('');
  $('threadSelect').value = state.threadId;
}

async function loadCatalogStatus() {
  const data = await request('/api/sql-assistant/status');
  state.catalogLoaded = Boolean(data.loaded);
  $('catalogBadge').textContent = data.loaded
    ? `${data.table_count} tablas · ${data.column_count} columnas · ${data.relation_count} relaciones`
    : 'Falta importar el esquema';
  $('messageForm').hidden = !state.catalogLoaded;
  if (!state.catalogLoaded) $('schemaPanel').hidden = false;
}

async function refreshThreads(selectId = '') {
  const data = await request('/api/sql-assistant/threads');
  state.threads = data.threads || [];
  if (selectId) state.threadId = selectId;
  else if (!state.threads.some(thread => thread.id === state.threadId)) state.threadId = state.threads[0]?.id || '';
  if (!state.threadId) {
    const created = await request('/api/sql-assistant/threads', { method: 'POST' });
    state.threads.unshift(created.thread);
    state.threadId = created.thread.id;
  }
  renderThreadOptions();
  await loadThread(state.threadId);
}

async function loadThread(id) {
  if (!id) return;
  const data = await request(`/api/sql-assistant/threads/${encodeURIComponent(id)}`);
  state.threadId = id;
  state.messages = data.thread.messages || [];
  renderThreadOptions();
  renderMessages();
}

async function loadKnowledge() {
  const data = await request('/api/sql-assistant/knowledge');
  const list = $('knowledgeList');
  const items = data.items || [];
  list.innerHTML = items.length ? items.map(item => `
    <article class="sql-knowledge-item" data-id="${escapeHtml(item.id)}">
      <div><h3>${escapeHtml(item.term)}</h3><p>${escapeHtml(item.definition)}</p></div>
      <div class="sql-knowledge-actions"><button class="sql-button secondary" type="button" data-edit>Editar</button><button class="sql-button secondary" type="button" data-delete>Eliminar</button></div>
    </article>`).join('') : '<div class="sql-empty">Todavía no hay definiciones compartidas.</div>';
  list.querySelectorAll('[data-id]').forEach(row => {
    const item = items.find(entry => entry.id === row.dataset.id);
    row.querySelector('[data-edit]').addEventListener('click', () => openKnowledgeForm(item));
    row.querySelector('[data-delete]').addEventListener('click', async () => {
      if (!confirm(`¿Eliminar la definición “${item.term}”?`)) return;
      try { await request(`/api/sql-assistant/knowledge/${item.id}`, { method: 'DELETE' }); await loadKnowledge(); }
      catch (error) { alert(error.message); }
    });
  });
}

function openKnowledgeForm(item = null) {
  $('knowledgeForm').hidden = false;
  $('knowledgeForm').dataset.id = item?.id || '';
  $('knowledgeTerm').value = item?.term || '';
  $('knowledgeDefinition').value = item?.definition || '';
  $('knowledgeTerm').focus();
}

$('schemaToggle').addEventListener('click', () => { $('schemaPanel').hidden = !$('schemaPanel').hidden; });
$('schemaClose').addEventListener('click', () => { if (state.catalogLoaded) $('schemaPanel').hidden = true; });
$('schemaForm').addEventListener('submit', async event => {
  event.preventDefault();
  const button = $('schemaForm').querySelector('button[type=submit]');
  const status = $('schemaStatus');
  button.disabled = true;
  status.textContent = 'Importando los metadatos…';
  try {
    const form = new FormData();
    form.append('columns_file', $('columnsFile').files[0]);
    form.append('relations_file', $('relationsFile').files[0]);
    const data = await request('/api/sql-assistant/schema', { method: 'POST', body: form });
    state.catalogLoaded = true;
    $('catalogBadge').textContent = `${data.table_count} tablas · ${data.column_count} columnas · ${data.relation_count} relaciones`;
    $('messageForm').hidden = false;
    status.textContent = 'Esquema importado. Ya podés iniciar una consulta.';
    setTimeout(() => { $('schemaPanel').hidden = true; }, 1400);
  } catch (error) { status.textContent = error.message; }
  finally { button.disabled = false; }
});

$('newThread').addEventListener('click', async () => {
  try {
    const data = await request('/api/sql-assistant/threads', { method: 'POST' });
    state.messages = [];
    await refreshThreads(data.thread.id);
    showNotice('');
  } catch (error) { showNotice(error.message); }
});

$('threadSelect').addEventListener('change', event => loadThread(event.target.value).catch(error => showNotice(error.message)));
$('messageForm').addEventListener('submit', async event => {
  event.preventDefault();
  const input = $('userMessage');
  const button = $('sendMessage');
  const message = input.value.trim();
  if (!message || !state.threadId) return;
  button.disabled = true;
  showNotice('Analizando la consulta…');
  try {
    const data = await request(`/api/sql-assistant/threads/${encodeURIComponent(state.threadId)}/messages`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message }),
    });
    input.value = '';
    state.messages = data.thread.messages || [];
    renderMessages();
    await refreshThreads(state.threadId);
    showNotice('');
  } catch (error) { showNotice(error.message); }
  finally { button.disabled = false; input.focus(); }
});

document.querySelectorAll('.sql-tab').forEach(button => button.addEventListener('click', async () => {
  document.querySelectorAll('.sql-tab').forEach(tab => { tab.classList.toggle('active', tab === button); tab.setAttribute('aria-selected', String(tab === button)); });
  const showChat = button.dataset.tab === 'chat';
  $('chatView').hidden = !showChat;
  $('knowledgeView').hidden = showChat;
  if (!showChat) await loadKnowledge().catch(error => { $('knowledgeList').textContent = error.message; });
}));

$('addKnowledge').addEventListener('click', () => openKnowledgeForm());
$('cancelKnowledge').addEventListener('click', () => { $('knowledgeForm').hidden = true; });
$('knowledgeForm').addEventListener('submit', async event => {
  event.preventDefault();
  const id = $('knowledgeForm').dataset.id;
  const payload = { term: $('knowledgeTerm').value.trim(), definition: $('knowledgeDefinition').value.trim() };
  try {
    await request(id ? `/api/sql-assistant/knowledge/${id}` : '/api/sql-assistant/knowledge', {
      method: id ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    });
    $('knowledgeForm').reset();
    $('knowledgeForm').hidden = true;
    await loadKnowledge();
  } catch (error) { alert(error.message); }
});

async function init() {
  try {
    await loadCatalogStatus();
    await refreshThreads();
  } catch (error) { showNotice(error.message); }
  renderMessages();
}
init();
