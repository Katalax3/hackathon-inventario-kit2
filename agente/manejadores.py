"""Qué hacer con cada evento que publica el taller.

Cada función se llama igual que el evento con "_" en lugar de "." (part.upserted -> part_upserted)
y recibe el cursor de la transacción, el campo `data` del evento y el evento completo.
"""

from psycopg.types.json import Jsonb

import inventario
from inventario import Esperar


# --- Catálogo (shop.catalog) --------------------------------------------------------------

def part_upserted(cur, d, evento):
    unidad = d["unit"] or {}
    cur.execute("""INSERT INTO parts (part_id, sku, name, family, "group", subgroup, unit_id, unit_name, image_url, active)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (part_id) DO UPDATE SET sku = EXCLUDED.sku, name = EXCLUDED.name,
                     family = EXCLUDED.family, "group" = EXCLUDED."group", subgroup = EXCLUDED.subgroup,
                     unit_id = EXCLUDED.unit_id, unit_name = EXCLUDED.unit_name, image_url = EXCLUDED.image_url,
                     active = EXCLUDED.active, updated_at = now()""",
                (d["part_id"], d["sku"], d["name"], d["family"], d["group"], d["subgroup"],
                 unidad.get("id"), unidad.get("name"), d["image_url"], d["active"]))


def location_upserted(cur, d, evento):
    cur.execute("""INSERT INTO locations (location_id, code, name, is_workbench, active) VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (location_id) DO UPDATE SET code = EXCLUDED.code, name = EXCLUDED.name,
                     is_workbench = EXCLUDED.is_workbench, active = EXCLUDED.active, updated_at = now()""",
                (d["location_id"], d["code"], d["name"], d["is_workbench"], d["active"]))


def bom_upserted(cur, d, evento):
    """Lista de partes de un modelo. Las líneas que ya no vienen se marcan inactivas."""
    cur.execute("""INSERT INTO models (model_id, model_name, brand) VALUES (%s, %s, %s)
                   ON CONFLICT (model_id) DO UPDATE SET model_name = EXCLUDED.model_name, brand = EXCLUDED.brand,
                     updated_at = now()""", (d["model_id"], d["model_name"], d["brand"]))
    for linea in d["lines"]:
        unidad = (linea["unit"] or {}).get("name")
        cur.execute("""INSERT INTO bom_lines (bom_line_id, model_id, part_id, name, group_name, unit, qty_per_unit, sort_order)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (bom_line_id) DO UPDATE SET model_id = EXCLUDED.model_id, part_id = EXCLUDED.part_id,
                         name = EXCLUDED.name, group_name = EXCLUDED.group_name, unit = EXCLUDED.unit,
                         qty_per_unit = EXCLUDED.qty_per_unit, sort_order = EXCLUDED.sort_order, active = true""",
                    (linea["bom_line_id"], d["model_id"], linea["part_id"], linea["name"], linea["group_name"],
                     unidad, linea["qty_per_unit"], linea["sort_order"]))
    cur.execute("UPDATE bom_lines SET active = false WHERE model_id = %s AND NOT (bom_line_id = ANY(%s))",
                (d["model_id"], [linea["bom_line_id"] for linea in d["lines"]]))


# --- Órdenes de trabajo (shop.work_orders) -------------------------------------------------

def exigir_orden(cur, work_order_id):
    orden = inventario.orden_por_id(cur, work_order_id)
    if orden is None:
        raise Esperar(f"orden {work_order_id}")
    return orden


def work_order_opened(cur, d, evento):
    cur.execute("""INSERT INTO work_orders (work_order_id, code, model_id, department, stage, received_at)
                   VALUES (%s, %s, %s, %s, 'awaiting_quick_inspection', %s)
                   ON CONFLICT (work_order_id) DO UPDATE SET code = EXCLUDED.code, model_id = EXCLUDED.model_id,
                     department = EXCLUDED.department, received_at = EXCLUDED.received_at""",
                (d["work_order_id"], d["code"], d["model_id"], d["department"], d["received_at"]))


def work_order_stage_changed(cur, d, evento):
    exigir_orden(cur, d["work_order_id"])
    cur.execute("UPDATE work_orders SET stage = %s WHERE work_order_id = %s", (d["to_stage"], d["work_order_id"]))


