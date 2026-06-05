import { API_BASE, postFile } from "./api.js";

const form = document.getElementById("form");
const list = document.getElementById("list");

async function loadList() {
  try {
    const res = await fetch(`${API_BASE}/api/feedback`);
    if (!res.ok) {
      list.innerHTML = "<p>Error al cargar histórico.</p>";
      return;
    }

    const data = await res.json();

    if (!Array.isArray(data) || data.length === 0) {
      list.innerHTML = `<p class="small">No hay feedback cargado todavía.</p>`;
      return;
    }

    list.innerHTML = data
      .map((f) => {
        const when = f.created_at
          ? new Date(f.created_at).toLocaleString(undefined, {
              year: "numeric",
              month: "2-digit",
              day: "2-digit",
              hour: "2-digit",
              minute: "2-digit",
            })
          : "—";

        return `
          <div class="output" style="margin:10px 0;">
            <div><b>${f.filename}</b></div>
            <div class="small">Doc origen: ${f.source_doc_name ?? "—"} | ${when}</div>
            <div class="small">Notas: ${f.notes ?? "—"}</div>
          </div>
        `;
      })
      .join("");
  } catch {
    list.innerHTML = "<p>Error al cargar histórico.</p>";
  }
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();

  const file = document.getElementById("file").files[0];
  const source = document.getElementById("source").value;
  const notes = document.getElementById("notes").value;

  if (!file) {
    alert("Seleccioná un .xlsx");
    return;
  }

  try {
    const res = await postFile("/api/feedback", file, {
      source_doc_name: source,
      notes,
    });

    // Consumimos el body para evitar leaks y refrescamos la lista
    await res.json();
    await loadList();

    alert("Feedback subido. ¡Gracias!");
    form.reset();
  } catch (err) {
    alert("Error: " + err.message);
  }
});

// Initial load
loadList();
