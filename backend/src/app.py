import logging

import uvicorn

from src.ai.model import AI
from src.config import Settings
from src.server.api import create_app

log = logging.getLogger("jarvis")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    settings = Settings()
    ai = AI(settings)
    log.info("AI: %s · effort %s", ai.name, settings.effort)
    uvicorn.run(
        create_app(settings, ai),
        host="0.0.0.0",
        port=settings.port,
        ws_max_size=16 * 1024 * 1024,
        ws_ping_interval=20,
        access_log=False,
        log_level="info",
        log_config=None,
    )


if __name__ == "__main__":
    main()
