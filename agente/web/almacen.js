// Pantalla de almacén: existencias, faltantes, sugerencias y recepciones por relacionar.

let existencias = [];
let recepciones = [];
let recepcionElegida = null;
const piezaARelacionar = campoEscaneo("relacionar-pieza", "pieza");

async function cargarEstado() {
  const e = await api("GET", "/estado");
  const errores = e.errores ? `<span class="error">${e.errores}</span>` : "0";
  document.getElementById("estado").innerHTML = `
    <div><span class="suave">Eventos procesados</span><b>${e.eventos_procesados}</b></div>
    <div><span class="suave">Por leer (lag)</span><b>${e.lag_total}</b></div>
    <div><span class="suave">Esperando datos</span><b>${e.eventos_esperando}</b></div>
    <div><span class="suave">Enviados a la DLQ</span><b>${errores}</b></div>`;
}

function dibujarExistencias() {
  const filtro = document.getElementById("filtro").value.trim().toLowerCase();
  const visibles = existencias.filter(p => {
    const texto = [p.name, p.sku, ...(p.locations || []).map(l => l.code)].join(" ").toLowerCase();
    return texto.includes(filtro);
  });
  document.getElementById("existencias").innerHTML = tabla(
    ["<th>Pieza</th>", "<th>Ubicaciones</th>", '<th class="num">Existencia</th>', '<th class="num">Reservado</th>', '<th class="num">Disponible</th>'],
    visibles.map(p => `<tr>
      <td><a href="kardex.html?pieza=${p.part_id}">${nombrePieza(p)}</a></td>
      <td>${(p.locations || []).map(l => `${t(l.code)}: ${l.on_hand}`).join(", ")}</td>
      <td class="num">${p.on_hand}</td>
      <td class="num">${p.reserved}</td>
      <td class="num ${p.available < 0 ? "error" : ""}">${p.available}</td>
    </tr>`));
}

async function cargarListas() {
  const [stock, faltantes, sugerencias, sinRelacionar] = await Promise.all([
    api("GET", "/stock"), api("GET", "/shortages"), api("GET", "/reorder-suggestions"), api("GET", "/unmatched-receipts"),
  ]);
  existencias = stock.items;
  recepciones = sinRelacionar.items;
  dibujarExistencias();

  document.getElementById("faltantes").innerHTML = tabla(
    ["<th>Orden</th>", "<th>Pieza</th>", '<th class="num">Falta</th>', "<th>Desde</th>"],
    faltantes.items.map(f => `<tr>
      <td><a href="orden.html?codigo=${encodeURIComponent(f.work_order_code)}">${t(f.work_order_code)}</a></td>
      <td>${f.part_id ? nombrePieza(f) : `${t(f.name)} <span class="aviso">(sin identificar)</span>`}</td>
      <td class="num">${f.missing_quantity ?? "¿?"}</td>
      <td>${fecha(f.opened_at)}</td>
    </tr>`));

  document.getElementById("sugerencias").innerHTML = tabla(
    ["<th>Pieza</th>", '<th class="num">Comprar</th>', "<th>Para las órdenes</th>"],
    sugerencias.items.map(s => `<tr>
      <td>${nombrePieza(s)}</td>
      <td class="num">${s.suggested_quantity}</td>
      <td class="suave">${s.work_order_ids.length ? s.work_order_ids.join(", ") : "reabasto por mínimo"}</td>
    </tr>`));

  document.getElementById("sin-relacionar").innerHTML = tabla(
    ["<th>Número de parte</th>", "<th>Descripción</th>", '<th class="num">Cantidad</th>', "<th>Orden</th>", "<th></th>"],
    recepciones.map(r => `<tr>
      <td>${t(r.part_number)}</td>
      <td>${t(r.description)}</td>
      <td class="num">${r.quantity}</td>
      <td>${t(r.work_order_code)}</td>
      <td><button class="secundario" onclick="abrirRelacionar(${r.id})">Relacionar</button></td>
    </tr>`));
}

async function abrirRelacionar(id) {
  recepcionElegida = recepciones.find(r => r.id === id);
  piezaARelacionar.limpiar();
  mensaje("relacionar-mensaje", "");
  document.getElementById("relacionar-detalle").innerHTML =
    `«${t(recepcionElegida.part_number)}» · ${t(recepcionElegida.description)} · ${recepcionElegida.quantity} piezas`;
  document.getElementById("relacionar").hidden = false;
  window.scrollTo({ top: 0, behavior: "smooth" });

  // Si el sku coincidió con varias piezas, se ofrecen como opciones.
  const candidatas = recepcionElegida.candidate_part_ids || [];
  if (candidatas.length) {
    const opciones = await Promise.all(candidatas.map(id => api("GET", `/parts/${id}/availability`)));
    const caja = document.querySelector("#relacionar-pieza .resultado");
    caja.innerHTML = `<span class="aviso">El sku coincide con varias piezas:</span><div class="opciones"></div>`;
    for (const p of opciones) {
      const boton = document.createElement("button");
      boton.className = "secundario";
      boton.innerHTML = nombrePieza(p);
      boton.onclick = () => piezaARelacionar.elegir(p);
      caja.querySelector(".opciones").appendChild(boton);
    }
  }
}

document.getElementById("relacionar-boton").onclick = async () => {
  if (!piezaARelacionar.valor) return mensaje("relacionar-mensaje", "Primero elige la pieza", "aviso");
  try {
    await api("POST", `/unmatched-receipts/${recepcionElegida.id}/resolve`, { part_id: piezaARelacionar.valor.part_id });
    document.getElementById("relacionar").hidden = true;
    actualizar();
  } catch (e) {
    mensaje("relacionar-mensaje", e.message, "error");
  }
};

document.getElementById("relacionar-cancelar").onclick = () => {
  document.getElementById("relacionar").hidden = true;
  detenerEscaner();
};

document.getElementById("filtro").addEventListener("input", dibujarExistencias);

async function actualizar() {
  try {
    await Promise.all([cargarEstado(), cargarListas()]);
  } catch (e) {
    document.getElementById("estado").innerHTML = `<span class="error">No hay conexión con el servicio: ${t(e.message)}</span>`;
  }
}

actualizar();
setInterval(actualizar, 10000);
