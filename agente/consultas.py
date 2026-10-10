"""Consultas de solo lectura que usan la API y las pantallas."""

import re
from datetime import datetime

import config
from inventario import exigir, fecha_texto, movimiento_json, orden_por_codigo, pieza, piezas_con_sku, reservado


def limpiar(fila):
    """Fechas en texto ISO terminado en Z, como pide el contrato."""
    return {k: fecha_texto(v) if isinstance(v, datetime) else v for k, v in fila.items()}


def saldo(cur, part_id):
    p = exigir(pieza(cur, part_id), f"no existe la pieza {part_id}")
    cur.execute("""SELECT location_id, code, on_hand FROM v_stock_by_location
                   WHERE part_id = %s AND on_hand <> 0 ORDER BY code""", (part_id,))
    # Las reservas son por pieza, no por ubicación; por eso reserved es 0 en cada ubicación.
    ubicaciones = [{**f, "reserved": 0, "available": f["on_hand"]} for f in cur.fetchall()]
    total = sum(u["on_hand"] for u in ubicaciones)
    apartado = reservado(cur, part_id)
    cur.execute("SELECT min_qty, max_qty, reorder_qty, lead_time_days FROM stock_policies WHERE part_id = %s", (part_id,))
    return {"part_id": part_id, "sku": p["sku"], "name": p["name"], "on_hand": total, "reserved": apartado,
            "available": total - apartado, "locations": ubicaciones, "policy": cur.fetchone()}


def kardex(cur, part_id):
    exigir(pieza(cur, part_id), f"no existe la pieza {part_id}")
    cur.execute("""SELECT v.*, l.code FROM v_ledger v JOIN locations l USING (location_id)
                   WHERE v.part_id = %s ORDER BY v.occurred_at DESC, v.movement_id DESC""", (part_id,))
    entradas = [{**movimiento_json(f), "balance": f["balance"], "code": f["code"],
                 "work_order_id": f["work_order_id"], "issued_by": f["issued_by"]} for f in cur.fetchall()]
    return {"part_id": part_id, "entries": entradas}


def materiales(cur, codigo):
    orden = exigir(orden_por_codigo(cur, codigo), f"no existe la orden {codigo}")
    # En una orden dada de baja se muestran también las líneas liberadas, como historial.
    cur.execute("""SELECT n.inspection_item_id, n.bom_line_id, n.part_id, n.name, p.sku, n.required_qty AS required,
                     COALESCE(SUM(r.quantity - r.issued_qty) FILTER (WHERE r.status = 'active'), 0)::int AS reserved,
                     COALESCE(SUM(r.issued_qty), 0)::int AS issued,
                     CASE WHEN s.shortage_id IS NULL THEN 0 ELSE s.missing_quantity END AS missing
                   FROM needs n
                   LEFT JOIN parts p ON p.part_id = n.part_id
                   LEFT JOIN reservations r ON r.work_order_id = n.work_order_id AND r.bom_line_id = n.bom_line_id
                   LEFT JOIN shortages s ON s.work_order_id = n.work_order_id AND s.bom_line_id = n.bom_line_id
                                        AND s.status = 'open'
                   WHERE n.work_order_id = %s AND (n.active OR %s)
                   GROUP BY n.work_order_id, n.bom_line_id, p.part_id, s.shortage_id
                   ORDER BY n.bom_line_id""", (orden["work_order_id"], orden["deleted_at"] is not None))
    lineas = cur.fetchall()
    cur.execute("SELECT model_name FROM models WHERE model_id = %s", (orden["model_id"],))
    modelo = cur.fetchone()
    cur.execute("SELECT COUNT(*) AS n FROM inspections WHERE work_order_id = %s AND status = 'approved'",
                (orden["work_order_id"],))
    return {"work_order_id": orden["work_order_id"], "code": orden["code"], "stage": orden["stage"],
            "model_name": modelo["model_name"] if modelo else None, "deleted": orden["deleted_at"] is not None,
            "approved_inspections": cur.fetchone()["n"], "lines": lineas}


def faltantes(cur, codigo=None, part_id=None):
    filtros, valores = ["s.status = 'open'"], []
    if codigo:
        filtros.append("w.code = %s")
        valores.append(codigo)
    if part_id:
        filtros.append("s.part_id = %s")
        valores.append(part_id)
    cur.execute(f"""SELECT s.shortage_id, s.work_order_id, w.code AS work_order_code, s.part_id, p.sku, s.name,
                      s.missing_quantity, s.inspection_item_id, s.opened_at
                    FROM shortages s JOIN work_orders w ON w.work_order_id = s.work_order_id
                    LEFT JOIN parts p ON p.part_id = s.part_id
                    WHERE {' AND '.join(filtros)} ORDER BY s.opened_at, s.shortage_id""", valores)
    return {"items": [limpiar(f) for f in cur.fetchall()]}


def sugerencias(cur):
    cur.execute("""SELECT r.part_id, p.sku, p.name, r.suggested_quantity, r.work_order_ids, r.updated_at
                   FROM reorder_suggestions r JOIN parts p ON p.part_id = r.part_id
                   ORDER BY r.suggested_quantity DESC, p.name""")
    return {"items": [limpiar(f) for f in cur.fetchall()]}


