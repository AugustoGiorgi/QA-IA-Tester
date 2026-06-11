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

function apiError(data, fallback) {
  if (typeof data?.detail === 'string') return data.detail;
  if (Array.isArray(data?.detail)) {
    return data.detail.map(item => item?.msg || JSON.stringify(item)).join(' ');
  }
  return fallback;
}

function fmtDate(value) {
  if (!value) return '';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString('es-AR');
}

function renderReviewList(id, items, emptyText, ok = false) {
  const list = $(id);
  const values = Array.isArray(items) ? items.filter(Boolean) : [];
  list.classList.toggle('ok', ok || !values.length);
  list.innerHTML = values.length
    ? values.map(item => `<li>${escapeHtml(item)}</li>`).join('')
    : `<li>${escapeHtml(emptyText)}</li>`;
}

function renderVariableEditor(id, values, kind) {
  const container = $(id);
  const entries = Object.entries(values || {});
  container.innerHTML = entries.length
    ? entries.map(([key, value]) => {
      const text = String(value ?? '');
      const todo = /todo/i.test(text);
      return `
        <div class="pw-variable-row">
          <label title="${escapeHtml(key)}">${escapeHtml(key)}</label>
          <input
            class="${todo ? 'todo' : ''}"
            data-variable-kind="${kind}"
            data-variable-key="${escapeHtml(key)}"
            value="${escapeHtml(text)}"
            autocomplete="off"
          />
        </div>
      `;
    }).join('')
    : '<span class="pw-muted">Sin variables detectadas.</span>';
  container.querySelectorAll('input').forEach(input => {
    input.addEventListener('input', () => input.classList.toggle('todo', /todo/i.test(input.value)));
  });
}

function collectVariables(kind) {
  return Object.fromEntries(
    [...document.querySelectorAll(`[data-variable-kind="${kind}"]`)]
      .map(input => [input.dataset.variableKey, input.value]),
  );
}

function replaceObjectDeclaration(code, variableName, values) {
  const declaration = new RegExp(`\\bconst\\s+${variableName}\\s*=\\s*\\{`);
  const match = declaration.exec(code);
  if (!match) return code;
  const start = match.index;
  const open = code.indexOf('{', match.index);
  let depth = 0;
  let quote = '';
  let escaped = false;
  let close = -1;
  for (let index = open; index < code.length; index += 1) {
    const char = code[index];
    if (quote) {
      if (escaped) escaped = false;
      else if (char === '\\') escaped = true;
      else if (char === quote) quote = '';
      continue;
    }
    if (char === '"' || char === "'" || char === '`') {
      quote = char;
      continue;
    }
    if (char === '{') depth += 1;
    if (char === '}') {
      depth -= 1;
      if (depth === 0) {
        close = index;
        break;
      }
    }
  }
  if (close < 0) return code;
  const end = code[close + 1] === ';' ? close + 2 : close + 1;
  const replacement = `const ${variableName} = ${JSON.stringify(values, null, 2)};`;
  return `${code.slice(0, start)}${replacement}${code.slice(end)}`;
}

