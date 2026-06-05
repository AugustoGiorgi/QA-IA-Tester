// frontend/js/chat_entendimiento.js
import { authFetch, authHeaders } from './auth.js';

const out = document.getElementById('out');
const fileInput = document.getElementById('file');

// Bloque UI de chat (invisible hasta que haya sesión)
const chatBox = document.createElement('section');
chatBox.id = 'chatbox';
chatBox.style.display = 'none';
chatBox.innerHTML = `
  <h3>Chat sobre este documento</h3>
  <div id="chatLog" class="chat-log"></div>
  <div class="chat-row">
    <input id="chatQ" type="text" placeholder="Preguntá algo sobre el documento..." />
    <button id="chatSend">Enviar</button>
  </div>
  <p class="small">Si no está en el documento, te aviso que no hay info suficiente.</p>
`;
out.insertAdjacentElement('afterend', chatBox);

let sessionId = null;
let lastPrepared = "";

// Helpers UI
const logDiv = () => document.getElementById('chatLog');
const addMsg = (role, text) => {
  const div = document.createElement('div');
  div.className = 'msg ' + role;
  div.textContent = text;
  logDiv().appendChild(div);
  logDiv().scrollTop = logDiv().scrollHeight;
};

// Crea la sesión cuando haya explicación y archivo
async function ensureSessionReady(source = 'unknown') {
  try {
    if (sessionId) return;

    const file = fileInput?.files?.[0];
    const explanation = (out.textContent || "").trim();

    console.debug('[chat] ensureSessionReady from:', source, { hasFile: !!file, explLen: explanation.length });

    if (!file || !explanation) return;

    if (lastPrepared === explanation) return;
    lastPrepared = explanation;

    const fd = new FormData();
    fd.append('file', file);
    fd.append('explanation', explanation);

    const res = await authFetch('/api/chat/start', { method: 'POST', headers: authHeaders(), body: fd });
    if (!res.ok) {
      const errText = await res.text();
      console.error('No pude iniciar sesión de chat:', errText);
      chatBox.style.display = 'block';
      addMsg('assistant', `No se pudo iniciar el chat (${res.status}). Revisá backend: /api/chat/start`);
      return;
    }
    const data = await res.json();
    sessionId = data.session_id;
    chatBox.style.display = 'block';
    console.debug('[chat] sessionId:', sessionId);
  } catch (e) {
    console.error('ensureSessionReady error:', e);
    chatBox.style.display = 'block';
    addMsg('assistant', 'Error iniciando chat: ' + e.message);
  }
}

// 1) Detectar cambios en la explicación (MutationObserver)
const obs = new MutationObserver(() => ensureSessionReady('mutation'));
obs.observe(out, { childList: true, subtree: true, characterData: true });

// 2) También escuchar el evento explícito del otro script
window.addEventListener('df:explanation-ready', () => ensureSessionReady('custom-event'));

// 3) Y cuando cambia el archivo seleccionado
fileInput.addEventListener('change', () => ensureSessionReady('file-change'));

// Enviar preguntas
async function sendQuestion(q) {
  if (!sessionId) await ensureSessionReady('sendQuestion');
  if (!sessionId) {
    alert('Todavía no tengo la sesión lista. Revisá consola/Network.');
    return;
  }
  addMsg('user', q);

  try {
    const res = await authFetch('/api/chat/ask', {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ session_id: sessionId, question: q }),
    });
    if (!res.ok) {
      const err = await res.text();
      addMsg('assistant', `Error (${res.status}): ${err}`);
      return;
    }
    const data = await res.json();
    addMsg('assistant', data.answer || 'Sin respuesta');
  } catch (e) {
    addMsg('assistant', 'Error de red: ' + e.message);
  }
}

const input = chatBox.querySelector('#chatQ');
const btn = chatBox.querySelector('#chatSend');

btn.addEventListener('click', () => {
  const q = (input.value || '').trim();
  if (!q) return;
  input.value = '';
  sendQuestion(q).catch(console.error);
});

input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    e.preventDefault();
    btn.click();
  }
});
