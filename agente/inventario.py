"""Reglas del inventario: saldos, reservas, faltantes, entradas y salidas.

Todas las funciones reciben `cur`, un cursor dentro de una transacción abierta con
base_datos.transaccion(). Si algo falla a medias, no se guarda nada.

Glosario de tablas:
  movements     libro mayor: cada entrada, salida, transferencia o ajuste (nunca se edita)
  needs         lo que una orden necesita por línea de su lista de partes (bom_line_id)
  reservations  piezas apartadas para una orden
  shortages     lo que una orden necesita y no hay
"""

import uuid
from datetime import datetime, timezone

from psycopg.types.json import Jsonb

import config


class Esperar(Exception):
    """El evento menciona algo que todavía no conocemos: se guarda y se reintenta después."""


class NoExiste(Exception):
    """La API responde 404."""


class Conflicto(Exception):
    """La API responde 409: no alcanza el saldo, o ya estaba hecho."""


class Invalido(Exception):
    """La API responde 422."""


def ahora():
    return datetime.now(timezone.utc)


def fecha_texto(fecha):
    return fecha.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalizar(texto):
    """'  tornillo   6x12 ' -> 'TORNILLO 6X12'"""
    return " ".join(texto.split()).upper()


# --- Eventos de salida ------------------------------------------------------------------

def emitir(cur, tipo, llave, datos):
    """Guarda el evento en la tabla outbox y consumidor.publicar_salidas() lo manda a Kafka.
    Como se guarda en la misma transacción que el cambio, nunca se pierde ni se inventa."""
    evento = {"event_id": str(uuid.uuid4()), "event_type": tipo, "event_version": 1,
              "occurred_at": fecha_texto(ahora()), "source": "inventory", "key": llave, "data": datos}
    cur.execute("INSERT INTO outbox (event_id, event_type, event_key, payload) VALUES (%s, %s, %s, %s)",
                (evento["event_id"], tipo, llave, Jsonb(evento)))


# --- Consultas básicas --------------------------------------------------------------------

def pieza(cur, part_id):
    cur.execute("SELECT * FROM parts WHERE part_id = %s", (part_id,))
    return cur.fetchone()


def ubicacion(cur, location_id):
    cur.execute("SELECT * FROM locations WHERE location_id = %s", (location_id,))
    return cur.fetchone()


def orden_por_id(cur, work_order_id):
    cur.execute("SELECT * FROM work_orders WHERE work_order_id = %s", (work_order_id,))
    return cur.fetchone()


def orden_por_codigo(cur, codigo):
    cur.execute("SELECT * FROM work_orders WHERE code = %s", (codigo,))
    return cur.fetchone()


def exigir(fila, mensaje):
    if fila is None:
        raise NoExiste(mensaje)
    return fila


def piezas_con_sku(cur, texto):
    """Piezas cuyo sku coincide ignorando mayúsculas y espacios de más."""
    cur.execute(r"""SELECT part_id, sku, name FROM parts
                    WHERE upper(regexp_replace(btrim(sku), '\s+', ' ', 'g')) = %s ORDER BY part_id""",
                (normalizar(texto),))
    return cur.fetchall()


def existencia(cur, part_id, location_id=None):
    """Lo que hay físicamente: la suma de los movimientos."""
    if location_id is None:
        cur.execute("SELECT COALESCE(SUM(quantity), 0) AS n FROM movements WHERE part_id = %s", (part_id,))
    else:
        cur.execute("SELECT COALESCE(SUM(quantity), 0) AS n FROM movements WHERE part_id = %s AND location_id = %s",
                    (part_id, location_id))
    return cur.fetchone()["n"]


def reservado(cur, part_id):
    cur.execute("""SELECT COALESCE(SUM(quantity - issued_qty), 0) AS n FROM reservations
                   WHERE part_id = %s AND status = 'active'""", (part_id,))
    return cur.fetchone()["n"]


def disponible(cur, part_id):
    return existencia(cur, part_id) - reservado(cur, part_id)


