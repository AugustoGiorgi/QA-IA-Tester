// === Validación de Calidad — header afuera del recuadro, tabla adentro ===

import { authFetch, authHeaders } from './auth.js';

const form      = document.getElementById('form');
const resultBox = document.getElementById('result');
const fileInput = document.getElementById('file');
const migrate   = document.getElementById('migrate');
const recoChat  = document.getElementById('recoChat');

let LAST_FILE = null;       // para reusar el archivo en el chat
let RECO_SID  = null;       // session_id del chat de recomendaciones
let RECO_INFO = null;       // summary devuelto por /start

// Al cargar: asegurar que el chat esté oculto
document.addEventListener('DOMContentLoaded', () => {
  hideRecoChat();
});

form?.addEventListener('submit', async (e) => {
  e.preventDefault();
  e.stopPropagation();

  const file = fileInput?.files?.[0];
  if (!file) {
    resultBox.innerHTML = `<div class="panel">Seleccioná un archivo .docx primero.</div>`;
    return;
  }
  LAST_FILE = file;

  // Mientras analiza, el chat debe estar oculto
  resultBox.innerHTML = `<div class="panel">Analizando “${escapeHtml(file.name)}”…</div>`;
  hideRecoChat();

  try {
    const res = await postFile('/api/quality', file);
    if (!res.ok) throw new Error(await safeText(res) || `HTTP ${res.status}`);
    const data = await res.json();

    renderPanel(data);
    renderMigrate(file);

    // Iniciar el chat de recomendaciones (si falla, el chat permanece oculto)
    await startRecoChat(file);
  } catch (err) {
    console.error('[calidad] error:', err);
    resultBox.innerHTML = `<div class="panel">Ocurrió un error analizando el documento. Revisá la consola para más detalle.</div>`;
  }
});

/* ---------------------------- Render ---------------------------- */
function renderPanel(data){
  // 1) Intento leer secciones del JSON; si no, caigo al Markdown
  let sections = parseSectionsFromJsonFlexible(data);
  if (!sections.length) {
    const md = String(data?.report_markdown || data?.markdown || data?.report || '');
    sections = parseFromMatrizCumplimiento(md);
  }

  // 2) Normalizo sin agregar frases genéricas
  const items = sections.map(normalizeSection);

  // 3) Totales ignorando "No aplica" (igual que el backend)
  const nonNA  = items.filter(s => !isNA(s.status));
  const totPos = nonNA.reduce((a,s)=>a+Number(s.possible||0),0);
  const totAch = nonNA.reduce((a,s)=>a+Number(s.achieved||0),0);
  const score  = detectScore(data) ?? (totPos ? (totAch/totPos)*100 : 0);
  const naCount = items.length - nonNA.length;

  const txtLink = data?.txt_path
    ? `<div class="small">Descargar TXT: <a href="/api/outputs/${encodeURIComponent(data.txt_path)}" target="_blank" rel="noopener">${escapeHtml(data.txt_path)}</a></div>`
    : '';

  const hero = `
    <div class="score-hero">
      <div>
        <div class="small muted">Puntaje total</div>
        <div class="big"><strong>${fmt(score, 0)}%</strong></div>
      </div>
      ${txtLink}
    </div>
  `;

  const subHead = `
    <div class="subpanel-head">
      <h3 style="margin:0">Matriz de Cumplimiento</h3>
      <div class="kpis">
        <span class="kpi">Secciones: <strong>${items.length}</strong>${naCount ? ` <span class="muted">(N/A: ${naCount})</span>` : ''}</span>
        <span class="kpi">Logrado: <strong>${fmt(totAch)}</strong></span>
        <span class="kpi">Posible: <strong>${fmt(totPos)}</strong></span>
      </div>
    </div>
  `;

  const table = `
    <table class="compact" id="secTable">
      <thead>
        <tr>
          <th>Sección</th>
          <th>Logrado</th>
          <th>Posible</th>
          <th>Rating</th>
          <th>Estado</th>
        </tr>
      </thead>
      <tbody>
        ${items.map((s, i) => renderRow(s, i)).join('')}
      </tbody>
    </table>
    <p class="small" style="margin-top:6px">Tip: hacé clic en una fila para ver los detalles (puntos fuertes, a mejorar y el porqué).</p>
  `;

  // 👉 Header afuera, recuadro negro (clase .output) adentro
  resultBox.innerHTML = `
    <div class="panel">
      ${hero}
      <div class="output" style="min-height:auto; white-space:normal; padding:16px;">
        ${subHead}
        ${table}
      </div>
    </div>
  `;

  attachDetailToggles(items);
}

