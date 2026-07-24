const TOKEN_KEY = 'qa_auth_token';
const USER_KEY = 'qa_auth_user';

export const ROLE_LABELS = {
  qa: 'QA',
  funcional: 'Funcional',
  lider: 'Líder',
};

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || '';
}

export function getUser() {
  try {
    return JSON.parse(localStorage.getItem(USER_KEY) || 'null');
  } catch {
    return null;
  }
}

export function setSession(token, user) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

export function authHeaders(extra = {}) {
  const token = getToken();
  return token ? { ...extra, Authorization: `Bearer ${token}` } : extra;
}

export async function authFetch(url, options = {}) {
  const headers = authHeaders(options.headers || {});
  const res = await fetch(url, { ...options, headers });
  if (res.status === 401) {
    clearSession();
    location.href = '/app/login.html';
  }
  return res;
}

export function trackActivity(payload = {}) {
  const token = getToken();
  const user = getUser();
  if (!token || !user) return;
  const body = JSON.stringify({
    action: payload.action || 'Accion frontend',
    module: payload.module || inferModule(location.pathname),
    detail: payload.detail || document.title || location.pathname,
    metadata: payload.metadata || {},
  });
  fetch('/api/activity', {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body,
    keepalive: true,
  }).catch(() => {});
}

function inferModule(value = '') {
  const text = String(value).toLowerCase();
  if (text.includes('playwright')) return 'playwright';
  if (text.includes('postman')) return 'postman';
  if (text.includes('quality-records') || text.includes('registro')) return 'registro-ia';
  if (text.includes('internal-tasks') || text.includes('tareas')) return 'tareas';
  if (text.includes('auth/users') || text.includes('usuarios')) return 'usuarios';
  if (text.includes('import')) return 'importacion';
  if (text.includes('quality') || text.includes('calidad')) return 'calidad-funcional';
  if (text.includes('testcases') || text.includes('casos')) return 'casos';
  if (text.includes('chat') || text.includes('explain') || text.includes('entendimiento')) return 'entendimiento';
  return 'app';
}

export function hasRole(allowedRoles = []) {
  const user = getUser();
  return Boolean(user && allowedRoles.includes(user.role));
}

export function requireAuth(allowedRoles = []) {
  const user = getUser();
  const token = getToken();
  if (!user || !token) {
    location.href = '/app/login.html';
    return null;
  }
  if (allowedRoles.length && !allowedRoles.includes(user.role)) {
    location.href = '/app/index.html';
    return null;
  }
  return user;
}

export async function logout() {
  try {
    await authFetch('/api/auth/logout', { method: 'POST' });
  } catch {}
  clearSession();
  location.href = '/app/login.html';
}
