import { API_BASE, postFile } from './api.js';
import { saveFileForTransfer, consumeTransferredFile, setFileInput } from './transfer.js';

const form = document.getElementById('form');
const out = document.getElementById('out');
const migrate = document.getElementById('migrate');
const fileInput = document.getElementById('file');
let generating = false;

// Si llegamos desde otra pantalla con un archivo migrado, precargarlo
const incoming = consumeTransferredFile();
if (incoming) {
  setFileInput(fileInput, incoming);
  const info = document.createElement('div');
  info.className = 'small';
  info.textContent = `Se cargó automáticamente el archivo: ${incoming.name}`;
  migrate.prepend(info);
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  if (generating) return;
  const file = fileInput.files[0];
  if (!file) return alert('Seleccioná un .docx');
  generating = true;
  const submit = form.querySelector('button[type="submit"]');
  if (submit) submit.disabled = true;
  out.textContent = 'Procesando...';
  try {
    const res = await postFile('/api/explain', file);
    const text = await res.text();
    out.textContent = text;

    // 👇 Avisar explícitamente que ya está la explicación (el chat escucha esto)
    window.dispatchEvent(new CustomEvent('df:explanation-ready'));

    renderMigrate(file);
  } catch (err) {
    out.textContent = 'Error: ' + err.message;
  } finally {
    generating = false;
    if (submit) submit.disabled = false;
  }
});

function renderMigrate(file) {
  migrate.innerHTML = `
    <div class="title">Usar este archivo en:</div>
    <div class="actions">
      <button id="goCasos">Diseño de Casos de Prueba</button>
    </div>
  `;
  document.getElementById('goCasos').onclick = async () => {
    await saveFileForTransfer(file);
    location.href = '/app/casos.html';
  };
}
