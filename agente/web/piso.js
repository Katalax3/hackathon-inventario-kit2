// Pantalla de piso: retirar, transferir y contar escaneando etiquetas.

const valor = id => document.getElementById(id).value;

// --- Pestañas -------------------------------------------------------------------------------

document.querySelectorAll("[data-pestana]").forEach(boton => {
  boton.onclick = () => {
    detenerEscaner();
    document.querySelectorAll("[data-pestana]").forEach(b => b.classList.toggle("activo", b === boton));
    for (const id of ["retirar", "transferir", "contar"]) {
      document.getElementById(id).hidden = id !== boton.dataset.pestana;
    }
  };
});

// --- Retirar contra una orden -----------------------------------------------------------------

const retiro = {
  ubicacion: campoEscaneo("r-ubicacion", "ubicacion", () => infoRetiro()),
  pieza: campoEscaneo("r-pieza", "pieza", () => infoRetiro()),
};

async function cargarOrdenes() {
  const { items } = await api("GET", "/work-orders");
  const select = document.getElementById("r-orden");
  select.innerHTML = items.map(o => {
    const reservas = o.reservas ? ` · ${o.reservas} con reserva` : "";
    return `<option value="${t(o.code)}">${t(o.code)} · ${t(o.model_name || "")}${reservas}</option>`;
  }).join("");
  const deLaUrl = new URLSearchParams(location.search).get("orden");
  if (deLaUrl) select.value = deLaUrl;
}

// Muestra cuánto hay en la ubicación y cuánto está reservado para la orden, y propone la cantidad.
async function infoRetiro() {
  const ubicacion = retiro.ubicacion.valor;
  const pieza = retiro.pieza.valor;
  const info = document.getElementById("r-info");
  if (!ubicacion || !pieza) return;
  try {
    const codigo = valor("r-orden");
    const [saldo, orden] = await Promise.all([
      api("GET", `/parts/${pieza.part_id}/availability`),
      api("GET", `/work-orders/${encodeURIComponent(codigo)}/materials`),
    ]);
    const aqui = (saldo.locations.find(l => l.location_id === ubicacion.location_id) || { on_hand: 0 }).on_hand;
    const paraLaOrden = orden.lines.filter(l => l.part_id === pieza.part_id).reduce((s, l) => s + l.reserved, 0);
    info.innerHTML = `En ${t(ubicacion.code)} hay <b>${aqui}</b> · reservadas para ${t(codigo)}: <b>${paraLaOrden}</b>`;
    document.getElementById("r-cantidad").value = Math.max(1, Math.min(paraLaOrden || 1, aqui || 1));
  } catch (e) {
    info.innerHTML = `<span class="error">${t(e.message)}</span>`;
  }
}

document.getElementById("r-orden").onchange = infoRetiro;

document.getElementById("r-boton").onclick = async () => {
  const ubicacion = retiro.ubicacion.valor;
  const pieza = retiro.pieza.valor;
  if (!ubicacion || !pieza) return mensaje("r-mensaje", "Escanea la ubicación y la pieza", "aviso");
  const tecnico = valor("r-tecnico").trim() || "piso";
  try { localStorage.setItem("tecnico", tecnico); } catch (e) { /* sin almacenamiento, no pasa nada */ }
  const cantidad = Number(valor("r-cantidad"));
  try {
    await api("POST", "/issues", {
      work_order_code: valor("r-orden"), part_id: pieza.part_id, location_id: ubicacion.location_id,
      quantity: cantidad, issued_by: tecnico,
    });
    mensaje("r-mensaje", `Listo: salieron ${cantidad} × ${pieza.name} para ${valor("r-orden")}`, "ok");
    retiro.pieza.limpiar();
    document.getElementById("r-info").textContent = "";
  } catch (e) {
    mensaje("r-mensaje", e.message, "error");
  }
};

// --- Transferir entre ubicaciones -----------------------------------------------------------

const transferencia = {
  origen: campoEscaneo("t-origen", "ubicacion", () => infoTransferencia()),
  pieza: campoEscaneo("t-pieza", "pieza", () => infoTransferencia()),
  destino: campoEscaneo("t-destino", "ubicacion"),
};

