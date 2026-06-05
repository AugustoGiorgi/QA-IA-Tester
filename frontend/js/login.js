import { setSession } from './auth.js';

const form = document.getElementById('loginForm');
const errorBox = document.getElementById('loginError');
const btn = document.getElementById('btnLogin');
const username = document.getElementById('username');
const password = document.getElementById('password');

setTimeout(() => {
  username.value = '';
  password.value = '';
  username.focus();
}, 80);

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  errorBox.textContent = '';
  btn.disabled = true;

  try {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        username: username.value.trim(),
        password: password.value,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || 'No se pudo iniciar sesión.');
    setSession(data.token, data.user);
    location.href = '/app/index.html';
  } catch (err) {
    errorBox.textContent = err.message;
  } finally {
    btn.disabled = false;
  }
});