/* ---------------------- Tabla + detalle ----------------------- */
function renderRow(s, idx){
  return `
    <tr class="clickable" data-i="${idx}">
      <td class="name-cell"><span class="chev">▸</span> ${escapeHtml(s.name)}</td>
      <td>${fmt(s.achieved)}</td>
      <td>${fmt(s.possible)}</td>
      <td>${fmt(s.rating,1)}/5</td>
      <td>${renderStatePill(s.status)}</td>
    </tr>
  `;
}

function attachDetailToggles(items){
  const tbody = document.querySelector('#secTable tbody');
  tbody.querySelectorAll('tr.clickable').forEach(tr => {
    tr.addEventListener('click', () => {
      const i = Number(tr.getAttribute('data-i'));
      const next = tr.nextElementSibling;
      const isDetail = next && next.classList.contains('detail-row');
      tr.classList.toggle('open', !isDetail);
      if (isDetail) { next.remove(); return; }
      tbody.querySelectorAll('tr.detail-row').forEach(n => n.previousElementSibling?.classList.remove('open'));
      tbody.querySelectorAll('tr.detail-row').forEach(n => n.remove());

      const s = items[i];
      const detail = document.createElement('tr');
      detail.className = 'detail-row';
      detail.innerHTML = `<td colspan="5"><div class="detail-box">${renderDetailHtml(s)}</div></td>`;
      tr.insertAdjacentElement('afterend', detail);
    });
  });
}

function renderDetailHtml(s){
  const strengths = (s.strengths && s.strengths.length)
    ? `<div class="detail-block"><h4>✅ Puntos fuertes</h4><ul class="list">${s.strengths.map(li).join('')}</ul></div>`
    : `<div class="detail-block"><h4>✅ Puntos fuertes</h4><div class="small muted">—</div></div>`;

  const improvements = (s.improvements && s.improvements.length)
    ? `<div class="detail-block"><h4>🛠️ A mejorar</h4><ul class="list">${s.improvements.map(li).join('')}</ul></div>`
    : `<div class="detail-block"><h4>🛠️ A mejorar</h4><div class="small muted">—</div></div>`;

  const grid = `<div class="detail-grid">${strengths}${improvements}</div>`;

  const why = s.rationale
    ? `<div class="small muted" style="margin-top:8px"><em>¿Por qué este puntaje?</em> ${escapeHtml(s.rationale)}</div>`
    : `<div class="small muted" style="margin-top:8px"><em>¿Por qué este puntaje?</em> Estado: ${escapeHtml(s.status)}, puntos ${fmt(s.achieved)} / ${fmt(s.possible)}, rating ${fmt(s.rating,1)}/5.</div>`;

  return grid + why;
}

/* -------------------- Normalización (sin genéricos) -------------------- */
function normalizeSection(s){
  const name = String(s?.name ?? 'Sección');
  const possible = num(s?.possible ?? s?.max_points ?? 0);
  const achieved = num(s?.achieved ?? s?.score ?? 0);
  const status = String(s?.status || s?.estado || '');
  const rating = hasNumber(s?.rating) ? Number(s.rating) : (possible ? (achieved/possible)*5 : 0);

  // SOLO lo que manda el backend. Sin frases genéricas.
  let strengths = [];
  let improvements = [];
  let rationale = '';

  strengths = arrString(firstExisting(s, 'strengths','fortalezas','strong','evidence','evidencia'));
  improvements = arrString(firstExisting(s, 'improvements','mejoras','improve','issues','problemas','observaciones'));
  rationale = String(firstExisting(s, 'rationale','why','justificacion','justificación','notes','notas') || '');

  return { name, possible, achieved, rating, status, strengths, improvements, rationale };
}
function firstExisting(obj, ...keys){
  for (const k of keys) if (obj && k in obj && obj[k]!=null && obj[k]!==undefined) return obj[k];
  return undefined;
}

