// Pantalla de orden: qué piezas tiene reservadas, entregadas y pendientes.

let codigoActual = null;

// El nombre de una orden es el del equipo que entró al taller: marca y modelo.
function nombreOrden(o) {
  return [o.brand, o.model_name].filter(Boolean).join(" ");
}

function estadoLinea(l) {
  if (l.required !== null && l.issued >= l.required) return `<span class="etiqueta ok">Entregado</span>`;
  if (l.missing === null || l.missing > 0) return `<span class="etiqueta error">Falta</span>`;
  if (l.reserved > 0) return `<span class="etiqueta aviso">Listo para retirar</span>`;
  return "";
}

// Por qué una orden no tiene piezas que mostrar.
function sinPiezas(orden) {
  if (!orden.approved_inspections) return "Todavía no tiene una inspección aprobada: aún no pide piezas.";
  return "Sus inspecciones aprobadas no pidieron piezas para comprar (todo salió bien o a reparar): no hay nada que retirar.";
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
    document.getElementById("titulo").textContent =
      [orden.code, nombreOrden(orden)].filter(Boolean).join(" · ") + (orden.deleted ? " (dada de baja)" : "");
    document.getElementById("subtitulo").textContent = "Etapa: " + (orden.stage || "sin etapa");
    document.getElementById("ir-a-piso").href = "piso.html?orden=" + encodeURIComponent(orden.code);

    const suma = campo => orden.lines.reduce((s, l) => s + (l[campo] || 0), 0);
    document.getElementById("resumen").innerHTML = `
      <div><span class="suave">Reservado</span><b>${suma("reserved")}</b></div>
      <div><span class="suave">Entregado</span><b>${suma("issued")}</b></div>
      <div><span class="suave">Faltante</span><b class="${suma("missing") ? "error" : ""}">${suma("missing")}</b></div>`;

    if (!orden.lines.length) {
      document.getElementById("lineas").innerHTML = `<p class="suave">${sinPiezas(orden)}</p>`;
      return;
    }
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

// Cada sugerencia es "número · nombre", así se encuentra escribiendo cualquiera de los dos.
// El Map guarda a qué número de orden corresponde cada texto.
let sugerencias = new Map();

async function cargarListaDeOrdenes() {
  // Se sugieren todas las órdenes; primero las de la demo (abiertas en el panel, aunque estén dadas de baja).
  const [demo, todas] = await Promise.all([api("GET", "/work-orders?demo=true"), api("GET", "/work-orders")]);
  const nuevas = new Map();
  for (const o of [...demo.items, ...todas.items]) {
    const texto = [o.code, nombreOrden(o)].filter(Boolean).join(" · ");
    if (!nuevas.has(texto)) nuevas.set(texto, o.code);
  }
  // Solo se redibuja si cambió, para no cerrar la lista mientras alguien la está viendo.
  if ([...nuevas.keys()].join() !== [...sugerencias.keys()].join()) {
    sugerencias = nuevas;
    llenarDatalist("ordenes", [...nuevas.keys()]);
  }
}

const campoCodigo = document.getElementById("codigo");

document.getElementById("buscar").onsubmit = e => {
  e.preventDefault();
  const texto = campoCodigo.value.trim();
  if (texto) cargarOrden(sugerencias.get(texto) || texto);
};

// Al elegir una sugerencia, la orden se abre sin tener que presionar Buscar.
campoCodigo.addEventListener("input", () => {
  if (sugerencias.has(campoCodigo.value)) cargarOrden(sugerencias.get(campoCodigo.value));
});

const deLaUrl = new URLSearchParams(location.search).get("codigo");
if (deLaUrl) {
  campoCodigo.value = deLaUrl;
  cargarOrden(deLaUrl);
}
cargarListaDeOrdenes();

// Todo se actualiza solo: las órdenes nuevas del panel aparecen en las sugerencias, y en la
// orden abierta se ven llegar reservas, compras y salidas.
setInterval(() => cargarListaDeOrdenes().catch(() => {}), 3000);
setInterval(() => { if (codigoActual) cargarOrden(codigoActual); }, 3000);
