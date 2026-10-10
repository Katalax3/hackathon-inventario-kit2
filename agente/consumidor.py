"""Lee los eventos del taller desde Kafka, los aplica en la base y publica los nuestros."""

import json
import time
import traceback

from confluent_kafka import OFFSET_BEGINNING, Consumer, Producer, TopicPartition
from confluent_kafka.admin import AdminClient, NewTopic
from psycopg.types.json import Jsonb

import bitacora
import config
import inventario
import manejadores
from base_datos import conexion, transaccion
from inventario import Esperar

TOPICO_CATALOGO = "shop.catalog"
TOPICOS_TALLER = ["shop.work_orders", "shop.inspections", "shop.purchasing"]

# Lo muestra la pantalla de almacén (observabilidad del consumidor).
# "reconstruyendo" empieza en True: la API no acepta escrituras hasta revisar si hay que repetir la bitácora.
estado = {"catalogo_cargado": False, "reconstruyendo": True, "lag": {}, "errores": 0, "ultimo_error": None}
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
        bitacora.anotar(cur, {"tipo": "evento", "topic": topico, "evento": evento})


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
            bitacora.anotar(cur, {"tipo": "descartado", "topic": topico, "evento": evento})
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
    global productor
    if productor is None:
        productor = Producer({"bootstrap.servers": config.BROKER})
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
    reconstruir_desde_bitacora()

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
    """Manda a Kafka lo pendiente: los eventos de salida (outbox) y la bitácora."""
    salida = Producer({"bootstrap.servers": config.BROKER, "enable.idempotence": True})
    while True:
        enviados = publicar_outbox(salida) + publicar_bitacora(salida)
        if not enviados:
            time.sleep(0.2)


def publicar_outbox(salida):
    with conexion() as cur:
        cur.execute("SELECT id, event_key, payload FROM outbox WHERE published_at IS NULL ORDER BY id LIMIT 500")
        filas = cur.fetchall()
    for fila in filas:
        salida.produce("inventory.events", key=fila["event_key"].encode(),
                       value=json.dumps(fila["payload"], ensure_ascii=False).encode())
    salida.flush(10)
    if filas:
        with conexion() as cur:
            cur.execute("UPDATE outbox SET published_at = now() WHERE id = ANY(%s)", ([f["id"] for f in filas],))
    return len(filas)


def publicar_bitacora(salida):
    """En el orden en que se aplicó (por id), a un tópico de una sola partición."""
    with conexion() as cur:
        cur.execute("SELECT id, registro FROM bitacora WHERE NOT publicado ORDER BY id LIMIT 500")
        filas = cur.fetchall()
    for fila in filas:
        salida.produce(bitacora.TOPICO, value=json.dumps(fila["registro"], ensure_ascii=False).encode())
    salida.flush(10)
    if filas:
        with conexion() as cur:
            cur.execute("UPDATE bitacora SET publicado = true WHERE id = ANY(%s)", ([f["id"] for f in filas],))
    return len(filas)


# --- Reconstrucción ----------------------------------------------------------------------

def preparar_bitacora():
    """Crea el tópico de la bitácora si no existe. Una sola partición para conservar el orden."""
    admin = AdminClient({"bootstrap.servers": config.BROKER})
    if bitacora.TOPICO not in admin.list_topics(timeout=30).topics:
        admin.create_topics([NewTopic(bitacora.TOPICO, num_partitions=1, replication_factor=1)])[bitacora.TOPICO].result()


def reconstruir_desde_bitacora():
    """Si la base está vacía (se borró, o se pidió --reconstruir), repite la bitácora en orden
    y la deja exactamente como estaba. Lo que no alcanzó a anotarse se lee después de los
    tópicos del taller, como siempre. Mientras dura, la API solo acepta lecturas."""
    with conexion() as cur:
        cur.execute("SELECT EXISTS (SELECT 1 FROM processed_events) AS hay_datos")
        hay_datos = cur.fetchone()["hay_datos"]
    lector = abrir_consumidor([bitacora.TOPICO])
    inicio, final = lector.get_watermark_offsets(TopicPartition(bitacora.TOPICO, 0), timeout=10)
    if hay_datos or final <= inicio:
        lector.close()
        estado["reconstruyendo"] = False
        return
    print(f"base vacía: se repiten {final - inicio} registros de la bitácora", flush=True)
    inventario.reproduciendo = True
    try:
        leido = inicio
        while leido < final:
            mensaje = lector.poll(1)
            if mensaje is None or mensaje.error():
                continue
            repetir(json.loads(mensaje.value()))
            leido = mensaje.offset() + 1
    finally:
        inventario.reproduciendo = False
        estado["reconstruyendo"] = False
        lector.close()
    print("bitácora repetida: la base quedó como estaba", flush=True)


def repetir(registro):
    if registro["tipo"] == "evento":
        procesar(registro["topic"], registro["evento"])
    elif registro["tipo"] == "descartado":
        with conexion() as cur:
            evento = registro["evento"]
            cur.execute("""INSERT INTO processed_events (event_id, event_type, topic) VALUES (%s, %s, %s)
                           ON CONFLICT DO NOTHING""", (evento["event_id"], evento.get("event_type", "?"), registro["topic"]))
    else:
        try:
            with transaccion() as cur:
                bitacora.ejecutar(cur, registro["operacion"], registro["datos"], registro["fecha"])
        except Exception:
            traceback.print_exc()  # no debería pasar: se repite sobre el mismo estado que la primera vez


def sin_detenerse(funcion):
    """Si algo truena (por ejemplo, se cae el broker), espera y vuelve a empezar."""
    while True:
        try:
            funcion()
        except Exception:
            traceback.print_exc()
            time.sleep(5)