/* ------------------------- Utilidades UI ------------------------- */
function renderStatePill(status=''){
  const s = String(status).toLowerCase();
  if (!s) return '';
  if (s.includes('no aplica')) return `<span class="pill na" style="background:#4b5563;color:#fff;">N/A</span>`;
  if (s.includes('falta')) return `<span class="pill bad">Falta</span>`;
  if (s.includes('parcial')) return `<span class="pill warn">Parcial</span>`;
  return `<span class="pill ok">OK</span>`;
}

/* -------------------- Parseo JSON flexible -------------------- */
function parseSectionsFromJsonFlexible(data) {
  const candidates = [
    data?.sections, data?.rubric, data?.breakdown, data?.desglose,
    data?.desglose_por_dimension, data?.dimensions, data?.scorecard,
    data?.matrix, data?.matriz, data?.quality?.sections, data?.analysis?.sections
  ];
  const arr = candidates.find(Array.isArray) || [];
  return arr.map(r => mapJsonRow(r)).filter(Boolean);

  function mapJsonRow(r) {
    if (!r || typeof r !== 'object') return null;
    const name = first(r, 'name','dimension','dimensión','seccion','sección','section') ?? 'Sección';
    const possible = pickNumber(first(r, 'possible','max','peso','weight','points','max_points'), 0);
    const rating = pickNumber(first(r, 'rating','estrellas','stars','puntaje','score_5'), NaN);
    let achieved = pickNumber(first(r, 'achieved','contribution','contribucion','contribución','points_awarded','puntos'), NaN);
    if (!hasNumber(achieved) && hasNumber(possible) && hasNumber(rating)) achieved = possible * (rating/5);
    const present = normBool(first(r, 'present','presente','presencia'));
    const status = first(r, 'estado','status') || (present === false ? 'Falta' : '');

    const strengths = arrString(first(r, 'strengths','fortalezas','strong','evidence','evidencia'));
    const improvements = arrString(first(r, 'improvements','mejoras','improve','issues','problemas','observaciones'));
    const rationale = String(first(r, 'rationale','why','justificacion','justificación','notes','notas') || '');

    return {
      name: String(name),
      possible: pickNumber(possible, 0),
      achieved: pickNumber(achieved, 0),
      rating: hasNumber(rating) ? Number(rating) : (hasNumber(possible) ? (Number(achieved||0)/Number(possible||1))*5 : 0),
      present,
      status: String(status || ''),
      strengths,
      improvements,
      rationale
    };
  }
  function first(obj, ...keys) { for (const k of keys) if (k in obj) return obj[k]; return undefined; }
}

/* --------- Parseo específico: Matriz de Cumplimiento ---------- */
function parseFromMatrizCumplimiento(md) {
  const table = extractTableAfterHeading(md, /matriz\s+de\s+cumplimiento/i);
  if (!table) return [];

  const { headers, rows } = parseMarkdownTableToObjects(table);
  const h = headers.map(normalizeNoAccent);
  const idx = {
    name:   h.findIndex(x => /seccion|sección|section|nombre/.test(x)),
    puntaje:h.findIndex(x => /puntaje|score|puntos/.test(x)),
    estado: h.findIndex(x => /estado|status/.test(x))
  };

  return rows.map(cols => {
    const name   = String(cols[idx.name] ?? 'Sección');
    const estado = String(cols[idx.estado] || '').trim();
    const [achieved, possible] = parsePuntajePair(cols[idx.puntaje] || '');
    const rating = hasNumber(possible) && possible > 0 ? (achieved / possible) * 5 : 0;
    const present = estado.toLowerCase().includes('falta') ? false : true;

    return {
      name, possible, achieved, rating, present,
      status: estado, strengths: [], improvements: [], rationale: ''
    };
  });
}
function parsePuntajePair(text) {
  const m = String(text).match(/([\d.,]+)\s*\/\s*([\d.,]+)/);
  if (!m) return [0,0];
  return [num(m[1]), num(m[2])];
}

