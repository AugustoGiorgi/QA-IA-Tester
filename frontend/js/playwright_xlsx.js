import { authFetch, requireAuth } from './auth.js';

requireAuth(['qa']);

const $ = id => document.getElementById(id);
let currentRecord = null;

function setStatus(text, ok = false) {
  const box = $('pwStatus');
  if (!box) return;
  box.textContent = text || '';
  box.className = `pw-status ${ok ? 'ok' : ''}`;
}

function escapeHtml(value = '') {
  return String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function fmtDate(value) {
  if (!value) return '';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString('es-AR');
}

function switchTab(tab) {
  document.querySelectorAll('[data-tab]').forEach(btn => btn.classList.toggle('active', btn.dataset.tab === tab));
  document.querySelectorAll('.pw-tab').forEach(section => section.classList.add('pw-hidden'));
  $(`tab-${tab}`)?.classList.remove('pw-hidden');
  if (tab === 'library') loadLibrary();
}

function setRecord(record) {
  currentRecord = record;
  $('pwCode').value = record?.generated_code || '';
  $('pwSelectors').textContent = JSON.stringify(record?.selectors || {}, null, 2);
  $('pwData').textContent = JSON.stringify(record?.test_data || {}, null, 2);
  $('pwNotes').innerHTML = (record?.ai_notes || []).map(note => `<p>${escapeHtml(note)}</p>`).join('') || '<span class="pw-muted">Sin notas.</span>';
  $('pwSave').disabled = !record;
  $('pwCopy').disabled = !record;
  $('pwDownload').disabled = !record;
}

async function generateAi(event) {
  event.preventDefault();
  setStatus('Generando codigo...');
  $('pwGenerate').disabled = true;
  try {
    const fd = new FormData();
    const mode = $('pwMode').value;
    fd.append('mode', mode);
    fd.append('title', $('pwTitle').value.trim());
    fd.append('requirement_id', $('pwReq').value.trim());
    fd.append('module', $('pwModule').value.trim());
    fd.append('initial_url', $('pwUrl').value.trim());
    fd.append('execution_role', $('pwRole').value.trim());
    fd.append('description', $('pwDescription').value.trim());
    fd.append('observations', $('pwObservations').value.trim());
    if (mode === 'video' && $('pwVideo').files.length) fd.append('video', $('pwVideo').files[0]);

    const res = await authFetch('/api/playwright/ai/generate', { method: 'POST', body: fd });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || 'No se pudo generar el codigo.');
    setRecord(data.record);
    setStatus('Codigo generado y guardado en la biblioteca.', true);
    await loadLibrary(false);
  } catch (err) {
    setStatus(err.message || 'Error al generar.');
  } finally {
    $('pwGenerate').disabled = false;
  }
}

