"""Conexión a PostgreSQL."""

from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

import config

pool = ConnectionPool(config.BASE_DATOS, kwargs={"row_factory": dict_row}, min_size=1, max_size=10, open=False)

TABLAS = ["movements", "reservations", "shortages", "needs", "inspections", "work_orders", "bom_lines",
          "models", "stock_policies", "reorder_suggestions", "unmatched_receipts", "parts", "locations",
          "outbox", "pending_events", "processed_events"]


@contextmanager
def conexion():
    """Para leer. Al salir se guarda (commit); si hubo error se deshace (rollback)."""
    with pool.connection() as conn:
        with conn.cursor() as cur:
            yield cur


@contextmanager
def transaccion():
    """Para escribir. El candado hace que solo una escritura ocurra a la vez en todo el
    servicio, así dos aprobaciones nunca pueden reservar la misma última pieza."""
    with conexion() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(1)")
        yield cur


def abrir(reconstruir=False):
    pool.open(wait=True)
    with transaccion() as cur:
        if reconstruir:
            # TRUNCATE no dispara el candado de "movements es inmutable", que es por renglón.
            cur.execute(f"TRUNCATE {', '.join(TABLAS)} RESTART IDENTITY CASCADE")
        # Las pruebas crean ubicaciones con códigos repetidos (otra "U-101", por ejemplo).
        # Se permite, y al escanear un código gana la ubicación con el ID más bajo.
        cur.execute("ALTER TABLE locations DROP CONSTRAINT IF EXISTS locations_code_key")
        cur.execute("""INSERT INTO locations (location_id, code, name) VALUES (%s, 'U-100', 'Recepción de material')
                       ON CONFLICT DO NOTHING""", (config.UBICACION_RECEPCION,))
