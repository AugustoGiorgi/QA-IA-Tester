import { authFetch } from './auth.js';

const form = document.getElementById('importForm');
const fileInput = document.getElementById('file');
const fileName = document.getElementById('fileName');
const fileError = document.getElementById('fileError');
const dropzone = document.getElementById('dropzone');
const statusPanel = document.getElementById('statusPanel');
const submitBtn = document.getElementById('submitBtn');
const resetBtn = document.getElementById('resetBtn');

const allowedExtensions = ['.csv', '.xlsx'];

function selectedFile() {
  return fileInput.files?.[0] || null;
}

function extensionOf(name = '') {
  const dot = name.lastIndexOf('.');
  return dot >= 0 ? name.slice(dot).toLowerCase() : '';
}

function setFileError(message = '') {
  fileError.textContent = message;
  dropzone.classList.toggle('invalid', Boolean(message));
}

function updateFileLabel() {
  const file = selectedFile();
  if (!file) {
    fileName.textContent = 'Formatos permitidos: .csv y .xlsx';
    setFileError('');
    return false;
  }

  const ext = extensionOf(file.name);
  const isValid = allowedExtensions.includes(ext);
  fileName.textContent = file.name;
  setFileError(isValid ? '' : 'Formato no permitido. Subi un archivo .csv o .xlsx.');
  return isValid;
}

function showStatus(html, mode = '') {
  statusPanel.className = `panel-block module-panel status-panel ${mode}`.trim();
  statusPanel.innerHTML = html;
}

function renderLoading(file) {
  showStatus(`
    <div class="loading-row">
      <span class="spinner"></span>
      <div>
        <h2>Procesando importacion</h2>
        <p class="small muted">Leyendo ${escapeHtml(file.name)}, validando filas y sincronizando con MongoDB.</p>
      </div>
    </div>
  `, 'processing');
}

function renderResult(data) {
  const hasErrors = Number(data.error_count || 0) > 0;
  const errors = Array.isArray(data.errors) ? data.errors : [];
  const report = data.error_report
    ? `<a class="link" href="/api/outputs/${encodeURIComponent(data.error_report)}" target="_blank" rel="noopener">Descargar reporte de errores</a>`
    : '';

  const errorTable = hasErrors ? `
    <div class="error-preview">
      <h3>Errores detectados</h3>
      <table class="compact">
        <thead>
          <tr><th>Fila</th><th>Campo</th><th>Error</th><th>Valor</th></tr>
        </thead>
        <tbody>
          ${errors.map(err => `
            <tr>
              <td>${escapeHtml(err.row)}</td>
              <td>${escapeHtml(err.field)}</td>
              <td>${escapeHtml(err.error)}</td>
              <td>${escapeHtml(err.value || '')}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
      ${report ? `<div class="small report-link">${report}</div>` : ''}
    </div>
  ` : '';

  showStatus(`
    <div class="result-head">
      <div>
        <h2>${hasErrors ? 'Importacion finalizada con observaciones' : 'Importacion exitosa'}</h2>
        <p class="small muted">Coleccion: ${escapeHtml(data.collection)} · Clave unica: ${escapeHtml(data.unique_key)}</p>
      </div>
      <span class="pill ${hasErrors ? 'warn' : 'ok'}">${hasErrors ? 'Con errores' : 'OK'}</span>
    </div>
    <div class="summary-grid">
      ${metric('Filas leidas', data.total_rows)}
      ${metric('Filas validas', data.valid_rows)}
      ${metric('Creados', data.created)}
      ${metric('Actualizados', data.updated)}
      ${metric('Vacias', data.empty_rows)}
      ${metric('Errores', data.error_count)}
    </div>
    ${errorTable}
  `, hasErrors ? 'warning' : 'success');
}

function renderError(message) {
  showStatus(`
    <h2>No se pudo importar</h2>
    <p>${escapeHtml(message)}</p>
  `, 'error');
}

function metric(label, value) {
  return `
    <div class="metric">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value ?? 0)}</strong>
    </div>
  `;
}

function buildFormData() {
  const fd = new FormData();
  fd.append('file', selectedFile());
  fd.append('collection', document.getElementById('collection').value.trim());
  fd.append('unique_key', document.getElementById('uniqueKey').value.trim());
  fd.append('required_fields', document.getElementById('requiredFields').value.trim());
  fd.append('field_types', document.getElementById('fieldTypes').value.trim());
  return fd;
}

function resetForm() {
  form.reset();
  updateFileLabel();
  statusPanel.className = 'panel-block module-panel status-panel hidden';
  statusPanel.innerHTML = '';
}

function escapeHtml(value = '') {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

fileInput.addEventListener('change', updateFileLabel);
resetBtn.addEventListener('click', resetForm);

['dragenter', 'dragover'].forEach(eventName => {
  dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropzone.classList.add('dragover');
  });
});

['dragleave', 'drop'].forEach(eventName => {
  dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropzone.classList.remove('dragover');
  });
});

dropzone.addEventListener('drop', (event) => {
  const file = event.dataTransfer?.files?.[0];
  if (!file) return;
  const transfer = new DataTransfer();
  transfer.items.add(file);
  fileInput.files = transfer.files;
  updateFileLabel();
});

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const file = selectedFile();
  if (!file) {
    setFileError('Selecciona un archivo para importar.');
    return;
  }
  if (!updateFileLabel()) return;

  submitBtn.disabled = true;
  renderLoading(file);

  try {
    const res = await authFetch('/api/import-data', {
      method: 'POST',
      body: buildFormData(),
    });
    const text = await res.text();
    const data = text ? JSON.parse(text) : {};
    if (!res.ok) {
      throw new Error(data?.detail || `HTTP ${res.status}`);
    }
    renderResult(data);
  } catch (err) {
    renderError(err.message || 'Error inesperado.');
  } finally {
    submitBtn.disabled = false;
  }
});
