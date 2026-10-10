"""API HTTP y pantallas. Rutas y campos según contract/openapi.yaml."""

import asyncio
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import bitacora
import consultas
import consumidor
import inventario
from base_datos import conexion, transaccion
from inventario import Conflicto, Invalido, NoExiste

app = FastAPI(title="Inventario del taller")


@app.middleware("http")
async def sin_cache(request, call_next):
    """El navegador siempre pregunta si hay una versión nueva de las pantallas y los datos;
    si no cambió, el servidor responde 304 y no se vuelve a descargar."""
    respuesta = await call_next(request)
    respuesta.headers["Cache-Control"] = "no-cache"
    return respuesta


@app.middleware("http")
async def esperar_reconstruccion(request, call_next):
    """Mientras se repite la bitácora nadie debe ver la base a medias. Las consultas de datos
    esperan a que termine (hasta 9 s, menos que la espera de las pruebas) y las escrituras se
    rechazan, porque cambiarían el orden. Las pantallas y /estado responden siempre."""
    ruta = request.url.path
    if consumidor.estado["reconstruyendo"] and ruta != "/estado" and ruta != "/" and "." not in ruta:
        if request.method == "GET":
            for _ in range(90):
                await asyncio.sleep(0.1)
                if not consumidor.estado["reconstruyendo"]:
                    return await call_next(request)
        return JSONResponse({"detail": "se está reconstruyendo la base; intenta en unos segundos"}, status_code=503)
    return await call_next(request)


@app.exception_handler(NoExiste)
def no_existe(request, error):
    return JSONResponse({"detail": str(error)}, status_code=404)


@app.exception_handler(Conflicto)
def conflicto(request, error):
    return JSONResponse({"detail": str(error)}, status_code=409)


@app.exception_handler(Invalido)
def invalido(request, error):
    return JSONResponse({"detail": str(error)}, status_code=422)


# --- Cuerpos de las peticiones -----------------------------------------------------------

class Salida(BaseModel):
    work_order_code: str
    part_id: int
    location_id: int
    quantity: int = Field(ge=1)
    issued_by: int | str


class Transferencia(BaseModel):
    part_id: int
    from_location_id: int
    to_location_id: int
    quantity: int = Field(ge=1)


class LineaConteo(BaseModel):
    part_id: int
    counted_quantity: int = Field(ge=0)


class Conteo(BaseModel):
    location_id: int
    reason: str
    lines: list[LineaConteo]


class Relacion(BaseModel):
    part_id: int
    location_id: int | None = None


class Politica(BaseModel):
    min_qty: int = Field(0, ge=0)
    max_qty: int | None = Field(None, ge=0)
    reorder_qty: int | None = Field(None, ge=1)
    lead_time_days: int | None = Field(None, ge=0)


# --- Rutas del contrato --------------------------------------------------------------------

@app.get("/parts/{part_id}/availability")
def disponibilidad(part_id: int):
    with conexion() as cur:
        return consultas.saldo(cur, part_id)


@app.get("/parts/{part_id}/ledger")
def kardex(part_id: int):
    with conexion() as cur:
        return consultas.kardex(cur, part_id)


@app.get("/work-orders/{code}/materials")
def materiales(code: str):
    with conexion() as cur:
        return consultas.materiales(cur, code)


@app.get("/shortages")
def faltantes(work_order_code: str | None = None, part_id: int | None = None):
    with conexion() as cur:
        return consultas.faltantes(cur, work_order_code, part_id)


@app.get("/reorder-suggestions")
def sugerencias():
    with conexion() as cur:
        return consultas.sugerencias(cur)


@app.get("/unmatched-receipts")
def sin_relacionar():
    with conexion() as cur:
        return consultas.sin_relacionar(cur)


@app.post("/unmatched-receipts/{recepcion_id}/resolve")
def relacionar(recepcion_id: int, datos: Relacion):
    with transaccion() as cur:
        movimiento = bitacora.ejecutar(cur, "relacion", {"id": recepcion_id, **datos.model_dump()})
        return inventario.movimiento_json(movimiento)


@app.post("/issues", status_code=201)
def salida(datos: Salida):
    with transaccion() as cur:
        movimiento = bitacora.ejecutar(cur, "salida", datos.model_dump())
        return inventario.movimiento_json(movimiento)


@app.post("/transfers", status_code=201)
def transferencia(datos: Transferencia):
    with transaccion() as cur:
        movimiento = bitacora.ejecutar(cur, "transferencia", datos.model_dump())
        return inventario.movimiento_json(movimiento)


@app.post("/counts", status_code=201)
def conteo(datos: Conteo):
    with transaccion() as cur:
        ajustes = bitacora.ejecutar(cur, "conteo", datos.model_dump())
        return {"adjustments": ajustes}


# --- Rutas extra para las pantallas ----------------------------------------------------------

@app.get("/stock")
def existencias():
    with conexion() as cur:
        return consultas.existencias(cur)


@app.get("/locations/{location_id}/stock")
def stock_en_ubicacion(location_id: int):
    with conexion() as cur:
        return consultas.stock_en_ubicacion(cur, location_id)


@app.get("/work-orders")
def ordenes(demo: bool = False):
    with conexion() as cur:
        return consultas.ordenes(cur, demo)


@app.get("/buscar")
def buscar(q: str):
    with conexion() as cur:
        return consultas.buscar(cur, q)


@app.get("/catalogo")
def catalogo():
    with conexion() as cur:
        return consultas.catalogo(cur)


@app.put("/parts/{part_id}/policy")
def politica(part_id: int, datos: Politica):
    with transaccion() as cur:
        bitacora.ejecutar(cur, "politica", {"part_id": part_id, "politica": datos.model_dump()})
        return {"ok": True}


@app.get("/estado")
def estado():
    with conexion() as cur:
        return consultas.resumen(cur, consumidor.estado)


# Las pantallas (carpeta web/). Va al final para no tapar las rutas de arriba.
app.mount("/", StaticFiles(directory=Path(__file__).parent / "web", html=True), name="web")
