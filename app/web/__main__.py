"""Punto de entrada CLI para ejecutar el servidor Web Flask del Trading Dashboard."""

import argparse
import logging
import sys
from app.web.app import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("trading_web")


def main() -> int:
    parser = argparse.ArgumentParser(description="Servidor Web del Multi-Bot Trading Dashboard")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Dirección IP de escucha (def: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=5000, help="Puerto de escucha (def: 5000)")
    parser.add_argument("--debug", action="store_true", help="Activar modo depuración de Flask")

    args = parser.parse_args()

    app = create_app()
    logger.info("Iniciando Dashboard Web en http://%s:%d ...", args.host, args.port)

    try:
        app.run(host=args.host, port=args.port, debug=args.debug)
        return 0
    except Exception as exc:
        logger.error("Fallo ejecutando servidor web: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())