/* --------------------- Utils varias ---------------------- */
async function postFile(path, file) {
  const fd = new FormData();
  fd.append('file', file);
  return authFetch(path, { method: 'POST', headers: authHeaders(), body: fd, credentials: 'same-origin', redirect: 'follow' });
}
async function safeText(res) { try { return await res.text(); } catch { return ''; } }
function fmt(n, d=0){ const x = Number(n||0); return x.toFixed(d); }
function hasNumber(v){ const n = Number(String(v??'').replace(',', '.')); return !Number.isNaN(n); }
function pickNumber(v, f){ const n = Number(String(v??'').replace(',', '.')); return Number.isNaN(n) ? f : n; }
function num(s){ const n = Number(String(s).replace(',', '.')); return Number.isNaN(n) ? 0 : n; }
function arrString(v){ if (Array.isArray(v)) return v.map(String); if (typeof v==='string'&&v.trim()) return v.split(/[;|]/).map(x=>x.trim()).filter(Boolean); return []; }
function normBool(v){ if (typeof v==='boolean') return v; if (v==null) return true; const s=String(v).toLowerCase(); return ['si','sí','true','1','yes','ok','parcial'].includes(s); }
function normalizeNoAccent(s){ return String(s||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().trim(); }
function escapeHtml(s=''){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function li(t){ return `<li>${escapeHtml(String(t))}</li>`; }
function isNA(status){ return String(status||'').toLowerCase().includes('no aplica'); }

function detectScore(data){
  if (hasNumber(data?.score)) return Number(String(data.score).replace(',', '.'));
  const md = String(data?.report_markdown || data?.markdown || data?.report || '');
  const m = md.match(/puntaje\s*total.*?([\d.,]+)\s*\/\s*100/i);
  if (m) return num(m[1]);
  return null;
}

/* --------- Markdown helpers ---------- */
function extractTableAfterHeading(md, headingRegex) {
  const lines = md.split('\n');
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!/^##\s+/i.test(line)) continue;
    if (!headingRegex.test(normalizeNoAccent(line))) continue;
    let j = i+1;
    while (j < lines.length && !lines[j].trim().startsWith('|')) j++;
    if (j >= lines.length) return '';
    const table = [];
    for (; j < lines.length; j++) {
      const l = lines[j];
      if (!l.trim() || !l.includes('|')) break;
      table.push(l);
    }
    return table.join('\n');
  }
  return '';
}
function parseMarkdownTableToObjects(mdTable) {
  const lines = mdTable.trim().split('\n').filter(Boolean);
  if (lines.length < 2) return { headers: [], rows: [] };
  const headers = splitRow(lines[0]);
  const rows = lines.slice(2).map(splitRow);
  return { headers, rows };
  function splitRow(row) {
    return row.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => c.trim());
  }
}

/* ---- Migrar archivo a otras pantallas (SIN recuadro) ---- */
function renderMigrate(file) {
  if (!migrate) return;
  migrate.innerHTML = `
    <div style="margin-top:12px">
      <div class="small" style="margin-bottom:8px">Usar este archivo en:</div>
      <div style="display:flex; gap:8px; flex-wrap:wrap">
        <button id="goEnt" type="button" class="btn btn-secondary">Entendimiento de Documento</button>
        <button id="goCasos" type="button" class="btn btn-secondary">Diseño de Casos de Prueba</button>
      </div>
    </div>
  `;
  const go = async (url) => { await saveFileForTransfer(file); location.href = url; };
  document.getElementById('goEnt')?.addEventListener('click', () => go('/app/entendimiento.html'));
  document.getElementById('goCasos')?.addEventListener('click', () => go('/app/casos.html'));
}
async function saveFileForTransfer(file) {
  const buf = await file.arrayBuffer();
  const bytes = new Uint8Array(buf);
  let binary = '';
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  const b64 = btoa(binary);
  sessionStorage.setItem('xfer_file', JSON.stringify({
    name: file.name, type: file.type || 'application/octet-stream',
    lastModified: file.lastModified || Date.now(), b64
  }));
}

