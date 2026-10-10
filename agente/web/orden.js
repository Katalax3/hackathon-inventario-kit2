// Pantalla de orden: qué piezas tiene reservadas, entregadas y pendientes.

let codigoActual = null;

function estadoLinea(l) {
  if (l.required !== null && l.issued >= l.required) return `<span class="etiqueta ok">Entregado</span>`;
  if (l.missing === null || l.missing > 0) return `<span class="etiqueta error">Falta</span>`;
  if (l.reserved > 0) return `<span class="etiqueta aviso">Listo para retirar</span>`;
  return "";
}

// Dónde está físicamente cada pieza, para que el técnico sepa a qué ubicación ir.
async function ubicacionesDe(lineas) {
  const ids = [...new Set(lineas.filter(l => l.part_id && l.reserved > 0).map(l => l.part_id))];
  const saldos = await Promise.all(ids.map(id => api("GET", `/parts/${id}/availability`)));
  const donde = {};
  saldos.forEach(s => { donde[s.part_id] = s.locations.map(l => `${l.code} (${l.on_hand})`).join(", "); });
  return donde;
}

async function cargarOrden(codigo) {
  try {
    const orden = await api("GET", `/work-orders/${encodeURIComponent(codigo)}/materials`);
    const donde = await ubicacionesDe(orden.lines);
    codigoActual = orden.code;
    mensaje("mensaje", "");
    document.getElementById("detalle").hidden = false;
    document.getElementById("titulo").textContent = orden.code + (orden.deleted ? " (dada de baja)" : "");
    document.getElementById("subtitulo").textContent = [orden.model_name, orden.stage].filter(Boolean).join(" · ");
    document.getElementById("ir-a-piso").href = "piso.html?orden=" + encodeURIComponent(orden.code);

    const suma = campo => orden.lines.reduce((s, l) => s + (l[campo] || 0), 0);
    document.getElementById("resumen").innerHTML = `
      <div><span class="suave">Reservado</span><b>${suma("reserved")}</b></div>
      <div><span class="suave">Entregado</span><b>${suma("issued")}</b></div>
      <div><span class="suave">Faltante</span><b class="${suma("missing") ? "error" : ""}">${suma("missing")}</b></div>`;

    document.getElementById("lineas").innerHTML = tabla(
      ["<th>Pieza</th>", '<th class="num">Necesita</th>', '<th class="num">Reservado</th>', '<th class="num">Entregado</th>',
       '<th class="num">Falta</th>', "<th>Dónde</th>", "<th></th>"],
      orden.lines.map(l => `<tr>
        <td>${l.part_id ? nombrePieza(l) : `${t(l.name)} <span class="aviso">(sin identificar)</span>`}</td>
        <td class="num">${l.required ?? "¿?"}</td>
        <td class="num">${l.reserved}</td>
        <td class="num">${l.issued}</td>
        <td class="num">${l.missing ?? "¿?"}</td>
        <td class="suave">${t(donde[l.part_id] || "")}</td>
        <td>${estadoLinea(l)}</td>
      </tr>`));
  } catch (e) {
    document.getElementById("detalle").hidden = true;
    mensaje("mensaje", e.message, "error");
  }
}

async function cargarListaDeOrdenes() {
  const { items } = await api("GET", "/work-orders");
  document.getElementById("ordenes").innerHTML =
    items.map(o => `<option value="${t(o.code)}">${t(o.model_name || "")}</option>`).join("");
}

document.getElementById("buscar").onsubmit = e => {
  e.preventDefault();
  const codigo = document.getElementById("codigo").value.trim();
  if (codigo) cargarOrden(codigo);
};

const deLaUrl = new URLSearchParams(location.search).get("codigo");
if (deLaUrl) {
  document.getElementById("codigo").value = deLaUrl;
  cargarOrden(deLaUrl);
}
cargarListaDeOrdenes();

// Se actualiza sola para ver llegar reservas y compras durante la demo.
setInterval(() => { if (codigoActual) cargarOrden(codigoActual); }, 4000);
