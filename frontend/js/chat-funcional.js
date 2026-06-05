import { authFetch, authHeaders, requireAuth } from './auth.js';

requireAuth(['funcional']);

const API = window.location.origin;

const chat = document.getElementById('chat');
const input = document.getElementById('input');
const btnStart = document.getElementById('btn-start');
const btnSend = document.getElementById('btn-send');
const btnOk = document.getElementById('btn-ok');
const btnChange = document.getElementById('btn-change');
const btnFinishNo = document.getElementById('btn-finish-no');
const btnFinishYes = document.getElementById('btn-finish-yes');
const result = document.getElementById('result');

/* 🎤 mic */
const btnMic = document.getElementById('btn-mic');
const micStatus = document.getElementById('mic-status');
let recognition = null;
let recognizing = false;
let manualStop = false; // ← para saber si cortamos nosotros
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition || null;

let sessionId = null;
let sectionStatus = null; // awaiting_input | awaiting_corrections | awaiting_confirmation | ok
let canConfirm = false;

function push(role, text) {
  const div = document.createElement('div');
  div.className = 'msg ' + (role === 'user' ? 'user' : 'assistant');
  div.textContent = text;
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
}

function setUI() {
  btnSend.disabled = !sessionId;
  btnOk.disabled = !(sessionId && canConfirm);
  btnChange.disabled = !(sessionId && canConfirm);
  btnFinishNo.disabled = !sessionId;
  btnFinishYes.disabled = !sessionId;
}

/* 🎤 helpers */
function setMicUI(active, text) {
  recognizing = !!active;
  if (btnMic) btnMic.classList.toggle('recording', !!active);
  if (micStatus) micStatus.textContent = text || (active ? 'Escuchando…' : '');
}
function initRecognition() {
  if (!SpeechRecognition) return null;
  const rec = new SpeechRecognition();
  rec.lang = 'es-AR';        // cambiá si preferís: 'es-ES', 'es-MX', etc.
  rec.interimResults = true; // muestra progreso “en vivo”
  rec.continuous = true;     // sesión continua (Chrome igual puede cortar por silencio)
  return rec;
}

/* —— Corrector básico de puntuación (conservador, sin IA) —— */
function basicPunctuate(text) {
  if (!text) return text;
  let t = text.trim();

  // 1) Normalizar espacios múltiples
  t = t.replace(/\s+/g, ' ');

  // 2) Quitar espacios antes de coma/punto y asegurar uno después si falta
  t = t.replace(/\s+([,.])/g, '$1');        // "hola  ,mundo" -> "hola, mundo"
  t = t.replace(/([,.])(\S)/g, '$1 $2');    // "hola,mundo" -> "hola, mundo"

  // 3) Insertar coma suave antes de "pero / sin embargo / aunque" si no hay puntuación ya
  t = t.replace(/(\S)\s+(pero|sin embargo|aunque)\b/gi, (m, prev, conj) => {
    // si ya hay puntuación justo antes, no agregamos coma
    if (/[.,;:]/.test(prev)) return `${prev} ${conj}`;
    return `${prev}, ${conj}`;
  });

  // 4) Mayúscula inicial del texto y después de punto
  t = t.replace(/^([a-zñáéíóúü])/, (m) => m.toUpperCase());
  t = t.replace(/([.!?]\s+)([a-zñáéíóúü])/g, (_, pre, c) => pre + c.toUpperCase());

  // 5) Punto final si corresponde (no si ya termina con ., !, ?, …)
  if (t.length > 3 && !/[.!?…]$/.test(t)) {
    t += '.';
  }

  return t;
}

