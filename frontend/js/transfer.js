// frontend/js/transfer.js
// Guarda/recupera el archivo seleccionado entre pantallas usando sessionStorage.
// Nota: sessionStorage suele permitir ~5–10MB. Si tu .docx supera eso, no podrá migrarse.

const KEY_B64 = "qa_file_b64";
const KEY_NAME = "qa_file_name";
const KEY_TYPE = "qa_file_type";

function arrayBufferToBase64(buffer) {
  let binary = "";
  const bytes = new Uint8Array(buffer);
  const len = bytes.byteLength;
  for (let i = 0; i < len; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary);
}

function base64ToUint8Array(b64) {
  const binary = atob(b64);
  const len = binary.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

export async function saveFileForTransfer(file) {
  const ab = await file.arrayBuffer();
  const b64 = arrayBufferToBase64(ab);
  sessionStorage.setItem(KEY_B64, b64);
  sessionStorage.setItem(KEY_NAME, file.name || "documento.docx");
  sessionStorage.setItem(KEY_TYPE, file.type || "application/vnd.openxmlformats-officedocument.wordprocessingml.document");
}

export function consumeTransferredFile() {
  const b64 = sessionStorage.getItem(KEY_B64);
  if (!b64) return null;
  const name = sessionStorage.getItem(KEY_NAME) || "documento.docx";
  const type = sessionStorage.getItem(KEY_TYPE) || "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

  const bytes = base64ToUint8Array(b64);
  const blob = new Blob([bytes], { type });
  let file = null;
  try {
    file = new File([blob], name, { type });
  } catch {
    // Para navegadores sin soporte de File constructor
    file = blob;
    file.name = name;
  }
  // Consumimos (limpiamos) para no dejar basura
  sessionStorage.removeItem(KEY_B64);
  sessionStorage.removeItem(KEY_NAME);
  sessionStorage.removeItem(KEY_TYPE);
  return file;
}

export function setFileInput(fileInput, file) {
  const dt = new DataTransfer();
  dt.items.add(file);
  fileInput.files = dt.files;
}