def reservado_en_linea(cur, work_order_id, bom_line_id):
    cur.execute("""SELECT COALESCE(SUM(quantity - issued_qty), 0) AS n FROM reservations
                   WHERE work_order_id = %s AND bom_line_id = %s AND status = 'active'""",
                (work_order_id, bom_line_id))
    return cur.fetchone()["n"]


def entregado_en_linea(cur, work_order_id, bom_line_id):
    cur.execute("SELECT COALESCE(SUM(issued_qty), 0) AS n FROM reservations WHERE work_order_id = %s AND bom_line_id = %s",
                (work_order_id, bom_line_id))
    return cur.fetchone()["n"]


# --- Libro mayor --------------------------------------------------------------------------

def registrar_movimiento(cur, tipo, part_id, location_id, cantidad, fecha=None, **extra):
    """Agrega un renglón al libro mayor. Cantidad positiva entra, negativa sale."""
    datos = {"type": tipo, "part_id": part_id, "location_id": location_id, "quantity": cantidad,
             "occurred_at": fecha or ahora(), **extra}
    columnas = ", ".join(datos)
    valores = ", ".join(["%s"] * len(datos))
    cur.execute(f"INSERT INTO movements ({columnas}) VALUES ({valores}) RETURNING *", list(datos.values()))
    return cur.fetchone()


def movimiento_json(m):
    return {"movement_id": m["movement_id"], "type": m["type"], "part_id": m["part_id"],
            "location_id": m["location_id"], "quantity": m["quantity"],
            "occurred_at": fecha_texto(m["occurred_at"]), "reference": m["reference"]}


# --- Reservas y faltantes -----------------------------------------------------------------

def reservar(cur, codigo, linea, part_id, cantidad):
    """`linea` es un renglón de needs o de shortages (trae work_order_id, bom_line_id e inspection_item_id)."""
    cur.execute("""INSERT INTO reservations (work_order_id, bom_line_id, inspection_item_id, part_id, quantity)
                   VALUES (%s, %s, %s, %s, %s) RETURNING reservation_id""",
                (linea["work_order_id"], linea["bom_line_id"], linea["inspection_item_id"], part_id, cantidad))
    reservation_id = cur.fetchone()["reservation_id"]
    emitir(cur, "stock.reserved", codigo, {
        "reservation_id": reservation_id, "work_order_id": linea["work_order_id"], "part_id": part_id,
        "quantity": cantidad, "inspection_item_id": linea["inspection_item_id"]})


def liberar_reservas(cur, work_order_id, bom_line_id, cantidad):
    """Quita `cantidad` de las reservas activas de una línea, de la más nueva a la más vieja."""
    cur.execute("""SELECT * FROM reservations WHERE work_order_id = %s AND bom_line_id = %s AND status = 'active'
                   ORDER BY reservation_id DESC""", (work_order_id, bom_line_id))
    for r in cur.fetchall():
        if cantidad <= 0:
            break
        quitar = min(r["quantity"] - r["issued_qty"], cantidad)
        cantidad -= quitar
        if r["issued_qty"] == 0 and quitar == r["quantity"]:
            cur.execute("UPDATE reservations SET status = 'cancelled', updated_at = now() WHERE reservation_id = %s",
                        (r["reservation_id"],))
        else:
            nueva = r["quantity"] - quitar
            estado = "fulfilled" if nueva == r["issued_qty"] else "active"
            cur.execute("UPDATE reservations SET quantity = %s, status = %s, updated_at = now() WHERE reservation_id = %s",
                        (nueva, estado, r["reservation_id"]))


def faltante_abierto(cur, work_order_id, bom_line_id):
    cur.execute("SELECT * FROM shortages WHERE work_order_id = %s AND bom_line_id = %s AND status = 'open'",
                (work_order_id, bom_line_id))
    return cur.fetchone()


