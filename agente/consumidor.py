"""Lee los eventos del taller desde Kafka, los aplica en la base y publica los nuestros."""

import json
import time
import traceback

from confluent_kafka import OFFSET_BEGINNING, Consumer, Producer, TopicPartition
from psycopg.types.json import Jsonb

import config
import manejadores
from base_datos import conexion, transaccion
from inventario import Esperar

TOPICO_CATALOGO = "shop.catalog"
TOPICOS_TALLER = ["shop.work_orders", "shop.inspections", "shop.purchasing"]

# Lo muestra la pantalla de almacén (observabilidad del consumidor).
estado = {"catalogo_cargado": False, "lag": {}, "errores": 0, "ultimo_error": None}
productor = None


def abrir_consumidor(topicos):
    """Consumidor que siempre lee desde el inicio de los tópicos. No hace falta recordar
    hasta dónde se leyó: lo ya aplicado se reconoce por su event_id en processed_events."""
    consumidor = Consumer({"bootstrap.servers": config.BROKER, "group.id": "inventario-agente",
                           "enable.auto.commit": False, "fetch.wait.max.ms": 50})
    particiones = []
    for topico in topicos:
        while topico not in consumidor.list_topics(timeout=10).topics:
            print(f"esperando a que exista el tópico {topico}...", flush=True)
            time.sleep(2)
        info = consumidor.list_topics(topico, timeout=10).topics[topico]
        particiones += [TopicPartition(topico, p, OFFSET_BEGINNING) for p in info.partitions]
    consumidor.assign(particiones)
    return consumidor


def leer_hasta_el_final(consumidor):
    """Aplica todo lo que ya hay en los tópicos del consumidor y regresa."""
    faltan = {}
    for particion in consumidor.assignment():
        inicio, final = consumidor.get_watermark_offsets(particion, timeout=10)
        if final > inicio:
            faltan[particion.partition] = final
    while faltan:
        mensaje = consumidor.poll(1)
        if mensaje is None or mensaje.error():
            continue
        recibir(mensaje)
        if mensaje.offset() + 1 >= faltan.get(mensaje.partition(), 0):
            faltan.pop(mensaje.partition(), None)


def recibir(mensaje):
    try:
        evento = json.loads(mensaje.value())
    except ValueError:
        mandar_a_dlq(mensaje.key(), mensaje.value(), "no es JSON")
        return
    procesar(mensaje.topic(), evento)


def aplicar(topico, evento):
    with transaccion() as cur:
        cur.execute("SELECT 1 FROM processed_events WHERE event_id = %s", (evento["event_id"],))
        if cur.fetchone():
            return  # duplicado: ya se aplicó antes

        # Los eventos de una misma llave van en orden: si uno anterior está esperando, este también.
        cur.execute("SELECT event_id FROM pending_events WHERE topic = %s AND event_key = %s ORDER BY id LIMIT 1",
                    (topico, evento["key"]))
        primero = cur.fetchone()
        if primero and str(primero["event_id"]) != evento["event_id"]:
            raise Esperar("un evento anterior de la misma llave")

        manejador = getattr(manejadores, evento["event_type"].replace(".", "_"))
        manejador(cur, evento["data"], evento)

        # Se marca como aplicado en la misma transacción: o pasan las dos cosas o ninguna.
        cur.execute("INSERT INTO processed_events (event_id, event_type, topic) VALUES (%s, %s, %s)",
                    (evento["event_id"], evento["event_type"], topico))
        cur.execute("DELETE FROM pending_events WHERE event_id = %s", (evento["event_id"],))


def procesar(topico, evento):
    error = None
    for intento in range(3):
        try:
            aplicar(topico, evento)
            return
        except Esperar as motivo:
            encolar(topico, evento, str(motivo))
            return
        except Exception as e:
            error = e
            traceback.print_exc()
            time.sleep(0.2)
    mandar_a_dlq(str(evento.get("key") or "").encode(), json.dumps(evento).encode(), str(error))
    try:
        with conexion() as cur:
            cur.execute("DELETE FROM pending_events WHERE event_id = %s", (evento.get("event_id"),))
            cur.execute("""INSERT INTO processed_events (event_id, event_type, topic) VALUES (%s, %s, %s)
                           ON CONFLICT DO NOTHING""", (evento.get("event_id"), evento.get("event_type", "?"), topico))
    except Exception:
        pass  # el evento ni siquiera trae un event_id válido; ya quedó en la DLQ


