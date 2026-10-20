import { authFetch } from './auth.js?v=20261001-2';

export async function showDataQueries(container, module, draftId, missing) {
  const queryable = missing.filter(item => item.queryable);
  container.hidden = !queryable.length;
  container.dataset.draftId = draftId;
  if (!queryable.length) return;

  container.replaceChildren();
  const heading = document.createElement('strong');
  heading.textContent = 'Consultas para conseguir los datos';
  const notice = document.createElement('p');
  notice.textContent = 'SELECT de solo lectura. Revisalas y ejecutalas manualmente en Oracle; esta app no accede a la base.';
  const state = document.createElement('p');
  state.textContent = 'Preparando consultas...';
  state.setAttribute('role', 'status');
  container.append(heading, notice, state);

  try {
    const response = await authFetch(`/api/sql-assistant/data-queries/${module}/${draftId}`, { method: 'POST' });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || 'No se pudieron preparar las consultas.');
    if (container.dataset.draftId !== draftId) return;
    const queries = payload.queries || [];
    const ready = queries.filter(item => item.status === 'ready').length;
    state.textContent = `Consultas listas para revisar: ${ready}/${queries.length}`;
    for (const [index, item] of queries.entries()) {
      const detail = document.createElement('details');
      detail.className = 'qa-data-query';
      detail.open = index === 0;
      const summary = document.createElement('summary');
      summary.textContent = `${item.key} · ${item.status === 'ready' ? 'SELECT disponible' : 'Necesita revisión'}`;
      detail.append(summary);
      if (item.sql) {
        const pre = document.createElement('pre');
        pre.textContent = item.sql;
        const copy = document.createElement('button');
        copy.type = 'button';
        copy.className = 'qa-query-copy';
        copy.textContent = 'Copiar SQL';
        copy.title = `Copiar consulta de ${item.key}`;
        copy.addEventListener('click', async () => {
          try {
            await navigator.clipboard.writeText(item.sql);
            copy.textContent = 'Copiado';
            setTimeout(() => { copy.textContent = 'Copiar SQL'; }, 2000);
          } catch {
            copy.textContent = 'Seleccioná el SQL para copiar';
          }
        });
        detail.append(pre, copy);
      }
      if (item.bindings?.length) {
        const binds = document.createElement('p');
        binds.textContent = `Completá estos parámetros antes de ejecutar: ${item.bindings.map(key => `:${key}`).join(', ')}`;
        detail.append(binds);
      }
      if (item.note) {
        const note = document.createElement('p');
        note.textContent = item.note;
        detail.append(note);
      }
      container.append(detail);
    }
  } catch (error) {
    if (container.dataset.draftId === draftId) {
      state.textContent = error.message || 'No se pudieron preparar las consultas.';
    }
  }
}