def abrir_faltante(cur, codigo, necesidad, cantidad):
    """Crea o actualiza el faltante de una línea. `cantidad` es None si no se sabe cuánto falta."""
    actual = faltante_abierto(cur, necesidad["work_order_id"], necesidad["bom_line_id"])
    if actual and actual["missing_quantity"] == cantidad and actual["part_id"] == necesidad["part_id"]:
        return
    if actual:
        cur.execute("""UPDATE shortages SET missing_quantity = %s, part_id = %s, name = %s, inspection_item_id = %s
                       WHERE shortage_id = %s""",
                    (cantidad, necesidad["part_id"], necesidad["name"], necesidad["inspection_item_id"], actual["shortage_id"]))
    else:
        cur.execute("""INSERT INTO shortages (work_order_id, bom_line_id, inspection_item_id, part_id, name, missing_quantity)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (necesidad["work_order_id"], necesidad["bom_line_id"], necesidad["inspection_item_id"],
                     necesidad["part_id"], necesidad["name"], cantidad))
    emitir(cur, "stock.shortage_detected", codigo, {
        "work_order_id": necesidad["work_order_id"], "part_id": necesidad["part_id"], "name": necesidad["name"],
        "missing_quantity": cantidad, "inspection_item_id": necesidad["inspection_item_id"]})


def cerrar_faltante(cur, work_order_id, bom_line_id, estado):
    cur.execute("""UPDATE shortages SET status = %s, closed_at = now()
                   WHERE work_order_id = %s AND bom_line_id = %s AND status = 'open'""",
                (estado, work_order_id, bom_line_id))


def fijar_necesidad(cur, orden, inspection_id, item, cantidad):
    """Una aprobación fija cuánto necesita la orden de una línea. Si la línea ya tenía
    necesidad, la reemplaza (no la suma) y se reserva o libera la diferencia."""
    cur.execute("""INSERT INTO needs (work_order_id, bom_line_id, inspection_id, inspection_item_id, part_id, name, required_qty)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (work_order_id, bom_line_id) DO UPDATE SET
                     inspection_id = EXCLUDED.inspection_id, inspection_item_id = EXCLUDED.inspection_item_id,
                     part_id = EXCLUDED.part_id, name = EXCLUDED.name, required_qty = EXCLUDED.required_qty,
                     active = true, updated_at = now()
                   RETURNING *""",
                (orden["work_order_id"], item["bom_line_id"], inspection_id, item["inspection_item_id"],
                 item["part_id"], item["name"], cantidad))
    ajustar_linea(cur, orden["code"], cur.fetchone())


def ajustar_linea(cur, codigo, necesidad):
    """Hace que las reservas y el faltante de una línea cuadren con lo que necesita."""
    orden, linea, part_id, requerido = (necesidad["work_order_id"], necesidad["bom_line_id"],
                                        necesidad["part_id"], necesidad["required_qty"])
    if part_id is None or requerido is None:
        # Pieza sin identificar o sin cantidad: no se puede reservar, queda como faltante.
        abrir_faltante(cur, codigo, necesidad, requerido)
        return

    cubierto = reservado_en_linea(cur, orden, linea) + entregado_en_linea(cur, orden, linea)
    if cubierto >= requerido:
        cerrar_faltante(cur, orden, linea, "cancelled")
        if cubierto > requerido:
            liberar_reservas(cur, orden, linea, cubierto - requerido)
            surtir_faltantes(cur, part_id)
    else:
        falta = requerido - cubierto
        tomar = min(max(disponible(cur, part_id), 0), falta)
        if tomar > 0:
            reservar(cur, codigo, necesidad, part_id, tomar)
        if falta > tomar:
            abrir_faltante(cur, codigo, necesidad, falta - tomar)
        else:
            cerrar_faltante(cur, orden, linea, "cancelled")
    actualizar_sugerencia(cur, part_id)


def cancelar_necesidad(cur, necesidad):
    """Por una orden dada de baja o una inspección anulada: libera reservas y cierra el faltante."""
    orden, linea = necesidad["work_order_id"], necesidad["bom_line_id"]
    liberar_reservas(cur, orden, linea, reservado_en_linea(cur, orden, linea))
    cerrar_faltante(cur, orden, linea, "cancelled")
    cur.execute("UPDATE needs SET active = false, updated_at = now() WHERE work_order_id = %s AND bom_line_id = %s",
                (orden, linea))
    if necesidad["part_id"] is not None:
        surtir_faltantes(cur, necesidad["part_id"])  # lo liberado le puede servir a otra orden


def surtir_faltantes(cur, part_id, codigo_primero=None):
    """Usa lo disponible de una pieza para cubrir faltantes abiertos: primero los de
    `codigo_primero` (la orden de la compra) y después del más antiguo al más nuevo."""
    cur.execute("""SELECT s.*, w.code FROM shortages s JOIN work_orders w USING (work_order_id)
                   WHERE s.part_id = %s AND s.status = 'open' AND s.missing_quantity IS NOT NULL
                     AND w.deleted_at IS NULL
                   ORDER BY (w.code = %s) DESC, s.opened_at, s.shortage_id""", (part_id, codigo_primero or ""))
    for faltante in cur.fetchall():
        libre = disponible(cur, part_id)
        if libre <= 0:
            break
        tomar = min(libre, faltante["missing_quantity"])
        reservar(cur, faltante["code"], faltante, part_id, tomar)
        emitir(cur, "stock.shortage_resolved", faltante["code"],
               {"work_order_id": faltante["work_order_id"], "part_id": part_id, "quantity": tomar})
        if tomar == faltante["missing_quantity"]:
            cur.execute("UPDATE shortages SET status = 'resolved', closed_at = now() WHERE shortage_id = %s",
                        (faltante["shortage_id"],))
        else:
            cur.execute("UPDATE shortages SET missing_quantity = missing_quantity - %s WHERE shortage_id = %s",
                        (tomar, faltante["shortage_id"]))
    actualizar_sugerencia(cur, part_id)


def actualizar_sugerencia(cur, part_id):
    """Sugerencia de compra = lo que falta en las órdenes + lo necesario para volver al mínimo."""
    cur.execute("""SELECT COALESCE(SUM(missing_quantity), 0) AS falta,
                          COALESCE(array_agg(DISTINCT work_order_id), '{}') AS ordenes
                   FROM shortages WHERE part_id = %s AND status = 'open'""", (part_id,))
    fila = cur.fetchone()
    cantidad, ordenes = fila["falta"], sorted(fila["ordenes"])

    cur.execute("SELECT * FROM stock_policies WHERE part_id = %s", (part_id,))
    politica = cur.fetchone()
    if politica:
        libre = disponible(cur, part_id)
        if libre < politica["min_qty"]:
            cantidad += politica["reorder_qty"] or (politica["max_qty"] or politica["min_qty"]) - libre

    cur.execute("SELECT * FROM reorder_suggestions WHERE part_id = %s", (part_id,))
    anterior = cur.fetchone()
    if cantidad <= 0:
        cur.execute("DELETE FROM reorder_suggestions WHERE part_id = %s", (part_id,))
        return
    if anterior and anterior["suggested_quantity"] == cantidad and sorted(anterior["work_order_ids"]) == ordenes:
        return
    cur.execute("""INSERT INTO reorder_suggestions (part_id, suggested_quantity, work_order_ids) VALUES (%s, %s, %s)
                   ON CONFLICT (part_id) DO UPDATE SET suggested_quantity = EXCLUDED.suggested_quantity,
                     work_order_ids = EXCLUDED.work_order_ids, updated_at = now()""", (part_id, cantidad, ordenes))
    emitir(cur, "stock.reorder_suggested", f"part:{part_id}",
           {"part_id": part_id, "suggested_quantity": cantidad, "work_order_ids": ordenes})


# --- Entradas -----------------------------------------------------------------------------

def registrar_entrada(cur, part_id, cantidad, costo, moneda, linea_compra, codigo_orden, fecha,
                      location_id=config.UBICACION_RECEPCION):
    movimiento = registrar_movimiento(cur, "receipt", part_id, location_id, cantidad, fecha,
                                      purchase_line_id=linea_compra, unit_cost=costo, currency=moneda,
                                      reference=f"compra línea {linea_compra}")
    emitir(cur, "stock.received", f"part:{part_id}", {
        "part_id": part_id, "location_id": location_id, "quantity": cantidad, "unit_cost": costo,
        "currency": moneda, "purchase_line_id": linea_compra})
    surtir_faltantes(cur, part_id, codigo_orden)
    return movimiento


def guardar_sin_relacionar(cur, compra, candidatas):
    """Compra que no coincide con ninguna pieza (o con varias): queda para revisión manual."""
    cur.execute("""INSERT INTO unmatched_receipts (purchase_line_id, purchase_request_id, request_number, part_number,
                     description, quantity, unit_price, currency, work_order_code, received_at, candidate_part_ids)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (purchase_line_id) DO NOTHING""",
                (compra["line_id"], compra["purchase_request_id"], compra["request_number"], compra["part_number"],
                 compra["description"], compra["quantity"], compra["unit_price"], compra["currency"],
                 compra["work_order_code"], compra["received_at"], [p["part_id"] for p in candidatas]))
    emitir(cur, "stock.unmatched_receipt", f"purchase_line:{compra['line_id']}", {
        "purchase_line_id": compra["line_id"], "part_number": compra["part_number"],
        "description": compra["description"], "quantity": compra["quantity"]})


def relacionar_recepcion(cur, recepcion_id, part_id, location_id=None):
    cur.execute("SELECT * FROM unmatched_receipts WHERE id = %s", (recepcion_id,))
    recepcion = exigir(cur.fetchone(), f"no existe la recepción {recepcion_id}")
    if recepcion["status"] != "pending":
        raise Conflicto("esa recepción ya se relacionó")
    exigir(pieza(cur, part_id), f"no existe la pieza {part_id}")
    exigir(ubicacion(cur, location_id or config.UBICACION_RECEPCION), f"no existe la ubicación {location_id}")
    movimiento = registrar_entrada(cur, part_id, recepcion["quantity"], str(recepcion["unit_price"] or "0.00"),
                                   recepcion["currency"] or "MXN", recepcion["purchase_line_id"],
                                   recepcion["work_order_code"], ahora(), location_id or config.UBICACION_RECEPCION)
    cur.execute("""UPDATE unmatched_receipts SET status = 'resolved', resolved_part_id = %s, resolved_at = now()
                   WHERE id = %s""", (part_id, recepcion_id))
    return movimiento


# --- Operaciones de piso ------------------------------------------------------------------

def registrar_salida(cur, codigo, part_id, location_id, cantidad, entregado_por):
    """El técnico retira una pieza contra su orden. Primero consume lo reservado para esa orden."""
    orden = exigir(orden_por_codigo(cur, codigo), f"no existe la orden {codigo}")
    if orden["deleted_at"]:
        raise Conflicto(f"la orden {codigo} fue dada de baja")
    exigir(pieza(cur, part_id), f"no existe la pieza {part_id}")
    exigir(ubicacion(cur, location_id), f"no existe la ubicación {location_id}")
    if existencia(cur, part_id, location_id) < cantidad:
        raise Conflicto("no hay suficiente en esa ubicación")

    cur.execute("""SELECT * FROM reservations WHERE work_order_id = %s AND part_id = %s AND status = 'active'
                   ORDER BY reservation_id""", (orden["work_order_id"], part_id))
    reservas = cur.fetchall()
    de_la_reserva = min(cantidad, sum(r["quantity"] - r["issued_qty"] for r in reservas))
    if disponible(cur, part_id) < cantidad - de_la_reserva:
        raise Conflicto("lo que hay está reservado para otras órdenes")

    movimiento = registrar_movimiento(cur, "issue", part_id, location_id, -cantidad, work_order_id=orden["work_order_id"],
                                      issued_by=str(entregado_por), reference=codigo)
    pendiente = de_la_reserva
    for r in reservas:
        usar = min(r["quantity"] - r["issued_qty"], pendiente)
        if usar == 0:
            break
        entregado = r["issued_qty"] + usar
        estado = "fulfilled" if entregado == r["quantity"] else "active"
        cur.execute("UPDATE reservations SET issued_qty = %s, status = %s, updated_at = now() WHERE reservation_id = %s",
                    (entregado, estado, r["reservation_id"]))
        pendiente -= usar

    emitir(cur, "stock.issued", codigo, {"work_order_id": orden["work_order_id"], "part_id": part_id,
                                         "location_id": location_id, "quantity": cantidad, "issued_by": entregado_por})
    actualizar_sugerencia(cur, part_id)
    return movimiento


def transferir(cur, part_id, origen, destino, cantidad):
    exigir(pieza(cur, part_id), f"no existe la pieza {part_id}")
    exigir(ubicacion(cur, origen), f"no existe la ubicación {origen}")
    exigir(ubicacion(cur, destino), f"no existe la ubicación {destino}")
    if origen == destino:
        raise Conflicto("el origen y el destino son la misma ubicación")
    if existencia(cur, part_id, origen) < cantidad:
        raise Conflicto("no hay suficiente en la ubicación de origen")

    transferencia = uuid.uuid4()
    registrar_movimiento(cur, "transfer_out", part_id, origen, -cantidad, transfer_id=transferencia,
                         reference=f"a ubicación {destino}")
    entrada = registrar_movimiento(cur, "transfer_in", part_id, destino, cantidad, transfer_id=transferencia,
                                   reference=f"de ubicación {origen}")
    emitir(cur, "stock.transferred", f"part:{part_id}",
           {"part_id": part_id, "from_location_id": origen, "to_location_id": destino, "quantity": cantidad})
    return entrada


def contar(cur, location_id, motivo, lineas):
    """Conteo cíclico: por cada diferencia entre lo contado y el sistema registra un ajuste."""
    if not motivo.strip():
        raise Invalido("el motivo es obligatorio")
    exigir(ubicacion(cur, location_id), f"no existe la ubicación {location_id}")
    ajustes = []
    for linea in lineas:
        part_id = linea["part_id"]
        exigir(pieza(cur, part_id), f"no existe la pieza {part_id}")
        delta = linea["counted_quantity"] - existencia(cur, part_id, location_id)
        if delta == 0:
            continue
        registrar_movimiento(cur, "adjustment", part_id, location_id, delta, reference=motivo)
        emitir(cur, "stock.adjusted", f"part:{part_id}",
               {"part_id": part_id, "location_id": location_id, "delta": delta, "reason": motivo})
        ajustes.append({"part_id": part_id, "delta": delta})
        surtir_faltantes(cur, part_id)
    return ajustes


def guardar_politica(cur, part_id, politica):
    exigir(pieza(cur, part_id), f"no existe la pieza {part_id}")
    if politica["max_qty"] is not None and politica["max_qty"] < politica["min_qty"]:
        raise Invalido("el máximo no puede ser menor que el mínimo")
    cur.execute("""INSERT INTO stock_policies (part_id, min_qty, max_qty, reorder_qty, lead_time_days)
                   VALUES (%(part_id)s, %(min_qty)s, %(max_qty)s, %(reorder_qty)s, %(lead_time_days)s)
                   ON CONFLICT (part_id) DO UPDATE SET min_qty = EXCLUDED.min_qty, max_qty = EXCLUDED.max_qty,
                     reorder_qty = EXCLUDED.reorder_qty, lead_time_days = EXCLUDED.lead_time_days""",
                {"part_id": part_id, **politica})
    actualizar_sugerencia(cur, part_id)
