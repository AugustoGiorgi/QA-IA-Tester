const number = value => Number(value) || 0;
const count = value => new Intl.NumberFormat('es-AR').format(value);
const percent = (value, total) => total > 0 ? `${(value / total * 100).toFixed(1)}%` : '--';

export function summarize(records) {
  return records.reduce((total, record) => {
    total.records += 1;
    for (const key of ['generated_cases', 'ok_cases', 'additional_qa_cases', 'additional_functional_cases']) total[key] += number(record[key]);
    total.seconds += number(record.design_time_seconds ?? number(record.design_time) * 60);
    return total;
  }, { records: 0, generated_cases: 0, ok_cases: 0, additional_qa_cases: 0, additional_functional_cases: 0, seconds: 0 });
}

export async function renderOverview(host) {
  host.innerHTML = '<p role="status">Cargando Registro IA...</p>';
  try {
    const response = await fetch('/api/quality-records');
    if (!response.ok) throw new Error('No se pudo cargar el Registro IA.');
    const { records = [] } = await response.json();
    if (!host.isConnected || document.getElementById('viewTitle')?.textContent !== 'Inicio') return;
    const t = summarize(records);
    const reviewed = t.ok_cases + t.additional_qa_cases;
    const final = reviewed + t.additional_functional_cases;
    const values = [['Generados por IA', t.generated_cases, 'blue'], ['Casos OK', t.ok_cases, 'green'], ['Adicionales QA', t.additional_qa_cases, 'amber'], ['Adicionales funcional', t.additional_functional_cases, 'pink']];
    const max = Math.max(1, ...values.map(([, value]) => value));
    host.innerHTML = `
      <div class="overview">
        <div class="overview-metrics">
          <div><span>Registros</span><strong>${count(t.records)}</strong></div>
          <div><span>Casos generados</span><strong>${count(t.generated_cases)}</strong></div>
          <div><span>Casos finales</span><strong>${count(final)}</strong></div>
          <div><span>Tiempo de dise&ntilde;o</span><strong>${count(Math.floor(t.seconds / 60))}<small> min ${Math.round(t.seconds % 60)} s</small></strong></div>
        </div>
        <section class="overview-chart" aria-labelledby="chartTitle">
          <h2 id="chartTitle">Casos de prueba del equipo</h2>
          ${t.records ? `<div class="overview-bars" role="img" aria-label="${values.map(([label, value]) => `${label}: ${value}`).join('. ')}">
            ${values.map(([label, value, color]) => `<div class="overview-bar-row"><span>${label}</span><div class="overview-track"><div class="overview-bar ${color}" style="width:${value / max * 100}%"></div></div><strong>${count(value)}</strong></div>`).join('')}
          </div>` : '<p class="muted">Todavia no hay registros. Carga el primero en Registro IA.</p>'}
        </section>
        <section class="overview-quality" aria-labelledby="qualityTitle">
          <h2 id="qualityTitle">Calidad acumulada</h2>
          <div class="overview-quality-grid">
            <div><span>Calidad IA</span><strong>${percent(t.ok_cases, t.generated_cases)}</strong><small>Casos OK / generados</small></div>
            <div><span>Post revision QA</span><strong>${percent(t.ok_cases, reviewed)}</strong><small>Casos OK / (OK + adicionales QA)</small></div>
            <div><span>Post revision funcional</span><strong>${percent(t.ok_cases, final)}</strong><small>Casos OK / casos finales</small></div>
          </div>
        </section>
      </div>`;
  } catch (error) {
    if (document.getElementById('viewTitle')?.textContent !== 'Inicio') return;
    host.innerHTML = '<p class="field-error" role="alert">No se pudo cargar el Registro IA.</p><button type="button" id="retryOverview">Reintentar</button>';
    host.querySelector('#retryOverview').addEventListener('click', () => renderOverview(host));
  }
}