/* ===================== Chat de Recomendaciones ===================== */

function hideRecoChat() {
  RECO_SID = null;
  RECO_INFO = null;
  if (recoChat) {
    recoChat.style.display = 'none';
    recoChat.innerHTML = '';
  }
}

async function startRecoChat(file) {
  try {
    const res = await postFile('/api/reco-chat/start', file);
    if (!res.ok) throw new Error(await safeText(res) || `HTTP ${res.status}`);
    const data = await res.json();
    RECO_SID = data?.session_id || null;
    RECO_INFO = data?.summary || null;
    renderRecoChat(); // 👈 solo se muestra si /start anduvo bien
  } catch (err) {
    console.warn('[reco-chat] no se pudo iniciar:', err);
    // No mostrar el chat: solo aviso suave debajo de la matriz
    try {
      resultBox.insertAdjacentHTML('beforeend',
        `<div class="small muted" style="margin-top:8px">No se pudo iniciar el chat para este archivo.</div>`
      );
    } catch {}
  }
}

function renderRecoChat() {
  if (!recoChat) return;
  if (!RECO_SID) { hideRecoChat(); return; }

  // Sin info extra ni mensaje inicial: caja vacía lista para chatear
  recoChat.style.display = 'block';
  recoChat.innerHTML = `
    <div class="chat">
      <div class="chat-head">
        <h3 style="margin:0">Chat de Recomendaciones</h3>
      </div>

      <div id="recoMsgs" class="chat-box"></div>

      <form id="recoForm" class="chat-form" autocomplete="off">
        <input id="recoInput" type="text" placeholder="Ej.: ¿qué significa ser más específico en Riesgos?" required />
        <button id="recoSend" type="submit" class="btn">Enviar</button>
      </form>

      <div class="small muted">
        Tip: también podés proponer opciones y te digo si sirven (sí/no) y cómo redactarlas.
      </div>
    </div>
  `;

  const f = document.getElementById('recoForm');
  f?.addEventListener('submit', onRecoSubmit);
}

function sysMsg(t) { return `<div class="msg sys"><div class="b">ⓘ</div><div class="t">${escapeHtml(String(t))}</div></div>`; }
function userMsg(t){ return `<div class="msg user"><div class="b">Tú</div><div class="t">${escapeHtml(String(t))}</div></div>`; }
function botMsg(t) { return `<div class="msg bot"><div class="b">AI</div><div class="t">${escapeHtml(String(t))}</div></div>`; }

async function onRecoSubmit(e) {
  e.preventDefault();
  const input = document.getElementById('recoInput');
  const box   = document.getElementById('recoMsgs');
  const q = (input?.value || '').trim();
  if (!q || !RECO_SID) return;

  box.insertAdjacentHTML('beforeend', userMsg(q));
  input.value = '';
  box.scrollTop = box.scrollHeight;

  try {
    const res = await authFetch('/api/reco-chat/ask', {
      method: 'POST',
      headers: authHeaders({'Content-Type':'application/json'}),
      body: JSON.stringify({ session_id: RECO_SID, question: q })
    });
    const data = await res.json();
    const a = String(data?.answer || 'No pude responder en este momento.');
    box.insertAdjacentHTML('beforeend', botMsg(a));
  } catch (err) {
    console.error('[reco-chat] ask error:', err);
    box.insertAdjacentHTML('beforeend', sysMsg('Ocurrió un error al responder.'));
  } finally {
    box.scrollTop = box.scrollHeight;
  }
}
