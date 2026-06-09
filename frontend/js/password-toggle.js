function enhancePasswordInput(input) {
  if (!(input instanceof HTMLInputElement) || input.dataset.passwordToggle === 'ready') return;

  input.dataset.passwordToggle = 'ready';
  const wrapper = document.createElement('span');
  wrapper.className = 'password-input-wrap';
  input.parentNode.insertBefore(wrapper, input);
  wrapper.appendChild(input);

  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'password-toggle';
  button.setAttribute('aria-label', 'Mostrar contrasena');
  button.setAttribute('aria-pressed', 'false');
  button.title = 'Mostrar contrasena';
  button.innerHTML = '<span class="password-eye-icon" aria-hidden="true"></span>';
  wrapper.appendChild(button);

  button.addEventListener('click', () => {
    const visible = input.type === 'text';
    input.type = visible ? 'password' : 'text';
    button.classList.toggle('is-visible', !visible);
    button.setAttribute('aria-pressed', String(!visible));
    button.setAttribute('aria-label', visible ? 'Mostrar contrasena' : 'Ocultar contrasena');
    button.title = visible ? 'Mostrar contrasena' : 'Ocultar contrasena';
    input.focus({ preventScroll: true });
    const position = input.value.length;
    input.setSelectionRange?.(position, position);
  });
}

function enhancePasswords(root = document) {
  if (root instanceof HTMLInputElement && root.matches('input[type="password"]')) {
    enhancePasswordInput(root);
  }
  root.querySelectorAll?.('input[type="password"]').forEach(enhancePasswordInput);
}

enhancePasswords();

new MutationObserver(mutations => {
  mutations.forEach(mutation => {
    mutation.addedNodes.forEach(node => {
      if (node instanceof Element) enhancePasswords(node);
    });
  });
}).observe(document.documentElement, { childList: true, subtree: true });
