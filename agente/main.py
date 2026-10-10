"""Arranca el servicio de inventario: consumidor de Kafka + API + pantallas.

    uv run --python 3.12 --with-requirements agente/requirements.txt agente/main.py
    ... agente/main.py --reconstruir     # borra los datos y los reconstruye desde la bitácora y los tópicos
"""

import argparse
import threading
from pathlib import Path

import uvicorn

import base_datos
import config
import consumidor
from api import app

AQUI = Path(__file__).parent
CERTIFICADO, LLAVE = AQUI / "certificado.pem", AQUI / "llave.pem"


def main():
    parser = argparse.ArgumentParser(description="Servicio de inventario del taller")
    parser.add_argument("--reconstruir", action="store_true", help="borra los datos y los reconstruye desde la bitácora y los tópicos")
    args = parser.parse_args()

    base_datos.crear_si_falta()
    base_datos.abrir(reconstruir=args.reconstruir)
    # Si la base quedó vacía (se borró o se pidió --reconstruir), el consumidor repite primero la bitácora.
    consumidor.preparar_bitacora()
    threading.Thread(target=consumidor.sin_detenerse, args=(consumidor.consumir,), daemon=True).start()
    threading.Thread(target=consumidor.sin_detenerse, args=(consumidor.publicar_salidas,), daemon=True).start()

    # El celular solo deja usar la cámara en HTTPS: si hay certificado, también se sirve por 8443.
    if CERTIFICADO.exists() and LLAVE.exists():
        https = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=config.PUERTO_HTTPS, log_level="warning",
                                              ssl_certfile=CERTIFICADO, ssl_keyfile=LLAVE))
        threading.Thread(target=https.run, daemon=True).start()
        print(f"pantallas para celular en https://<ip-de-esta-máquina>:{config.PUERTO_HTTPS}", flush=True)

    print(f"API y pantallas en http://localhost:{config.PUERTO}", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=config.PUERTO, log_level="warning")


if __name__ == "__main__":
    main()
