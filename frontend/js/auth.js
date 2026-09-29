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
export function authFetch(url, options = {}) { return fetch(url, options); }
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
