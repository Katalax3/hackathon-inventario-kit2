// Kardex: saldo de una pieza por ubicación y todos sus movimientos con el saldo corrido.

const TIPOS = {
  receipt: "Entrada por compra",
  issue: "Salida a orden",
  transfer_out: "Transferencia (sale)",
  transfer_in: "Transferencia (entra)",
  adjustment: "Ajuste por conteo",
};

let piezaActual = null;
campoEscaneo("pieza", "pieza", p => cargarPieza(p.part_id));

const numero = id => (document.getElementById(id).value === "" ? null : Number(document.getElementById(id).value));

async function cargarPieza(partId) {
  const [saldo, kardex] = await Promise.all([
    api("GET", `/parts/${partId}/availability`),
    api("GET", `/parts/${partId}/ledger`),
  ]);
  piezaActual = partId;
  document.getElementById("detalle").hidden = false;
  document.getElementById("titulo").innerHTML = nombrePieza(saldo);
  document.getElementById("saldo").innerHTML = `
    <div><span class="suave">Existencia</span><b>${saldo.on_hand}</b></div>
    <div><span class="suave">Reservado</span><b>${saldo.reserved}</b></div>
    <div><span class="suave">Disponible</span><b>${saldo.available}</b></div>`;
  document.getElementById("ubicaciones").innerHTML = tabla(
    ["<th>Ubicación</th>", '<th class="num">Existencia</th>'],
    saldo.locations.map(l => `<tr><td>${t(l.code)}</td><td class="num">${l.on_hand}</td></tr>`));

  const politica = saldo.policy || {};
  document.getElementById("min").value = politica.min_qty ?? "";
  document.getElementById("max").value = politica.max_qty ?? "";
  document.getElementById("reorden").value = politica.reorder_qty ?? "";
  document.getElementById("dias").value = politica.lead_time_days ?? "";

  document.getElementById("movimientos").innerHTML = tabla(
    ["<th>Fecha</th>", "<th>Tipo</th>", "<th>Ubicación</th>", "<th>Referencia</th>", '<th class="num">Cantidad</th>', '<th class="num">Saldo</th>'],
    kardex.entries.map(m => `<tr>
      <td>${fecha(m.occurred_at)}</td>
      <td>${TIPOS[m.type] || t(m.type)}</td>
      <td>${t(m.code)}</td>
      <td class="suave">${t(m.reference)}</td>
      <td class="num ${m.quantity < 0 ? "error" : "ok"}">${m.quantity > 0 ? "+" : ""}${m.quantity}</td>
      <td class="num"><b>${m.balance}</b></td>
    </tr>`));
}

document.getElementById("guardar-politica").onclick = async () => {
  try {
    await api("PUT", `/parts/${piezaActual}/policy`, {
      min_qty: numero("min") ?? 0, max_qty: numero("max"), reorder_qty: numero("reorden"), lead_time_days: numero("dias"),
    });
    mensaje("politica-mensaje", "Guardado", "ok");
  } catch (e) {
    mensaje("politica-mensaje", e.message, "error");
  }
};

const deLaUrl = new URLSearchParams(location.search).get("pieza");
if (deLaUrl) cargarPieza(Number(deLaUrl));