def sin_relacionar(cur):
    cur.execute("""SELECT id, purchase_line_id, part_number, description, quantity, unit_price::text AS unit_price,
                     currency, received_at, work_order_code, candidate_part_ids
                   FROM unmatched_receipts WHERE status = 'pending' ORDER BY id""")
    return {"items": [limpiar(f) for f in cur.fetchall()]}


def existencias(cur):
    """Para la pantalla de almacén: piezas con existencia o reservas, con su detalle por ubicación."""
    cur.execute("""SELECT a.part_id, p.sku, p.name, a.on_hand, a.reserved, a.available,
                     (SELECT json_agg(json_build_object('location_id', s.location_id, 'code', s.code, 'on_hand', s.on_hand)
                                      ORDER BY s.code)
                      FROM v_stock_by_location s WHERE s.part_id = a.part_id AND s.on_hand <> 0) AS locations
                   FROM v_availability a JOIN parts p ON p.part_id = a.part_id
                   WHERE a.on_hand <> 0 OR a.reserved <> 0
                   ORDER BY p.name""")
    return {"items": cur.fetchall()}


def stock_en_ubicacion(cur, location_id):
    cur.execute("""SELECT s.part_id, p.sku, p.name, s.on_hand FROM v_stock_by_location s
                   JOIN parts p ON p.part_id = s.part_id
                   WHERE s.location_id = %s AND s.on_hand <> 0 ORDER BY p.name""", (location_id,))
    return {"items": cur.fetchall()}


def ordenes(cur, solo_demo=False):
    """Órdenes vivas. Con `solo_demo`, las abiertas desde el panel del simulador, incluidas las dadas de baja."""
    if solo_demo:
        filtro, valores = "w.work_order_id BETWEEN %s AND %s", config.ORDENES_DEMO
    else:
        filtro, valores = "w.deleted_at IS NULL", ()
    cur.execute(f"""SELECT w.work_order_id, w.code, m.model_name, w.stage, w.deleted_at IS NOT NULL AS dada_de_baja,
                      (SELECT COUNT(*) FROM reservations r WHERE r.work_order_id = w.work_order_id AND r.status = 'active') AS reservas,
                      (SELECT COUNT(*) FROM shortages s WHERE s.work_order_id = w.work_order_id AND s.status = 'open') AS faltantes
                    FROM work_orders w LEFT JOIN models m ON m.model_id = w.model_id
                    WHERE {filtro}
                    ORDER BY w.received_at DESC NULLS LAST, w.work_order_id DESC LIMIT 200""", valores)
    return {"items": cur.fetchall()}


def buscar(cur, texto):
    """Interpreta lo escaneado o escrito: código de ubicación, sku, P-<part_id> o parte del nombre."""
    texto = texto.strip()
    cur.execute("""SELECT location_id, code, name FROM locations WHERE upper(code) = upper(%s)
                   ORDER BY location_id LIMIT 1""", (texto,))
    ubicaciones = cur.fetchall()

    piezas = piezas_con_sku(cur, texto)
    sin_sku = re.fullmatch(r"[Pp]-(\d+)", texto)
    if not piezas and sin_sku:
        cur.execute("SELECT part_id, sku, name FROM parts WHERE part_id = %s", (int(sin_sku.group(1)),))
        piezas = cur.fetchall()
    if not piezas and not ubicaciones and len(texto) >= 2:
        cur.execute("""SELECT part_id, sku, name FROM parts WHERE name ILIKE %s OR sku ILIKE %s
                       ORDER BY name LIMIT 20""", (f"%{texto}%", f"%{texto}%"))
        piezas = cur.fetchall()
    return {"locations": ubicaciones, "parts": piezas}


def catalogo(cur):
    """Opciones para sugerir en los campos que se escriben a mano."""
    # Si un código de ubicación se repite (lo hacen las pruebas), queda el de ID más bajo, igual que al escanear.
    cur.execute("""SELECT DISTINCT ON (code) location_id, code, name FROM locations
                   WHERE active ORDER BY code, location_id""")
    ubicaciones = cur.fetchall()
    cur.execute("SELECT part_id, sku, name FROM parts WHERE active ORDER BY name")
    piezas = cur.fetchall()
    cur.execute("SELECT DISTINCT issued_by AS v FROM movements WHERE type = 'issue' AND issued_by IS NOT NULL ORDER BY 1")
    tecnicos = [f["v"] for f in cur.fetchall()]
    cur.execute("SELECT DISTINCT reference AS v FROM movements WHERE type = 'adjustment' AND reference IS NOT NULL ORDER BY 1")
    motivos = [f["v"] for f in cur.fetchall()]
    return {"locations": ubicaciones, "parts": piezas, "technicians": tecnicos, "count_reasons": motivos}


def resumen(cur, estado_consumidor):
    cur.execute("""SELECT (SELECT COUNT(*) FROM processed_events) AS eventos_procesados,
                          (SELECT COUNT(*) FROM pending_events) AS eventos_esperando,
                          (SELECT COUNT(*) FROM outbox WHERE published_at IS NULL) AS salidas_por_publicar""")
    return {**cur.fetchone(), **estado_consumidor, "lag_total": sum(estado_consumidor["lag"].values())}
