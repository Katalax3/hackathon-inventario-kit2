# Agente de inventario del taller

Servicio que escucha lo que pasa en el taller (por Kafka), lleva el inventario en PostgreSQL
y lo muestra en pantallas web. Resuelve el reto descrito en el PDF y en `../contract/`.

```
Simulador del taller ──Kafka──► consumidor.py ──► PostgreSQL (inventario_agente)
  (shop.catalog, shop.work_orders,                    │
   shop.inspections, shop.purchasing)                 ▼
                                    api.py ◄── pantallas web (web/)
                                       │
                 inventory.events ◄────┘  (eventos de salida, vía la tabla outbox)
```

## Qué necesitas

- El kit levantado: `docker compose up -d` en la carpeta de arriba.
- PostgreSQL con la base `inventario_agente` (ver abajo).
- [uv](https://docs.astral.sh/uv/). Instala solo Python 3.12 y las librerías. Hace falta 3.12 porque
  `confluent-kafka` todavía no funciona con Python 3.14.

## 1. Crear la base de datos (una sola vez)

La base sale de `../inventario.dump`, que trae las tablas vacías. En el contenedor `postsql`:

```bash
docker cp ../inventario.dump postsql:/tmp/inventario.dump
docker exec postsql createdb -U postgres inventario_agente
docker exec postsql pg_restore -U postgres -d inventario_agente --no-owner /tmp/inventario.dump
docker exec postsql rm /tmp/inventario.dump
```

La conexión se configura con la variable `BASE_DATOS`. El valor por defecto está en
`config.py`: `postgresql://postgres:its@localhost:5432/inventario_agente`.

## 2. Arrancar

Desde la carpeta del kit:

```bash
uv run --python 3.12 --with-requirements agente/requirements.txt agente/main.py
```

La primera vez lee todo el historial de los tópicos (unos 1,700 eventos, cerca de un minuto).
En la pantalla de Almacén, "Por leer (lag)" llega a 0 cuando termina.

| Pantalla | Dirección | Para qué |
|---|---|---|
| Almacén | http://localhost:8000 | Existencias, faltantes, sugerencias de compra, recepciones por relacionar |
| Orden | http://localhost:8000/orden.html | Qué tiene reservado, entregado y pendiente una orden |
| Piso | http://localhost:8000/piso.html | Retirar, transferir y contar escaneando (pensada para el celular) |
| Kardex | http://localhost:8000/kardex.html | Movimientos de una pieza con su saldo, mínimo y máximo |
| API | http://localhost:8000/docs | Documentación automática de todas las rutas |

### Usarlo desde el celular (cámara)

El navegador del celular solo presta la cámara en páginas `https`. Si existen
`agente/certificado.pem` y `agente/llave.pem`, el servicio también responde en el puerto 8443:

```bash
# Cambia 10.0.111.63 por la IP de tu computadora en la red (ip route get 1.1.1.1)
openssl req -x509 -newkey rsa:2048 -nodes -days 30 -keyout agente/llave.pem -out agente/certificado.pem \
  -subj "/CN=inventario-taller" -addext "subjectAltName=IP:10.0.111.63,IP:127.0.0.1,DNS:localhost"
```

En el celular, conectado a la misma red, abre `https://10.0.111.63:8443/piso.html` y acepta el
aviso de "conexión no privada". Esa advertencia sale porque el certificado lo firmaste tú.

Las etiquetas son Code 128 (`../labels/etiquetas-demo.pdf`). En cada campo puedes tocar 📷 o
escribir el código; un lector USB también funciona porque "escribe" y manda Enter.

## 3. Reconstruir desde cero

Borra los datos de la base y vuelve a leer los tópicos desde el inicio:

```bash
uv run --python 3.12 --with-requirements agente/requirements.txt agente/main.py --reconstruir
```

Las salidas, transferencias, conteos y recepciones relacionadas a mano se hacen por la API y no
viven en Kafka, así que la reconstrucción no las recupera. Por eso el jurado revisa la
reconstrucción antes de la demo.

## 4. Pruebas de contrato

Con el servicio corriendo:

```bash
uv run --python 3.12 --with confluent-kafka==2.6.1 --with jsonschema contract-tests/run_tests.py
```

## Archivos

| Archivo | Qué hace |
|---|---|
| `main.py` | Arranca todo: consumidor, publicador de eventos y servidor web |
| `config.py` | Direcciones del broker y de la base, puertos |
| `base_datos.py` | Conexión a PostgreSQL y el candado de escritura |
| `consumidor.py` | Lee Kafka, aplica cada evento una sola vez, reintenta lo que llega antes de tiempo, manda errores a `inventory.dlq` y publica `inventory.events` |
| `manejadores.py` | Qué hacer con cada evento del taller (una función por tipo de evento) |
| `inventario.py` | Las reglas: saldos, reservas, faltantes, entradas, salidas, transferencias, conteos |
| `consultas.py` | Lecturas para la API y las pantallas |
| `api.py` | Rutas HTTP (las del contrato y unas extra para las pantallas) |
| `web/` | Pantallas: HTML, CSS y JavaScript sin frameworks |

## Decisiones (resumen para el documento de una página)

- **Idempotencia.** Cada evento aplicado guarda su `event_id` en `processed_events` dentro de la
  misma transacción que sus cambios. Un evento repetido se reconoce y se ignora. Las compras
  además tienen un índice único por línea de compra.
- **Reservas dobles y concurrencia.** Toda escritura (eventos y API) toma el mismo candado de
  PostgreSQL (`pg_advisory_xact_lock`), así que nunca hay dos reservas calculándose a la vez sobre
  la misma existencia. Lo disponible se recalcula dentro de la transacción, y nunca se reserva
  más de lo que hay.
- **Compras y catálogo.** Se compara `part_number` con el `sku` sin importar mayúsculas ni espacios
  de más. Si no coincide con ninguna pieza se espera 3 segundos, por si la pieza viene en camino
  por `shop.catalog`. Si sigue sin coincidir, o coincide con varias piezas, va a revisión manual
  (`/unmatched-receipts`), donde se elige la pieza.
- **Kits.** Se inventarían los componentes, porque cada línea de la lista de partes trae su
  `part_id`. Una línea sin `part_id` (por ejemplo, un kit sin clave) no se puede reservar y queda
  como faltante sin identificar.
- **Desorden.** Si un evento menciona algo que aún no existe (orden, pieza, línea), se guarda en
  `pending_events` y se reintenta cada segundo. Los eventos de la misma llave esperan en fila para
  no perder su orden. Al arrancar se lee todo `shop.catalog` antes que lo demás.
- **Libro mayor.** El stock nunca se edita: siempre es la suma de `movements`, y la base impide
  modificar o borrar movimientos.