def encolar(topico, evento, motivo):
    with conexion() as cur:
        cur.execute("""INSERT INTO pending_events (event_id, topic, event_type, event_key, payload, waiting_for)
                       VALUES (%s, %s, %s, %s, %s, %s)
                       ON CONFLICT (event_id) DO UPDATE SET attempts = pending_events.attempts + 1,
                         waiting_for = EXCLUDED.waiting_for, next_attempt_at = now()""",
                    (evento["event_id"], topico, evento["event_type"], evento["key"], Jsonb(evento), motivo))


def reintentar_pendientes():
    with conexion() as cur:
        cur.execute("SELECT topic, payload FROM pending_events ORDER BY id")
        pendientes = cur.fetchall()
    for pendiente in pendientes:
        procesar(pendiente["topic"], pendiente["payload"])


def mandar_a_dlq(llave, valor, error):
    """Lo que no se pudo procesar va a inventory.dlq sin detener el consumo."""
    estado["errores"] += 1
    estado["ultimo_error"] = error
    print(f"a inventory.dlq: {error}", flush=True)
    productor.produce("inventory.dlq", key=llave, value=valor, headers={"error": error[:500]})
    productor.flush(5)


def medir_lag(consumidores):
    """Cuántos eventos faltan por leer en cada partición."""
    for consumidor in consumidores:
        for particion in consumidor.position(consumidor.assignment()):
            inicio, final = consumidor.get_watermark_offsets(particion, timeout=5)
            leido = max(particion.offset, inicio)
            estado["lag"][f"{particion.topic}[{particion.partition}]"] = final - leido


def consumir():
    global productor
    productor = Producer({"bootstrap.servers": config.BROKER})

    # Regla 22: primero todo el catálogo; si no, las compras llegarían antes que sus piezas.
    catalogo = abrir_consumidor([TOPICO_CATALOGO])
    leer_hasta_el_final(catalogo)
    estado["catalogo_cargado"] = True
    print("catálogo cargado; leyendo órdenes, inspecciones y compras", flush=True)

    taller = abrir_consumidor(TOPICOS_TALLER)
    siguiente_reintento = siguiente_medicion = 0
    while True:
        # El catálogo tiene prioridad para conocer las piezas antes de que se usen.
        mensaje = catalogo.poll(0)
        if mensaje is None:
            mensaje = taller.poll(0.2)
        if mensaje is not None and not mensaje.error():
            recibir(mensaje)

        if time.monotonic() > siguiente_reintento:
            reintentar_pendientes()
            siguiente_reintento = time.monotonic() + 1
        if time.monotonic() > siguiente_medicion:
            medir_lag([catalogo, taller])
            siguiente_medicion = time.monotonic() + 5


def publicar_salidas():
    """Manda a inventory.events lo que quedó en la tabla outbox."""
    salida = Producer({"bootstrap.servers": config.BROKER, "enable.idempotence": True})
    while True:
        with conexion() as cur:
            cur.execute("SELECT id, event_key, payload FROM outbox WHERE published_at IS NULL ORDER BY id LIMIT 500")
            filas = cur.fetchall()
        if not filas:
            time.sleep(0.2)
            continue
        for fila in filas:
            salida.produce("inventory.events", key=fila["event_key"].encode(),
                           value=json.dumps(fila["payload"], ensure_ascii=False).encode())
        salida.flush(10)
        with conexion() as cur:
            cur.execute("UPDATE outbox SET published_at = now() WHERE id = ANY(%s)", ([f["id"] for f in filas],))


def sin_detenerse(funcion):
    """Si algo truena (por ejemplo, se cae el broker), espera y vuelve a empezar."""
    while True:
        try:
            funcion()
        except Exception:
            traceback.print_exc()
            time.sleep(5)
