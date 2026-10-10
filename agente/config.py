"""Configuración. Se lee de agente/.env (copia .env.example y llénalo en cada computadora).
Una variable de entorno con el mismo nombre tiene prioridad sobre el archivo."""

import os
from pathlib import Path

from psycopg.conninfo import make_conninfo


def leer_env(ruta):
    """Lee líneas NOMBRE=valor; ignora las vacías y las que empiezan con #."""
    valores = {}
    if ruta.exists():
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("#") and "=" in linea:
                nombre, valor = linea.split("=", 1)
                valores[nombre.strip()] = valor.strip().strip('"').strip("'")
    return valores


ARCHIVO = leer_env(Path(__file__).parent / ".env")


def valor(nombre, por_defecto):
    return os.environ.get(nombre) or ARCHIVO.get(nombre) or por_defecto


BROKER = valor("BROKER", "localhost:19092")
PUERTO = int(valor("PUERTO", "8000"))
PUERTO_HTTPS = int(valor("PUERTO_HTTPS", "8443"))

# Conexión a PostgreSQL. Si DB_CONTRASENA queda vacía, PostgreSQL la busca en ~/.pgpass o PGPASSWORD.
# BASE_DATOS, si se define, reemplaza a todas las DB_* con una URL completa.
BASE_DATOS = valor("BASE_DATOS", None) or make_conninfo(
    "",
    host=valor("DB_HOST", "localhost"),
    port=valor("DB_PUERTO", "5432"),
    user=valor("DB_USUARIO", "postgres"),
    password=valor("DB_CONTRASENA", None),
    dbname=valor("DB_NOMBRE", "inventario_agente"),
)

# Toda compra entra a la ubicación de recepción U-100 (regla 11 del contrato).
UBICACION_RECEPCION = 100

# Las órdenes abiertas desde el panel del simulador (las de la demo) se numeran desde 90001
# (ver simulator/simulator.py). Las pruebas de contrato usan IDs de 9 dígitos.
ORDENES_DEMO = (90001, 99_999_999)
