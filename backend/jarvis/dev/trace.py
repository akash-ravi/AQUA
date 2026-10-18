"""Print a task's notebook as a readable story: python -m jarvis.dev.trace [task_id|last]"""
import argparse
import sys
from pathlib import Path

from jarvis.config import Settings
from jarvis.task import Notebook


def find_notebook(data_dir: Path, which: str) -> Path | None:
    folder = Path(data_dir) / "tasks"
    if which != "last":
        path = folder / f"{which}.jsonl"
        return path if path.exists() else None
    files = sorted(folder.glob("*.jsonl"), key=lambda path: path.stat().st_mtime)
    return files[-1] if files else None


def story(events: list[dict]) -> list[str]:
    lines = []
    for event in events:
        kind, at = event["event"], event["at"][11:19]
        if kind == "task":
            lines.append(f"{at}  TASK  {event.get('goal')}   (AI: {event.get('ai') or '?'})")
        elif kind == "decision":
            args = {k: v for k, v in event.get("args", {}).items() if k not in ("why", "remember")}
            lines.append(f"{at}  {event.get('n'):>3}. AI chose {event['tool']} {args}   [{event.get('ms', 0)} ms]")
        elif kind == "bad_reply":
            lines.append(f"{at}       unusable AI reply: {event.get('reason')}")
        elif kind == "step_sending":
            confirmed = "  (approved)" if event.get("confirmed") else ""
            lines.append(f"{at}       → phone: {event.get('action')} {event.get('label', '')}{confirmed}")
        elif kind == "step_result":
            extra = f" ({event['error']})" if event.get("error") else ""
            lines.append(f"{at}       ← phone: {event.get('status')}{extra}")
        elif kind == "late_result":
            lines.append(f"{at}       ← phone (late): {event.get('status')}")
        elif kind == "ask":
            lines.append(f"{at}  ASK ({event.get('kind')}): {event.get('text', '').replace(chr(10), ' / ')}")
        elif kind == "answer":
            lines.append(f"{at}  ANSWER: {event.get('status')} {event.get('text') or ''}".rstrip())
        elif kind == "update":
            lines.append(f"{at}  CHANGE: {event.get('text')}")
        elif kind == "picture":
            lines.append(f"{at}       picture taken ({event.get('bytes')} bytes)")
        elif kind == "status":
            lines.append(f"{at}  STATUS: {event.get('to')} ({event.get('reason', '')})")
        elif kind == "done":
            proof = " | ".join(event.get("proof") or [])
            lines.append(f"{at}  DONE: {event.get('outcome')} — {event.get('summary')}")
            if proof:
                lines.append(f"          proof on screen: {proof}")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description="Show what happened in a task.")
    parser.add_argument("task", nargs="?", default="last", help="task id, or 'last'")
    args = parser.parse_args()
    path = find_notebook(Settings().data_dir, args.task)
    if path is None:
        sys.exit("No task notebook found.")
    print(f"{path.name}\n")
    print("\n".join(story(Notebook(path).read())))


if __name__ == "__main__":
    main()
