// Lectura de etiquetas Code 128 con la cámara, usando la librería html5-qrcode.
// El navegador del celular solo presta la cámara en HTTPS (o en localhost).

let lectorActivo = null;

// Abre la cámara dentro del elemento `idCaja` y devuelve el texto del primer código leído.
async function escanear(idCaja) {
  await detenerEscaner();
  const lector = new Html5Qrcode(idCaja, {
    formatsToSupport: [Html5QrcodeSupportedFormats.CODE_128],
    experimentalFeatures: { useBarCodeDetectorIfSupported: true },
    verbose: false,
  });
  lectorActivo = lector;
  return new Promise((resolver, rechazar) => {
    let leido = false;
    lector.start(
      { facingMode: "environment" },
      { fps: 10, qrbox: (ancho, alto) => ({ width: Math.floor(ancho * 0.9), height: Math.floor(alto * 0.45) }) },
      async texto => {
        if (leido) return;
        leido = true;
        await detenerEscaner();
        resolver(texto);
      },
      () => {} // cuadro sin código: seguir buscando
    ).catch(error => {
      lectorActivo = null;
      rechazar(error);
    });
  });
}

async function detenerEscaner() {
  if (!lectorActivo) return;
  const lector = lectorActivo;
  lectorActivo = null;
  try { await lector.stop(); } catch (e) { /* ya estaba detenido */ }
  lector.clear();
}

// Campo donde se escanea (o escribe) una ubicación o una pieza.
// tipo: "ubicacion" o "pieza". alElegir(objeto) se llama cuando queda identificado.
// Devuelve un objeto con .valor (lo elegido) y .limpiar().
function campoEscaneo(idContenedor, tipo, alElegir) {
  const caja = document.getElementById(idContenedor);
  const ayuda = tipo === "ubicacion" ? "Escanea o escribe la ubicación (ej. U-101)" : "Escanea o escribe la pieza (sku o nombre)";
  caja.innerHTML = `
    <div class="fila">
      <input placeholder="${ayuda}" autocomplete="off">
      <button type="button" class="secundario" title="Usar la cámara">📷</button>
    </div>
    <div class="camara" id="${idContenedor}-camara"></div>
    <div class="resultado"></div>`;
  const entrada = caja.querySelector("input");
  const resultado = caja.querySelector(".resultado");
  const campo = { valor: null };
  let ultimoTexto = null;

  function elegir(objeto) {
    campo.valor = objeto;
    const texto = tipo === "ubicacion" ? `${t(objeto.code)} · ${t(objeto.name)}` : nombrePieza(objeto);
    resultado.innerHTML = `<span class="ok">✔ ${texto}</span>`;
    if (alElegir) alElegir(objeto);
  }

  async function identificar(texto) {
    if (texto === ultimoTexto) return;
    ultimoTexto = texto;
    campo.valor = null;
    resultado.innerHTML = "";
    if (!texto.trim()) return;
    try {
      const encontrado = await api("GET", "/buscar?q=" + encodeURIComponent(texto));
      const lista = tipo === "ubicacion" ? encontrado.locations : encontrado.parts;
      if (lista.length === 1) return elegir(lista[0]);
      if (!lista.length) {
        resultado.innerHTML = `<span class="error">No se encontró «${t(texto)}»</span>`;
        return;
      }
      // Varias piezas con el mismo sku (o coincidencias por nombre): que la persona elija.
      resultado.innerHTML = `<span class="aviso">¿Cuál es?</span><div class="opciones"></div>`;
      for (const p of lista) {
        const boton = document.createElement("button");
        boton.type = "button";
        boton.className = "secundario";
        boton.innerHTML = nombrePieza(p);
        boton.onclick = () => elegir(p);
        resultado.querySelector(".opciones").appendChild(boton);
      }
    } catch (e) {
      resultado.innerHTML = `<span class="error">${t(e.message)}</span>`;
    }
  }

  entrada.addEventListener("change", () => identificar(entrada.value));
  // Los lectores USB "escriben" el código y mandan Enter.
  entrada.addEventListener("keydown", e => {
    if (e.key === "Enter") { e.preventDefault(); identificar(entrada.value); }
  });
  caja.querySelector("button").onclick = async () => {
    try {
      const texto = await escanear(idContenedor + "-camara");
      entrada.value = texto;
      identificar(texto);
    } catch (e) {
      resultado.innerHTML = `<span class="error">No se pudo abrir la cámara (${t(e.message || e)}). En el celular la página debe abrirse con https.</span>`;
    }
  };

  campo.limpiar = () => {
    ultimoTexto = null;
    entrada.value = "";
    resultado.innerHTML = "";
    campo.valor = null;
  };
  campo.elegir = elegir;
  return campo;
}
