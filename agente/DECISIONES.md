# Documento de decisiones

Python (FastAPI + confluent-kafka) sobre PostgreSQL. El stock es siempre la suma de `movements`,
que un trigger hace inmutable.

## 1. ¿Cómo relacionamos las compras con el catálogo?

- `part_number` se normaliza (sin espacios de más, en mayúsculas) y se compara con el `sku`
  normalizado de cada pieza. **Una coincidencia:** entrada en `U-100` y `stock.received`.
- **Ninguna, o varias** (piezas con el mismo `sku`): no se adivina. Va a `unmatched_receipts`,
  emite `stock.unmatched_receipt` y se resuelve a mano en Almacén, que ofrece las piezas candidatas.
- Antes de la revisión se esperan 3 s por si la pieza viene en camino (entre tópicos no hay orden);
  al arrancar se lee todo `shop.catalog` antes que lo demás.
- `purpose = customer_order` se ignora: no entra al almacén ni surte faltantes.

## 2. ¿Cómo manejamos los kits?

Se inventarían los **componentes**, no el kit. Cada línea de la lista de partes trae su `part_id` y
la necesidad vive por (orden, `bom_line_id`), así que cada componente se reserva, falta y se compra
por separado, sin llevar dos existencias (kit y piezas) que se contradigan. Una línea sin `part_id`
(por ejemplo, un kit sin clave) no se puede reservar: queda como faltante sin identificar.

**Familia `SERVICIO`** (cromado, rectificado): se trata como cualquier pieza. Llega con
`action = buy` y se compra por requisición, así que genera faltante y sugerencia de compra como una
refacción. Una sola regla, sin excepciones.

## 3. ¿Cómo evitamos reservas dobles?

- Toda escritura (Kafka o API) toma el mismo candado de PostgreSQL (`pg_advisory_xact_lock`) en su
  transacción: dos aprobaciones nunca calculan a la vez lo disponible.
- Dentro de la transacción se recalcula `available = on_hand − reserved` y se reserva
  `min(disponible, necesidad)`; el resto queda como faltante.
- Una segunda aprobación de la misma línea **reemplaza** la necesidad, no la suma.
- `POST /issues` y `POST /transfers` responden 409 si no alcanza el saldo; nunca dejan negativo.

## 4. ¿Cómo garantizamos idempotencia?

- Cada evento guarda su `event_id` en `processed_events` **en la misma transacción** que sus
  efectos: o se guarda todo o nada. Si ya está, se ignora.
- Las salidas van a `outbox` en esa transacción y otro hilo las publica: un reenvío no genera
  salidas nuevas. Lo que llega antes de tiempo espera en `pending_events` (en orden por llave); lo
  que falla 3 veces va a `inventory.dlq`.
- **Reconstrucción exacta:** todo lo aplicado (eventos y operaciones de piso con su fecha) se anota
  en orden en el tópico `inventory.bitacora`. Si se borra la base, el agente la crea y repite la
  bitácora: queda idéntica tabla por tabla.
