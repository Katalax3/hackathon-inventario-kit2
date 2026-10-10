"""Configuración. Se puede cambiar con variables de entorno."""

import os

BROKER = os.environ.get("BROKER", "localhost:19092")
BASE_DATOS = os.environ.get("BASE_DATOS", "postgresql://postgres:its@localhost:5432/inventario_agente")
PUERTO = int(os.environ.get("PUERTO", "8000"))
PUERTO_HTTPS = int(os.environ.get("PUERTO_HTTPS", "8443"))

# Toda compra entra a la ubicación de recepción U-100 (regla 11 del contrato).
UBICACION_RECEPCION = 100
