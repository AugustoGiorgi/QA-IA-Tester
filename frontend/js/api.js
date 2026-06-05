// Si está servido por FastAPI en el puerto 8000, usamos el mismo origen.
export const API_BASE = window.location.origin;

export function authHeaders(extra = {}) {
  const token = localStorage.getItem("qa_auth_token") || "";
  return token ? { ...extra, Authorization: `Bearer ${token}` } : extra;
}

/**
 * Sube un archivo con campos extra al backend vía POST.
 * @param {string} url - Ruta relativa del endpoint (ej: "/api/feedback")
 * @param {File} file - Archivo a enviar
 * @param {Object} extraFields - Campos adicionales opcionales
 * @returns {Promise<Response>} Respuesta cruda del fetch
 */
export async function postFile(url, file, extraFields = {}) {
  const fd = new FormData();
  fd.append("file", file);

  for (const [k, v] of Object.entries(extraFields)) {
    fd.append(k, v ?? "");
  }

  const res = await fetch(`${API_BASE}${url}`, {
    method: "POST",
    headers: authHeaders(),
    body: fd,
  });

  if (!res.ok) {
    throw new Error(await res.text());
  }

  return res;
}