async function infoTransferencia() {
  const origen = transferencia.origen.valor;
  const pieza = transferencia.pieza.valor;
  if (!origen || !pieza) return;
  const saldo = await api("GET", `/parts/${pieza.part_id}/availability`);
  const aqui = (saldo.locations.find(l => l.location_id === origen.location_id) || { on_hand: 0 }).on_hand;
  document.getElementById("t-info").innerHTML = `En ${t(origen.code)} hay <b>${aqui}</b>`;
  document.getElementById("t-cantidad").value = Math.max(1, aqui);
}

document.getElementById("t-boton").onclick = async () => {
  const { origen, pieza, destino } = transferencia;
  if (!origen.valor || !pieza.valor || !destino.valor) {
    return mensaje("t-mensaje", "Escanea origen, pieza y destino", "aviso");
  }
  const cantidad = Number(valor("t-cantidad"));
  try {
    await api("POST", "/transfers", {
      part_id: pieza.valor.part_id, from_location_id: origen.valor.location_id,
      to_location_id: destino.valor.location_id, quantity: cantidad,
    });
    mensaje("t-mensaje", `Listo: ${cantidad} × ${pieza.valor.name} de ${origen.valor.code} a ${destino.valor.code}`, "ok");
    pieza.limpiar();
    document.getElementById("t-info").textContent = "";
  } catch (e) {
    mensaje("t-mensaje", e.message, "error");
  }
};

// --- Conteo cíclico ---------------------------------------------------------------------------

let renglones = []; // piezas de la ubicación: { part_id, sku, name, on_hand, contado }

const conteo = {
  ubicacion: campoEscaneo("c-ubicacion", "ubicacion", cargarConteo),
  pieza: campoEscaneo("c-pieza", "pieza", agregarPieza),
};

async function cargarConteo(ubicacion) {
  const { items } = await api("GET", `/locations/${ubicacion.location_id}/stock`);
  renglones = items;
  dibujarConteo();
}

function agregarPieza(pieza) {
  if (!renglones.some(r => r.part_id === pieza.part_id)) {
    renglones.push({ ...pieza, on_hand: 0 });
    dibujarConteo();
  }
  conteo.pieza.limpiar();
}

function dibujarConteo() {
  if (!conteo.ubicacion.valor) return;
  document.getElementById("c-lista").innerHTML = tabla(
    ["<th>Pieza</th>", '<th class="num">Sistema</th>', '<th class="num">Contado</th>'],
    renglones.map((r, i) => `<tr>
      <td>${nombrePieza(r)}</td>
      <td class="num">${r.on_hand}</td>
      <td class="num"><input type="number" min="0" inputmode="numeric" style="width:90px"
          value="${r.contado ?? ""}" oninput="renglones[${i}].contado = this.value"></td>
    </tr>`));
}

document.getElementById("c-boton").onclick = async () => {
  const ubicacion = conteo.ubicacion.valor;
  if (!ubicacion) return mensaje("c-mensaje", "Escanea la ubicación", "aviso");
  // Solo se mandan las piezas que sí se contaron (con un número escrito).
  const lineas = renglones
    .filter(r => r.contado !== undefined && r.contado !== "")
    .map(r => ({ part_id: r.part_id, counted_quantity: Number(r.contado) }));
  if (!lineas.length) return mensaje("c-mensaje", "Escribe cuántas contaste de al menos una pieza", "aviso");
  try {
    const { adjustments } = await api("POST", "/counts", { location_id: ubicacion.location_id, reason: valor("c-motivo"), lines: lineas });
    const texto = adjustments.length
      ? "Ajustes registrados: " + adjustments.map(a => `${renglones.find(r => r.part_id === a.part_id).name} ${a.delta > 0 ? "+" : ""}${a.delta}`).join(", ")
      : "Todo cuadra: no hubo ajustes";
    mensaje("c-mensaje", texto, "ok");
    cargarConteo(ubicacion);
  } catch (e) {
    mensaje("c-mensaje", e.message, "error");
  }
};

// --- Al abrir la página ----------------------------------------------------------------------

try { document.getElementById("r-tecnico").value = localStorage.getItem("tecnico") || ""; } catch (e) { /* sin almacenamiento */ }
cargarOrdenes().catch(e => mensaje("r-mensaje", e.message, "error"));
