// Si está servido por FastAPI en el puerto 8000, usamos el mismo origen.
import { requestWithCancellation } from './auth.js?v=20261001-2';

export const API_BASE = window.location.origin;

export function authHeaders(extra = {}) {
  return { ...extra };
}

/**
 * Sube un archivo con campos extra al backend vía POST.
 * @param {string} url - Ruta relativa del endpoint (ej: "/api/feedback")
 * @param {File} file - Archivo a enviar
 * @param {Object} extraFields - Campos adicionales opcionales
 * @returns {Promise<Response>} Respuesta cruda del fetch
 */
export async function postFile(url, file, extraFields = {}, options = {}) {
  const fd = new FormData();
  fd.append("file", file);

  for (const [k, v] of Object.entries(extraFields)) {
    fd.append(k, v ?? "");
  }

  const res = await requestWithCancellation(`${API_BASE}${url}`, {
    method: "POST",
    headers: authHeaders(),
    body: fd,
    ...options,
  });

  if (!res.ok) {
    const contentType = res.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || data.error || `Error HTTP ${res.status}`);
    }
    await res.text();
    if (res.status >= 500) {
      throw new Error(`El servidor no pudo completar la solicitud (HTTP ${res.status}). Revisá los logs de Render; puede haber sido interrumpida durante la generación.`);
    }
    throw new Error(`La solicitud falló (HTTP ${res.status}). Revisá el archivo e intentá nuevamente.`);
  }

  return res;
}
