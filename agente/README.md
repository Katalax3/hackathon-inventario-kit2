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

- El kit levantado (`docker compose up -d` en la carpeta del kit).
- PostgreSQL corriendo. La base no hace falta crearla: si no existe, el agente la crea con las tablas
  de `esquema.sql` (sacadas de `inventario.dump`).
- [uv](https://docs.astral.sh/uv/). Instala solo Python 3.12 y las librerías. Hace falta 3.12 porque
  `confluent-kafka` todavía no funciona con Python 3.14.

## Arrancar (un comando)

Desde la carpeta del kit:

```bash
uv run --python 3.12 --with-requirements agente/requirements.txt agente/main.py
```

La conexión se configura con la variable `BASE_DATOS`; el valor por defecto está en `config.py`:
`postgresql://postgres:its@localhost:5432/inventario_agente`.

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

## Reconstruir desde cero

El agente anota en orden todo lo que aplica (eventos del taller y operaciones de piso) en su propio
tópico, `inventory.bitacora`. Si la base se borra, basta con volver a arrancarlo: la crea, repite
la bitácora y la deja **exactamente** como estaba, incluidas salidas, transferencias, conteos,
recepciones relacionadas a mano y mínimos/máximos. Para vaciarla a propósito y reconstruir:

```bash
uv run --python 3.12 --with-requirements agente/requirements.txt agente/main.py --reconstruir
```

Mientras reconstruye, la API retiene las consultas hasta que termina (para no mostrar una base a
medias) y rechaza las escrituras con 503.

Si reinicias el kit desde cero (`RESET=1`), borra también la bitácora, o se repetiría la historia
anterior: `docker compose exec redpanda rpk topic delete inventory.bitacora`.

## Pruebas de contrato

Con el servicio corriendo, como las corre el jurado:

```bash
docker compose run --rm contract-tests --api http://host.docker.internal:8000
```

Si tu firewall (por ejemplo `ufw`) bloquea lo que entra desde las redes de Docker, las pruebas no
alcanzan la API ("la API no responde... timed out"). Permite los puertos del agente:

```bash
sudo ufw allow from 172.16.0.0/12 to any port 8000 proto tcp   # pruebas en Docker
sudo ufw allow 8443/tcp                                          # celular en la misma red
```

## Archivos

| Archivo | Qué hace |
|---|---|
| `main.py` | Arranca el agente: consumidor, publicador de eventos y servidor web |
| `config.py` | Direcciones del broker y de la base, puertos |
| `base_datos.py` | Conexión a PostgreSQL, el candado de escritura y la creación de la base si no existe |
| `bitacora.py` | Anota en orden todo lo que se aplica, para reconstruir la base exactamente |
| `esquema.sql` | Las tablas, sacadas de `inventario.dump` |
| `consumidor.py` | Lee Kafka, aplica cada evento una sola vez, reintenta lo que llega antes de tiempo, manda errores a `inventory.dlq` y publica `inventory.events` |
| `manejadores.py` | Qué hacer con cada evento del taller (una función por tipo de evento) |
| `inventario.py` | Las reglas: saldos, reservas, faltantes, entradas, salidas, transferencias, conteos |
| `consultas.py` | Lecturas para la API y las pantallas |
| `api.py` | Rutas HTTP (las del contrato y unas extra para las pantallas) |
| `web/` | Pantallas: HTML, CSS y JavaScript sin frameworks |

## Decisiones

Cómo relacionamos compras con el catálogo, cómo manejamos kits, cómo evitamos reservas dobles y
cómo garantizamos idempotencia: [DECISIONES.md](DECISIONES.md).
