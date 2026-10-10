# Documento de decisiones

Servicio de inventario del taller: Python (FastAPI + confluent-kafka) sobre PostgreSQL. El stock
nunca se edita: siempre es la suma de la tabla `movements`, y un trigger impide modificar o
borrar movimientos.

## 1. ¿Cómo relacionamos las compras con el catálogo?

- `part_number` se normaliza (sin espacios al inicio ni al final, espacios internos repetidos
  reducidos a uno, todo en mayúsculas) y se compara contra el `sku` normalizado de cada pieza.
- **Una sola coincidencia:** entrada en `U-100` y `stock.received`.
- **Ninguna, o varias** (piezas distintas con el mismo `sku`): no se adivina. Va a
  `unmatched_receipts`, emite `stock.unmatched_receipt` y se resuelve a mano en la pantalla de
  Almacén (`POST /unmatched-receipts/{id}/resolve`). Si el sku era ambiguo, se ofrecen las
  piezas candidatas.
- Antes de mandarla a revisión manual se espera 3 s: la pieza puede venir en camino por
  `shop.catalog`, porque entre tópicos no hay orden. Al arrancar se lee todo `shop.catalog`
  antes que los demás tópicos (regla 22).
- `purpose = customer_order` se ignora: no entra al almacén ni surte faltantes.

## 2. ¿Cómo manejamos los kits?

Se inventarían los **componentes**, no el kit. Cada línea de la lista de partes trae su
`part_id`, y la necesidad vive por (orden, `bom_line_id`): cada componente se reserva, falta y
se compra por separado. Así no hay que llevar dos existencias (kit y piezas) que se
contradigan. Una línea sin `part_id` (por ejemplo, un kit sin clave) no se puede reservar: se
reporta como faltante sin identificar (`part_id: null`) hasta que se dé de alta la pieza.

## 3. ¿Cómo evitamos reservas dobles?

- Toda escritura, venga de Kafka o de la API, abre una transacción que toma el mismo candado de
  PostgreSQL (`pg_advisory_xact_lock(1)`). Dos aprobaciones nunca calculan a la vez lo disponible
  de la misma pieza.
- Dentro de la transacción se recalcula `available = on_hand − reserved` desde los movimientos y
  se reserva `min(disponible, necesidad)`. El resto queda como faltante.
- Una segunda aprobación de la misma línea **reemplaza** la necesidad (reserva o libera la
  diferencia), no la suma.
- `POST /issues` y `POST /transfers` responden 409 si no alcanza el saldo; nunca dejan negativo.

## 4. ¿Cómo garantizamos idempotencia?

- Cada evento aplicado guarda su `event_id` en `processed_events` **en la misma transacción** que
  sus efectos: o se guarda todo o nada. Si el `event_id` ya está, el evento se ignora.
- Los eventos de salida se escriben en la tabla `outbox` en esa misma transacción, y otro hilo
  los publica en `inventory.events`. Un reenvío no genera salidas nuevas.
- Además, un índice único impide registrar dos entradas de la misma línea de compra.
- Lo que llega antes de tiempo (inspección antes que su orden o su pieza) se guarda en
  `pending_events` y se reintenta en orden por llave. Lo que falla tras 3 intentos va a
  `inventory.dlq` sin detener el consumo.
- **Reconstrucción:** con `--reconstruir` se vacían las tablas y se releen los tópicos desde el
  inicio. Se llega a la misma existencia por pieza y ubicación (comprobado con
  `run_tests.py snapshot/compare`).
