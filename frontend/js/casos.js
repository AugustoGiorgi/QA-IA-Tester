import { postFile } from './api.js';
import { saveFileForTransfer, consumeTransferredFile, setFileInput } from './transfer.js';

const form = document.getElementById('form');
const result = document.getElementById('result');
const migrate = document.getElementById('migrate');
const fileInput = document.getElementById('file');

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
  result.textContent = 'Generando Excel...';
  const file = fileInput.files[0];
  if (!file) return alert('Seleccioná un .docx');
  try {
    const res = await postFile('/api/testcases', file);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `casos_${file.name.replace(/\.docx$/,'')}.xlsx`;
    a.click();
    URL.revokeObjectURL(url);
    result.textContent = 'Descarga iniciada.';

    renderMigrate(file);
  } catch (err) {
    result.textContent = 'Error: ' + err.message;
  }
});

function renderMigrate(file) {
  migrate.innerHTML = `
    <div class="title">Usar este archivo en:</div>
    <div class="actions">
      <button id="goEnt">Entendimiento de Documento</button>
      <button id="goCalidad">Validación de Calidad</button>
    </div>
  `;
  document.getElementById('goEnt').onclick = async () => {
    await saveFileForTransfer(file);
    location.href = '/app/entendimiento.html';
  };
  document.getElementById('goCalidad').onclick = async () => {
    await saveFileForTransfer(file);
    location.href = '/app/calidad.html';
  };
}
