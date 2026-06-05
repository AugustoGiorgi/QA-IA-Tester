import { authFetch, authHeaders, getUser } from './auth.js';

const currentUser = getUser();

if (currentUser?.role === 'lider') {
  mountProjectChatWidget();
}

function mountProjectChatWidget() {
  const widget = document.createElement('section');
  widget.className = 'project-chat-widget';
  widget.innerHTML = `
    <button id="projectChatBubble" class="project-chat-bubble" type="button" aria-label="Abrir asistente de proyectos">
      AI
    </button>
    <div id="projectChatPanel" class="project-chat-panel" aria-label="Asistente de proyectos">
      <header class="project-chat-header">
        <div>
          <strong>Asistente de Proyectos</strong>
          <span>Consulta datos cargados e informacion operativa</span>
        </div>
        <div class="project-chat-actions">
          <button id="projectChatReset" type="button" aria-label="Reiniciar chat">↻</button>
          <button id="projectChatClose" type="button" aria-label="Cerrar chat">×</button>
        </div>
      </header>
      <div id="projectChatMessages" class="project-chat-messages"></div>
      <form id="projectChatForm" class="project-chat-form" autocomplete="off">
        <input id="projectChatInput" type="text" placeholder="Escribi tu consulta..." />
        <button id="projectChatSend" type="submit">Enviar</button>
      </form>
    </div>
  `;

  document.body.appendChild(widget);

  const bubble = document.getElementById('projectChatBubble');
  const panel = document.getElementById('projectChatPanel');
  const close = document.getElementById('projectChatClose');
  const reset = document.getElementById('projectChatReset');
  const form = document.getElementById('projectChatForm');
  const input = document.getElementById('projectChatInput');
  const send = document.getElementById('projectChatSend');
  const messages = document.getElementById('projectChatMessages');

  let sessionId = newSessionId();
  let typingNode = null;

  bubble.addEventListener('click', () => {
    panel.classList.toggle('active');
    if (panel.classList.contains('active')) input.focus();
  });

  close.addEventListener('click', () => panel.classList.remove('active'));

  reset.addEventListener('click', () => {
    messages.innerHTML = '';
    sessionId = newSessionId();
    input.focus();
  });

  form.addEventListener('submit', async event => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text) return;

    addMessage(messages, text, 'user');
    input.value = '';
    send.disabled = true;
    typingNode = addMessage(messages, 'Escribiendo...', 'bot');

    try {
      const res = await authFetch('/api/chat-proyectos', {
        method: 'POST',
        headers: authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ message: text, session_id: sessionId }),
      });
      const data = await res.json().catch(() => ({}));
      typingNode?.remove();
      typingNode = null;

      if (!res.ok || !data.success) {
        addMessage(messages, data.error || data.detail || 'No se pudo obtener respuesta.', 'bot');
        return;
      }

      addMessage(messages, data.response, 'bot', true);
    } catch {
      typingNode?.remove();
      typingNode = null;
      addMessage(messages, 'Error de conexion.', 'bot');
    } finally {
      send.disabled = false;
      input.focus();
    }
  });
}

function newSessionId() {
  return `lider_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

function addMessage(container, text, type, allowHtml = false) {
  const node = document.createElement('div');
  node.className = type === 'user' ? 'project-msg user' : 'project-msg bot';
  if (allowHtml) node.innerHTML = sanitizeBotHtml(text);
  else node.textContent = text;
  container.appendChild(node);
  container.scrollTop = container.scrollHeight;
  return node;
}

function sanitizeBotHtml(value = '') {
  const template = document.createElement('template');
  template.innerHTML = String(value);
  template.content.querySelectorAll('script, iframe, object, embed, link, style').forEach(node => node.remove());
  template.content.querySelectorAll('*').forEach(node => {
    [...node.attributes].forEach(attr => {
      const name = attr.name.toLowerCase();
      const val = attr.value.toLowerCase();
      if (name.startsWith('on') || val.startsWith('javascript:')) node.removeAttribute(attr.name);
    });
  });
  return template.innerHTML;
}