btnStart.addEventListener('click', async () => {
  try {
    const res = await authFetch(`${API}/api/functional/coach/start`, {
      method: 'POST',
      headers: authHeaders({'Content-Type': 'application/json'}),
      body: JSON.stringify({ titulo: null })
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    sessionId = data.session_id;
    sectionStatus = data.status;
    canConfirm = sectionStatus === 'awaiting_confirmation';
    push('assistant', data.message);
    setUI();
  } catch (e) {
    push('assistant', 'Error al iniciar: ' + e.message);
  }
});

btnSend.addEventListener('click', async () => {
  const text = input.value.trim();
  if (!text || !sessionId) return;
  push('user', text);
  input.value = '';

  try {
    const res = await authFetch(`${API}/api/functional/coach/message`, {
      method: 'POST',
      headers: authHeaders({'Content-Type': 'application/json'}),
      body: JSON.stringify({ session_id: sessionId, text })
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    sectionStatus = data.status;
    canConfirm = sectionStatus === 'awaiting_confirmation';
    push('assistant', data.assistant);
    setUI();
  } catch (e) {
    push('assistant', 'Error: ' + e.message);
  }
});

btnOk.addEventListener('click', async () => {
  if (!sessionId) return;
  try {
    const res = await authFetch(`${API}/api/functional/coach/confirm`, {
      method: 'POST',
      headers: authHeaders({'Content-Type': 'application/json'}),
      body: JSON.stringify({ session_id: sessionId, ok: true })
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    canConfirm = false;
    if (data.done) {
      push('assistant', data.assistant || '¡Listo! Terminaste todas las secciones.');
    } else {
      push('assistant', data.assistant);
    }
    sectionStatus = 'awaiting_input';
    setUI();
  } catch (e) {
    push('assistant', 'Error: ' + e.message);
  }
});

btnChange.addEventListener('click', async () => {
  if (!sessionId) return;
  const changes = input.value.trim();
  if (!changes) {
    push('assistant', 'Escribí qué querés cambiar en el cuadro de texto y volvé a tocar “Pedir cambios”.');
    return;
  }
  push('user', '(Pedir cambios) ' + changes);
  input.value = '';
  try {
    const res = await authFetch(`${API}/api/functional/coach/confirm`, {
      method: 'POST',
      headers: authHeaders({'Content-Type': 'application/json'}),
      body: JSON.stringify({ session_id: sessionId, ok: false, changes })
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    sectionStatus = data.status;
    canConfirm = sectionStatus === 'awaiting_confirmation';
    push('assistant', data.assistant);
    setUI();
  } catch (e) {
    push('assistant', 'Error: ' + e.message);
  }
});

btnFinishNo.addEventListener('click', async () => {
  if (!sessionId) return;
  try {
    const res = await authFetch(`${API}/api/functional/coach/finish`, {
      method: 'POST',
      headers: authHeaders({'Content-Type': 'application/json'}),
      body: JSON.stringify({ session_id: sessionId, generate_doc: false })
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    result.innerHTML = `<div class="output"><p>${data.message || 'Chat finalizado.'}</p></div>`;
  } catch (e) {
    result.innerHTML = `<div class="output">Error: ${e.message}</div>`;
  }
});

btnFinishYes.addEventListener('click', async () => {
  if (!sessionId) return;
  try {
    const res = await authFetch(`${API}/api/functional/coach/finish`, {
      method: 'POST',
      headers: authHeaders({'Content-Type': 'application/json'}),
      body: JSON.stringify({ session_id: sessionId, generate_doc: true })
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    if (!data.ok) {
      const missing = (data.missing || []).join(', ');
      result.innerHTML = `<div class="output"><p><b>No se puede generar:</b> faltan secciones obligatorias confirmadas.</p><p>Completá: ${missing}</p></div>`;
      return;
    }
    const dl = data.docx_filename
      ? `<p><b>Descargar:</b> <a class="link" target="_blank" href="${API}/api/outputs/docx/${data.docx_filename}">${data.docx_filename}</a></p>`
      : '';
    const score = data.quality_score != null ? `<p>Puntaje de calidad: <b>${Number(data.quality_score).toFixed(1)}%</b></p>` : '';
    const rep = data.quality_report_txt
      ? `<p class="small">Reporte de calidad: <a class="link" target="_blank" href="${API}/api/outputs/${data.quality_report_txt}">${data.quality_report_txt}</a></p>`
      : '';

    result.innerHTML = `
      <div class="output">
        <p>¡Documento generado!</p>
        ${dl}
        ${score}
        ${rep}
        <p class="small">Ubicación local: <code>backend/data/outputs/${data.docx_filename || ''}</code></p>
      </div>
    `;
  } catch (e) {
    result.innerHTML = `<div class="output">Error: ${e.message}</div>`;
  }
});

/* 🎤 inicialización & handlers */
if (btnMic) {
  btnMic.addEventListener('click', () => {
    if (!SpeechRecognition) {
      push('assistant', 'Tu navegador no soporta dictado. Probá en Chrome/Edge o activá HTTPS.');
      return;
    }
    if (!recognition) recognition = initRecognition();

    if (!recognizing) {
      // Iniciar captura continua
      manualStop = false;
      try {
        setMicUI(true, 'Escuchando…');
        recognition.start();
      } catch (e) {
        setMicUI(false, '');
        push('assistant', 'No pude acceder al micrófono (revisá permisos).');
      }
    } else {
      // Cortar manualmente (no auto-reiniciar)
      manualStop = true;
      setMicUI(false, '');
      try { recognition.stop(); } catch {}
    }
  });
}

if (SpeechRecognition) {
  if (!recognition) recognition = initRecognition();

  let finalTranscript = '';
  recognition.onstart = () => setMicUI(true, 'Escuchando…');

  recognition.onerror = (e) => {
    // errores comunes: 'not-allowed', 'no-speech', 'aborted', 'network'
    if (e.error === 'not-allowed') {
      manualStop = true;
      setMicUI(false, '');
      push('assistant', 'Permiso de mic denegado.');
    }
    // Si hay otros errores, dejamos que onend maneje reinicio si corresponde
  };

  recognition.onend = () => {
    // Si NO lo cortaste vos, reintenta para mantener la sesión “continua”
    if (!manualStop) {
      setTimeout(() => {
        if (!recognizing) return; // si se apagó en el medio
        try {
          setMicUI(true, 'Escuchando…');
          recognition.start();
        } catch {
          setMicUI(false, '');
        }
      }, 150);
    } else {
      // fin manual
      setMicUI(false, '');
    }

    // Volcar lo que haya al input con puntuación básica
    if (finalTranscript.trim()) {
      const improved = basicPunctuate(finalTranscript.trim());
      input.value = (input.value ? input.value + ' ' : '') + improved;
      input.focus();
      // si querés auto-enviar al terminar una “tanda”, podrías:
      // btnSend.click();
      finalTranscript = '';
    }
  };

  recognition.onresult = (event) => {
    let interim = '';
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const txt = event.results[i][0].transcript;
      if (event.results[i].isFinal) finalTranscript += txt;
      else interim += txt;
    }
    if (interim) setMicUI(true, 'Escuchando… ' + interim);
  };
}

setUI();
