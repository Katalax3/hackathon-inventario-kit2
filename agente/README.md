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

- Docker con el contenedor de PostgreSQL corriendo (por defecto se llama `postsql`; otro nombre se
  indica con la variable `CONTENEDOR_POSTGRES`).
- [uv](https://docs.astral.sh/uv/). Instala solo Python 3.12 y las librerías. Hace falta 3.12 porque
  `confluent-kafka` todavía no funciona con Python 3.14.

## Arrancar (un comando)

Desde la carpeta del kit:

```bash
agente/arrancar.sh
```

El script levanta el kit (`docker compose up -d`). La primera vez también crea la base
`inventario_agente` desde `inventario.dump` (las tablas vacías), y al final arranca el agente.
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

Borra los datos de la base y vuelve a leer los tópicos desde el inicio:

```bash
agente/arrancar.sh --reconstruir
```

Las salidas, transferencias, conteos y recepciones relacionadas a mano se hacen por la API y no
viven en Kafka, así que la reconstrucción no las recupera. Por eso el jurado revisa la
reconstrucción antes de la demo.

## Pruebas de contrato

Con el servicio corriendo:

```bash
uv run --python 3.12 --with confluent-kafka==2.6.1 --with jsonschema contract-tests/run_tests.py
```

## Archivos

| Archivo | Qué hace |
|---|---|
| `arrancar.sh` | Un comando: levanta el kit, crea la base la primera vez y arranca el agente |
| `main.py` | Arranca el agente: consumidor, publicador de eventos y servidor web |
| `config.py` | Direcciones del broker y de la base, puertos |
| `base_datos.py` | Conexión a PostgreSQL y el candado de escritura |
| `consumidor.py` | Lee Kafka, aplica cada evento una sola vez, reintenta lo que llega antes de tiempo, manda errores a `inventory.dlq` y publica `inventory.events` |
| `manejadores.py` | Qué hacer con cada evento del taller (una función por tipo de evento) |
| `inventario.py` | Las reglas: saldos, reservas, faltantes, entradas, salidas, transferencias, conteos |
| `consultas.py` | Lecturas para la API y las pantallas |
| `api.py` | Rutas HTTP (las del contrato y unas extra para las pantallas) |
| `web/` | Pantallas: HTML, CSS y JavaScript sin frameworks |

## Decisiones

Cómo relacionamos compras con el catálogo, cómo manejamos kits, cómo evitamos reservas dobles y
cómo garantizamos idempotencia: [DECISIONES.md](DECISIONES.md).
