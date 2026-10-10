# Documento de decisiones

Python (FastAPI + confluent-kafka) sobre PostgreSQL. El stock es siempre la suma de `movements`,
que un trigger hace inmutable.

## 1. ¿Cómo relacionamos las compras con el catálogo?

- `part_number` se normaliza (sin espacios de más, en mayúsculas) y se compara con el `sku`
  normalizado de cada pieza. **Una coincidencia:** entrada en `U-100` y `stock.received`.
- **Ninguna** (números de proveedor, o una pieza que aún no tiene `sku`) **o varias** (piezas con el
  mismo `sku`): no se adivina. Va a `unmatched_receipts`, emite `stock.unmatched_receipt` y se
  relaciona a mano en Almacén.
- Se esperan 3 s por si la pieza aún no llega del catálogo, que se lee primero al arrancar.
- `purpose = customer_order` se ignora: no entra al almacén ni surte faltantes.

## 2. ¿Cómo manejamos los kits?

Un kit son las líneas con el mismo `group_name` (o `name` si es null). Inventariamos solo los
**componentes**: cada línea trae su `part_id` y la necesidad vive por (orden, `bom_line_id`), así que
cada uno se reserva, falta y se compra por separado. Llevar también el kit duplicaría existencias, y
renombrar un grupo no afecta el inventario. Una línea sin `part_id` queda como faltante sin identificar.

**`SERVICIO`** (cromado, rectificado): como cualquier pieza; genera faltante y sugerencia de compra.

## 3. ¿Cómo evitamos reservas dobles?

- Toda escritura (Kafka o API) toma un mismo candado (`pg_advisory_xact_lock`) y dentro recalcula
  `available = on_hand − reserved`; reserva `min(disponible, necesidad)` y el resto es faltante.
- Una segunda aprobación de la misma línea **reemplaza** la necesidad, no la suma.
- Solo puede correr un agente por base (`pg_try_advisory_lock` al arrancar); un segundo se detiene.
- `POST /issues` y `/transfers` responden 409 si no alcanza el saldo: nunca hay negativos.

## 4. ¿Cómo garantizamos idempotencia?

- Cada evento guarda su `event_id` en `processed_events` **en la misma transacción** que sus
  efectos: o se guarda todo o nada. Si ya está, se ignora. Además, un índice único impide dos
  entradas de la misma línea de compra.
- Las salidas van a `outbox` en esa transacción y otro hilo las publica: un reenvío no genera
  salidas nuevas. Lo que llega antes de tiempo espera en `pending_events` (en orden por llave); lo
  que falla 3 veces va a `inventory.dlq`.
- **Reconstrucción exacta:** todo lo aplicado (eventos y operaciones de piso) se anota en orden en
  `inventory.bitacora`; si se borra la base, el agente la recrea idéntica repitiéndola.