function applyVariablesToCode(showStatus = true) {
  if (!currentRecord) return;
  const selectors = collectVariables('selector');
  const testData = collectVariables('data');
  let code = $('pwCode').value;
  code = replaceObjectDeclaration(code, 'selectors', selectors);
  code = replaceObjectDeclaration(code, 'testData', testData);
  $('pwCode').value = code;
  currentRecord = { ...currentRecord, selectors, test_data: testData, generated_code: code };
  if (showStatus) setStatus('Variables aplicadas al codigo. Guarda los cambios cuando termines.', true);
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
  renderVariableEditor('pwSelectors', record?.selectors, 'selector');
  renderVariableEditor('pwData', record?.test_data, 'data');
  $('pwNotes').innerHTML = (record?.ai_notes || []).map(note => `<p>${escapeHtml(note)}</p>`).join('') || '<span class="pw-muted">Sin notas.</span>';
  $('pwQualityScore').textContent = record ? `${Number(record.quality_score || 0)}%` : '-';
  const reviewStatus = record?.review_status || (record ? 'needs_review' : '');
  $('pwReviewStatus').textContent = reviewStatus === 'ready' ? 'Listo' : reviewStatus ? 'Borrador para revisar' : '';
  $('pwReviewStatus').classList.toggle('ready', reviewStatus === 'ready');
  const covered = Array.isArray(record?.covered_steps) ? record.covered_steps : [];
  $('pwCoverage').textContent = covered.length
    ? `Pasos cubiertos por la revision: ${covered.join(', ')}`
    : 'La cobertura no pudo determinarse automaticamente.';
  renderReviewList(
    'pwManualActions',
    record?.manual_actions,
    'No se detectaron pendientes manuales adicionales.',
  );
  renderReviewList(
    'pwWarnings',
    record?.warnings,
    'La revision no detecto patrones de riesgo.',
  );
  $('pwSave').disabled = !record;
  $('pwAudit').disabled = !record;
  $('pwApplyVariables').disabled = !record;
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
    fd.append('description', mode === 'video' ? $('pwVideoDescription').value.trim() : $('pwDescription').value.trim());
    fd.append('observations', $('pwObservations').value.trim());
    fd.append('codegen', $('pwCodegen').value.trim());
    fd.append('selector_context', $('pwSelectorContext').value.trim());
    if (mode === 'video' && $('pwVideo').files.length) fd.append('video', $('pwVideo').files[0]);

    const res = await authFetch('/api/playwright/ai/generate', { method: 'POST', body: fd });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(apiError(data, 'No se pudo generar el codigo.'));
    setRecord(data.record);
    const pending = Array.isArray(data.record?.manual_actions) ? data.record.manual_actions.length : 0;
    setStatus(
      data.record?.review_status === 'needs_review'
        ? `Borrador generado y guardado. Hay ${pending} puntos para revisar antes de ejecutarlo.`
        : 'Codigo generado, auditado y guardado en la biblioteca.',
      true,
    );
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
    const selectors = collectVariables('selector');
    const testData = collectVariables('data');
    const res = await authFetch(`/api/playwright/ai/generated/${currentRecord.id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        generated_code: $('pwCode').value,
        selectors,
        test_data: testData,
      }),
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

async function auditCurrent() {
  if (!currentRecord) return;
  setStatus('Guardando y auditando nuevamente el codigo...');
  $('pwAudit').disabled = true;
  try {
    const selectors = collectVariables('selector');
    const testData = collectVariables('data');
    const saveRes = await authFetch(`/api/playwright/ai/generated/${currentRecord.id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        generated_code: $('pwCode').value,
        selectors,
        test_data: testData,
      }),
    });
    const saveData = await saveRes.json().catch(() => ({}));
    if (!saveRes.ok) throw new Error(saveData.detail || 'No se pudo guardar antes de auditar.');
    const res = await authFetch(`/api/playwright/ai/generated/${currentRecord.id}/audit`, { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(apiError(data, 'No se pudo auditar el codigo.'));
    setRecord(data.record);
    setStatus(
      data.record?.review_status === 'needs_review'
        ? 'Auditoria completada. El codigo sigue como borrador y conserva los pendientes detectados.'
        : 'Codigo corregido y recalificado por la auditoria automatica.',
      true,
    );
    await loadLibrary(false);
  } catch (err) {
    setStatus(err.message || 'Error al auditar.');
  } finally {
    $('pwAudit').disabled = !currentRecord;
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
$('pwAudit')?.addEventListener('click', auditCurrent);
$('pwApplyVariables')?.addEventListener('click', () => applyVariablesToCode());
$('pwCopy')?.addEventListener('click', async () => {
  await navigator.clipboard.writeText($('pwCode').value);
  setStatus('Codigo copiado.', true);
});
$('pwDownload')?.addEventListener('click', () => downloadRecord());
$('pwRefresh')?.addEventListener('click', () => loadLibrary());
$('btnGen')?.addEventListener('click', generateZip);

loadLibrary(false);
