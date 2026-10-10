"""Conexión a PostgreSQL."""

from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

import config

pool = ConnectionPool(config.BASE_DATOS, kwargs={"row_factory": dict_row}, min_size=1, max_size=10, open=False)

TABLAS = ["movements", "reservations", "shortages", "needs", "inspections", "work_orders", "bom_lines",
          "models", "stock_policies", "reorder_suggestions", "unmatched_receipts", "parts", "locations",
          "outbox", "pending_events", "processed_events", "bitacora"]

ESQUEMA = Path(__file__).parent / "esquema.sql"


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


def crear_si_falta():
    """Si la base no existe la crea, y si no tiene tablas les pone las de esquema.sql."""
    nombre = conninfo_to_dict(config.BASE_DATOS)["dbname"]
    with psycopg.connect(make_conninfo(config.BASE_DATOS, dbname="postgres"), autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (nombre,)).fetchone():
            print(f"la base {nombre} no existe: se crea", flush=True)
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(nombre)))
    with psycopg.connect(config.BASE_DATOS, autocommit=True) as conn:
        if conn.execute("SELECT to_regclass('public.parts')").fetchone()[0] is None:
            print("la base no tiene tablas: se crean desde esquema.sql", flush=True)
            conn.execute(ESQUEMA.read_text())


candado_unico = None


def asegurar_unico():
    """Solo un agente a la vez: dos sobre la misma base publicarían eventos repetidos en Kafka.
    El candado dura mientras el proceso tenga abierta esta conexión."""
    global candado_unico
    candado_unico = psycopg.connect(config.BASE_DATOS, autocommit=True)
    if not candado_unico.execute("SELECT pg_try_advisory_lock(2)").fetchone()[0]:
        raise SystemExit("Ya hay otro agente encendido con esta base. Apágalo antes: pkill -f agente/main.py")


def abrir(reconstruir=False):
    pool.open(wait=True)
    with transaccion() as cur:
        # Copia en orden de todo lo que se aplica; ver bitacora.py.
        cur.execute("""CREATE TABLE IF NOT EXISTS bitacora (
                         id bigserial PRIMARY KEY, registro jsonb NOT NULL, publicado boolean NOT NULL DEFAULT false)""")
        if reconstruir:
            # TRUNCATE no dispara el candado de "movements es inmutable", que es por renglón.
            cur.execute(f"TRUNCATE {', '.join(TABLAS)} RESTART IDENTITY CASCADE")
        # Las pruebas crean ubicaciones con códigos repetidos (otra "U-101", por ejemplo).
        # Se permite, y al escanear un código gana la ubicación con el ID más bajo.
        cur.execute("ALTER TABLE locations DROP CONSTRAINT IF EXISTS locations_code_key")
        cur.execute("""INSERT INTO locations (location_id, code, name) VALUES (%s, 'U-100', 'Recepción de material')
                       ON CONFLICT DO NOTHING""", (config.UBICACION_RECEPCION,))
