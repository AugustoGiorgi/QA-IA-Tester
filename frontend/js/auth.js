// Shared context for existing QA modules. No account or session is persisted.
const shared = Object.freeze({ username: 'shared', role: 'qa', full_name: 'Equipo QA', shared: true });
export const ROLE_LABELS = { qa: 'QA' };
export function getToken() { return ''; }
export function getUser() { return shared; }
export function clearSession() {
  localStorage.removeItem('qa_auth_token');
  localStorage.removeItem('qa_auth_user');
}
export function setSession() { clearSession(); }
export function authHeaders(extra = {}) { return { ...extra }; }

const cancellablePaths = [
  '/api/testcases', '/api/explain', '/api/quality', '/api/postman/analyze',
  '/api/karate/generate', '/api/playwright/generate',
  '/api/reco-chat/start', '/api/reco-chat/ask', '/api/chat/start', '/api/chat/ask',
  '/api/functional/coach/start', '/api/functional/coach/message', '/api/functional/coach/confirm', '/api/functional/coach/finish',
  '/api/chat-proyectos',
];
let activeCancelableRequest = null;
let cancelNoticeTimer = null;

function isCancelable(url, options) {
  const method = (options.method || 'GET').toUpperCase();
  const path = new URL(url, window.location.origin).pathname;
  return method !== 'GET' && (
    cancellablePaths.some(item => path === item || path.startsWith(`${item}/`))
  );
}

function showRequestControl(controller) {
  document.getElementById('request-cancel-control')?.remove();
  clearTimeout(cancelNoticeTimer);
  const box = document.createElement('div');
  box.id = 'request-cancel-control';
  box.className = 'request-cancel-control';
  box.setAttribute('role', 'status');
  box.innerHTML = '<span>Proceso en curso</span><button type="button">Cancelar proceso</button>';
  box.querySelector('button').addEventListener('click', () => {
    controller.abort();
    box.querySelector('span').textContent = 'Cancelando solicitud…';
    box.querySelector('button').disabled = true;
  }, { once: true });
  document.body.append(box);
  return box;
}

export async function requestWithCancellation(url, options = {}) {
  if (!isCancelable(url, options)) return fetch(url, options);
  activeCancelableRequest?.abort();
  const controller = new AbortController();
  activeCancelableRequest = controller;
  const control = showRequestControl(controller);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } catch (error) {
    if (controller.signal.aborted) {
      const cancelled = new Error('Se canceló la espera en esta pantalla. El servidor podría seguir procesando.');
      cancelled.name = 'AbortError';
      throw cancelled;
    }
    throw error;
  } finally {
    if (activeCancelableRequest === controller) activeCancelableRequest = null;
    if (controller.signal.aborted) {
      control.querySelector('span').textContent = 'Se canceló la espera en esta pantalla. El servidor podría seguir procesando.';
      control.querySelector('button').remove();
      cancelNoticeTimer = setTimeout(() => control.remove(), 9000);
    } else {
      control.remove();
    }
  }
}

export function authFetch(url, options = {}) { return requestWithCancellation(url, options); }
export function trackActivity() {}
export function hasRole(roles = []) { return roles.includes('qa'); }
export function requireAuth(roles = []) {
  if (roles.length && !hasRole(roles)) {
    location.replace('/app/index.html');
    return null;
  }
  return shared;
}
export function logout() { location.replace('/app/index.html'); }
clearSession();
