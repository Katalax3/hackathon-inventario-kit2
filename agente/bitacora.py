"""Bitácora: copia en orden, en el tópico inventory.bitacora, de todo lo que el agente aplica.

Los eventos del taller viven en sus tópicos, pero no el orden en que se aplicaron. Las
operaciones de piso (salidas, transferencias, conteos, recepciones relacionadas a mano y
mínimos/máximos) solo pasan por la API. Si se borra la base, repetir la bitácora en el mismo
orden deja todo exactamente como estaba (ver consumidor.reconstruir_desde_bitacora).
"""

from psycopg.types.json import Jsonb

import inventario

TOPICO = "inventory.bitacora"

# Cada operación de piso recibe (cursor, datos, fecha). La fecha se guarda para que al repetirla
# los movimientos queden con la misma hora.
OPERACIONES = {
    "salida": lambda cur, d, fecha: inventario.registrar_salida(
        cur, d["work_order_code"], d["part_id"], d["location_id"], d["quantity"], d["issued_by"], fecha),
    "transferencia": lambda cur, d, fecha: inventario.transferir(
        cur, d["part_id"], d["from_location_id"], d["to_location_id"], d["quantity"], fecha),
    "conteo": lambda cur, d, fecha: inventario.contar(cur, d["location_id"], d["reason"], d["lines"], fecha),
    "relacion": lambda cur, d, fecha: inventario.relacionar_recepcion(
        cur, d["id"], d["part_id"], d.get("location_id"), fecha),
    "politica": lambda cur, d, fecha: inventario.guardar_politica(cur, d["part_id"], d["politica"]),
}


def anotar(cur, registro):
    """Guarda el registro en la misma transacción que el cambio; publicar_salidas() lo manda a Kafka."""
    if not inventario.reproduciendo:  # al repetir la bitácora no se vuelve a anotar
        cur.execute("INSERT INTO bitacora (registro) VALUES (%s)", (Jsonb(registro),))


def ejecutar(cur, operacion, datos, fecha=None):
    """Aplica una operación de piso y la anota en la bitácora."""
    fecha = fecha or inventario.ahora().isoformat()
    resultado = OPERACIONES[operacion](cur, datos, fecha)
    anotar(cur, {"tipo": "operacion", "operacion": operacion, "datos": datos, "fecha": fecha})
    return resultado