async function saveCurrent() {
  if (!currentRecord) return;
  setStatus('Guardando cambios...');
  try {
    const res = await authFetch(`/api/playwright/ai/generated/${currentRecord.id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ generated_code: $('pwCode').value }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || 'No se pudo guardar.');
    setRecord(data.record);
    setStatus('Cambios guardados.', true);
    await loadLibrary(false);
  } catch (err) {
    setStatus(err.message || 'Error al guardar.');
  }
}

async function loadLibrary(showStatus = true) {
  const list = $('pwList');
  if (!list) return;
  list.innerHTML = '<p class="pw-muted">Cargando pruebas...</p>';
  try {
    const res = await authFetch('/api/playwright/ai/generated');
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || 'No se pudo cargar la biblioteca.');
    const records = data.records || [];
    list.innerHTML = records.map(record => `
      <article class="pw-item">
        <div>
          <strong>${escapeHtml(record.title)}</strong>
          <span class="pw-muted">${escapeHtml(record.requirement_id || 'Sin REQ')} · ${escapeHtml(record.module || 'Sin modulo')} · ${escapeHtml(record.created_by)} · ${fmtDate(record.updated_at || record.created_at)}</span>
        </div>
        <div class="pw-actions">
          <button class="pw-btn secondary" type="button" data-open="${escapeHtml(record.id)}">Abrir</button>
          <button class="pw-btn secondary" type="button" data-download="${escapeHtml(record.id)}">Descargar</button>
          <button class="pw-btn danger" type="button" data-delete="${escapeHtml(record.id)}">Eliminar</button>
        </div>
      </article>
    `).join('') || '<p class="pw-muted">Todavia no hay pruebas guardadas.</p>';
    list.querySelectorAll('[data-open]').forEach(btn => btn.addEventListener('click', () => openRecord(btn.dataset.open)));
    list.querySelectorAll('[data-download]').forEach(btn => btn.addEventListener('click', () => downloadRecord(btn.dataset.download)));
    list.querySelectorAll('[data-delete]').forEach(btn => btn.addEventListener('click', () => deleteRecord(btn.dataset.delete)));
    if (showStatus) setStatus('Biblioteca actualizada.', true);
  } catch (err) {
    list.innerHTML = `<p class="pw-status">${escapeHtml(err.message)}</p>`;
  }
}

async function openRecord(id) {
  const res = await authFetch(`/api/playwright/ai/generated/${id}`);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) return alert(data.detail || 'No se pudo abrir.');
  setRecord(data.record);
  switchTab('ai');
  setStatus('Prueba cargada desde biblioteca.', true);
}

async function downloadRecord(id = currentRecord?.id) {
  if (!id) return;
  const res = await authFetch(`/api/playwright/ai/generated/${id}/download`);
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    alert(data.detail || 'No se pudo descargar.');
    return;
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${(currentRecord?.title || 'playwright-test').toLowerCase().replace(/[^a-z0-9]+/g, '-')}.spec.ts`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

async function deleteRecord(id) {
  if (!confirm('Eliminar esta prueba generada?')) return;
  const res = await authFetch(`/api/playwright/ai/generated/${id}`, { method: 'DELETE' });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    alert(data.detail || 'No se pudo eliminar.');
    return;
  }
  if (currentRecord?.id === id) setRecord(null);
  await loadLibrary();
}

async function generateZip() {
  const xlsx = $('xlsx');
  const zip = $('zip');
  const out = $('result');
  const dl = $('btnDl');
  const pill = $('statusPill');
  dl.classList.add('pw-hidden');
  dl.removeAttribute('href');
  out.textContent = 'Generando proyecto...';
  pill.textContent = '';

  if (!xlsx.files.length) {
    out.textContent = 'Subi un Excel (.xlsx) con los casos.';
    return;
  }

  $('btnGen').disabled = true;
  try {
    const fd = new FormData();
    fd.append('cases_xlsx', xlsx.files[0]);
    if (zip.files.length) fd.append('selectors_zip', zip.files[0]);
    const res = await authFetch('/api/playwright/build-xlsx-v2', { method: 'POST', body: fd });
    if (!res.ok) {
      const e = await res.json().catch(() => ({}));
      throw new Error(e.detail || 'Error al generar.');
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    dl.href = url;
    dl.classList.remove('pw-hidden');
    out.textContent = 'Hecho. Abrilo en VS Code y corre npm i, npx playwright install y npx playwright test.';
    pill.textContent = 'Listo';
  } catch (err) {
    out.textContent = err.message || 'Fallo de red o CORS.';
  } finally {
    $('btnGen').disabled = false;
  }
}

document.querySelectorAll('[data-tab]').forEach(btn => btn.addEventListener('click', () => switchTab(btn.dataset.tab)));
$('pwMode')?.addEventListener('change', () => {
  const video = $('pwMode').value === 'video';
  $('videoMode').classList.toggle('hidden', !video);
  $('textMode').classList.toggle('hidden', video);
});
$('aiForm')?.addEventListener('submit', generateAi);
$('pwSave')?.addEventListener('click', saveCurrent);
$('pwCopy')?.addEventListener('click', async () => {
  await navigator.clipboard.writeText($('pwCode').value);
  setStatus('Codigo copiado.', true);
});
$('pwDownload')?.addEventListener('click', () => downloadRecord());
$('pwRefresh')?.addEventListener('click', () => loadLibrary());
$('btnGen')?.addEventListener('click', generateZip);

loadLibrary(false);
