import argparse
import logging

import uvicorn

from src.ai.model import AI
from src.config import Settings
from src.server.api import create_app

log = logging.getLogger("jarvis")


def main() -> None:
    parser = argparse.ArgumentParser(prog="jarvis")
    parser.add_argument("--debug", action="store_true", help="log every command, AI call and turn with its latency")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s.%(msecs)03d %(message)s", datefmt="%H:%M:%S")
    if args.debug:
        log.setLevel(logging.DEBUG)  # only jarvis.*; uvicorn and the SDK stay at info
    settings = Settings()
    ai = AI(settings)
    log.info("AI: %s · effort %s · Jev %s · ElevenLabs voice %s", ai.name, settings.effort,
             "on" if ai.jev else "off", "on" if settings.elevenlabs_api_key else "off (set ELEVENLABS_API_KEY)")
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