def work_order_deleted(cur, d, evento):
    """Orden dada de baja: se liberan sus reservas y se cierran sus faltantes."""
    exigir_orden(cur, d["work_order_id"])
    cur.execute("UPDATE work_orders SET deleted_at = %s WHERE work_order_id = %s",
                (evento["occurred_at"], d["work_order_id"]))
    cur.execute("SELECT * FROM needs WHERE work_order_id = %s AND active", (d["work_order_id"],))
    for necesidad in cur.fetchall():
        inventario.cancelar_necesidad(cur, necesidad)


# --- Inspecciones (shop.inspections) -------------------------------------------------------

def guardar_inspeccion(cur, d, estado):
    cur.execute("""INSERT INTO inspections (inspection_id, work_order_id, kind, status, approved_by, approved_at, reason, items)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (inspection_id) DO UPDATE SET status = EXCLUDED.status,
                     approved_by = COALESCE(EXCLUDED.approved_by, inspections.approved_by),
                     approved_at = COALESCE(EXCLUDED.approved_at, inspections.approved_at),
                     reason = COALESCE(EXCLUDED.reason, inspections.reason)""",
                (d["inspection_id"], d["work_order_id"], d.get("kind", "quick"), estado, d.get("approved_by"),
                 d.get("approved_at"), d.get("reason"), Jsonb(d.get("items", []))))


def inspection_submitted(cur, d, evento):
    guardar_inspeccion(cur, d, "submitted")


def inspection_rejected(cur, d, evento):
    guardar_inspeccion(cur, d, "rejected")


def inspection_discarded(cur, d, evento):
    guardar_inspeccion(cur, d, "discarded")


def inspection_approved(cur, d, evento):
    """Solo los renglones con action = buy piden piezas. repair y ok son del cliente: no se reservan."""
    orden = exigir_orden(cur, d["work_order_id"])
    guardar_inspeccion(cur, d, "approved")
    if orden["deleted_at"]:
        return
    for item in d["items"]:
        if item["action"] != "buy":
            continue
        if item["part_id"] is not None and inventario.pieza(cur, item["part_id"]) is None:
            raise Esperar(f"pieza {item['part_id']}")
        cantidad = item["quantity"]
        if cantidad is None:
            # Sin cantidad capturada: se usa la de la lista de partes del modelo.
            cur.execute("SELECT qty_per_unit FROM bom_lines WHERE bom_line_id = %s", (item["bom_line_id"],))
            linea = cur.fetchone()
            if linea is None:
                raise Esperar(f"línea de lista de partes {item['bom_line_id']}")
            cantidad = linea["qty_per_unit"]
        inventario.fijar_necesidad(cur, orden, d["inspection_id"], item, cantidad)


def inspection_voided(cur, d, evento):
    """Inspección aprobada que se anula: se libera lo que pidió."""
    guardar_inspeccion(cur, d, "voided")
    cur.execute("SELECT * FROM needs WHERE inspection_id = %s AND active", (d["inspection_id"],))
    for necesidad in cur.fetchall():
        inventario.cancelar_necesidad(cur, necesidad)


# --- Compras (shop.purchasing) --------------------------------------------------------------

def purchase_item_received(cur, d, evento):
    if d["purpose"] != "restock":
        return  # material comprado para un cliente: no entra al almacén
    piezas = inventario.piezas_con_sku(cur, d["part_number"])
    if len(piezas) == 1:
        inventario.registrar_entrada(cur, piezas[0]["part_id"], d["quantity"], d["unit_price"], d["currency"],
                                     d["line_id"], d["work_order_code"], d["received_at"])
    elif segundos_esperando(cur, evento) < 3:
        # La pieza puede venir en camino por shop.catalog: se le da un momento antes de la revisión manual.
        raise Esperar(f"sku {d['part_number']}")
    else:
        inventario.guardar_sin_relacionar(cur, d, piezas)


def segundos_esperando(cur, evento):
    cur.execute("SELECT EXTRACT(EPOCH FROM now() - created_at) AS s FROM pending_events WHERE event_id = %s",
                (evento["event_id"],))
    fila = cur.fetchone()
    return fila["s"] if fila else 0
