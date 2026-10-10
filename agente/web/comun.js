// Funciones que usan todas las pantallas.

// Llama a la API. Si responde con error, lanza una excepción con el mensaje del servidor.
async function api(metodo, ruta, cuerpo) {
  const opciones = { method: metodo, headers: { "Content-Type": "application/json" } };
  if (cuerpo !== undefined) opciones.body = JSON.stringify(cuerpo);
  const respuesta = await fetch(ruta, opciones);
  const datos = await respuesta.json().catch(() => null);
  if (!respuesta.ok) {
    const detalle = datos && datos.detail;
    throw new Error(typeof detalle === "string" ? detalle : "La petición falló (" + respuesta.status + ")");
  }
  return datos;
}

// Escapa texto para ponerlo dentro de HTML sin riesgo.
function t(valor) {
  if (valor === null || valor === undefined) return "";
  return String(valor).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Nombre de una pieza con su código (sku, o P-<id> si no tiene).
function nombrePieza(p) {
  return `${t(p.name)} <span class="suave">${t(p.sku || "P-" + p.part_id)}</span>`;
}

function mensaje(id, texto, tipo) {
  document.getElementById(id).innerHTML = texto ? `<div class="mensaje ${tipo}">${t(texto)}</div>` : "";
}

function fecha(iso) {
  return iso ? new Date(iso).toLocaleString("es-MX", { dateStyle: "short", timeStyle: "short" }) : "";
}

function tabla(encabezados, renglones) {
  if (!renglones.length) return `<p class="suave">Nada por ahora.</p>`;
  return `<div class="tabla"><table><tr>${encabezados.join("")}</tr>${renglones.join("")}</table></div>`;
}
