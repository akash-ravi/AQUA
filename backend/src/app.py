import argparse
import logging
import sys

import uvicorn

from src.agent.brain import brain_runner
from src.agent.task import mark_interrupted_after_restart
from src.ai.claude import ClaudeAI
from src.config import Settings
from src.server.api import RunTask, create_app
from src.server.smoke import run_smoke


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Jarvis backend.")
    parser.add_argument("--smoke", action="store_true", help="test the phone connection without an AI")
    args = parser.parse_args()
    sys.stdout.reconfigure(line_buffering=True)  # show prints at once, even when output is piped
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    settings = Settings()
    routine: RunTask = run_smoke
    if args.smoke:
        print("Smoke test mode: no AI is used.")
    else:
        for task_id in mark_interrupted_after_restart(settings.data_dir):
            print(f"Task {task_id} was cut off by a restart; its last unanswered step is marked unknown.")
        ai = ClaudeAI(settings)
        routine = brain_runner(ai)
        print(f"AI: {ai.name} · effort {settings.effort}")
    uvicorn.run(
        create_app(settings, routine),
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
