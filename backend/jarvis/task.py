import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from jarvis.messages import new_id, new_rejoin_key

MAX_NOTES = 12
MAX_NOTE_CHARS = 150
MAX_SCREENS_SEEN = 8


@dataclass
class Step:
    n: int
    tool: str
    target: int | None = None
    label: str = ""
    why: str = ""
    status: str = ""
    change: str = ""
    error: str = ""
    irreversible: bool = False
    signature: str = ""

    @property
    def result(self) -> str:
        return self.change or self.error or self.status


@dataclass
class Task:
    goal: str
    device_id: str
    id: str = field(default_factory=lambda: new_id("t"))
    rejoin_key: str = field(default_factory=new_rejoin_key)
    revision: int = 0
    corrections: list[str] = field(default_factory=list)
    answers: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    done_for_good: list[str] = field(default_factory=list)
    unsure: list[str] = field(default_factory=list)
    screens_seen: list[str] = field(default_factory=list)
    typed_texts: list[str] = field(default_factory=list)
    model_calls: int = 0
    bad_in_row: int = 0
    bad_total: int = 0
    finish_misses: int = 0
    wait_seconds: float = 0.0
    work_seconds: float = 0.0
    status: str = "running"

    def add_note(self, text: str | None) -> None:
        text = (text or "").strip()[:MAX_NOTE_CHARS]
        if not text or text in self.notes:
            return
        self.notes.append(text)
        del self.notes[:-MAX_NOTES]

    def saw_screen(self, title: str) -> None:
        if title and (not self.screens_seen or self.screens_seen[-1] != title):
            self.screens_seen.append(title)
            del self.screens_seen[:-MAX_SCREENS_SEEN]

    def snapshot(self) -> dict:
        return asdict(self)

    @classmethod
    def from_snapshot(cls, data: dict) -> "Task":
        data = dict(data)
        data["steps"] = [Step(**step) for step in data.get("steps", [])]
        data["answers"] = [tuple(pair) for pair in data.get("answers", [])]
        known = cls.__dataclass_fields__
        return cls(**{key: value for key, value in data.items() if key in known})


class Notebook:
    """One JSON line per event, flushed to disk before anything is sent to the phone."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def for_task(cls, data_dir: Path, task_id: str) -> "Notebook":
        return cls(Path(data_dir) / "tasks" / f"{task_id}.jsonl")

    def write(self, event: str, **data) -> None:
        line = {"at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "event": event, **data}
        with open(self.path, "a", encoding="utf-8") as file:
            file.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
            file.flush()
            os.fsync(file.fileno())

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        events = []
        with open(self.path, encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except ValueError:
                        pass
        return events


def load_task(data_dir: Path, task_id: str) -> tuple[Task, list[dict]] | None:
    notebook = Notebook.for_task(data_dir, task_id)
    events = notebook.read()
    snapshot = next((e["task"] for e in reversed(events) if e["event"] == "state"), None)
    if snapshot is None:
        return None
    return Task.from_snapshot(snapshot), events


def unfinished_task_ids(data_dir: Path) -> list[str]:
    folder = Path(data_dir) / "tasks"
    if not folder.exists():
        return []
    ids = []
    for path in sorted(folder.glob("*.jsonl")):
        events = Notebook(path).read()
        if events and not any(e["event"] == "done" for e in events):
            ids.append(path.stem)
    return ids


def mark_interrupted_after_restart(data_dir: Path) -> list[str]:
    """Tasks that were running when the server stopped: unanswered commands become unknown."""
    marked = []
    for task_id in unfinished_task_ids(data_dir):
        notebook = Notebook.for_task(data_dir, task_id)
        events = notebook.read()
        if any(e["event"] == "status" and e.get("to") == "interrupted" and e.get("reason") == "server restart"
               for e in events[-3:]):
            continue
        answered = {e.get("command_id") for e in events if e["event"] in ("step_result", "late_result")}
        for event in events:
            if event["event"] == "step_sending" and event.get("command_id") not in answered:
                notebook.write("step_result", n=event.get("n"), command_id=event.get("command_id"),
                               status="unknown", error="no_result", change="server restarted before the phone answered")
        notebook.write("status", to="interrupted", reason="server restart")
        marked.append(task_id)
    return marked


TASK_ID = re.compile(r"^t-[0-9a-f]{16}$")


def is_task_id(text: str) -> bool:
    return bool(TASK_ID.match(text))


def task_summary(events: list[dict]) -> dict:
    """The short version of a task for a list in the app."""
    started = next((e for e in events if e["event"] == "task"), {})
    done = next((e for e in reversed(events) if e["event"] == "done"), None)
    snapshot = next((e["task"] for e in reversed(events) if e["event"] == "state"), {})
    last_status = next((e.get("to") for e in reversed(events) if e["event"] == "status"), None)
    if done:
        status = done.get("outcome")
    elif last_status in ("interrupted", "paused"):
        status = last_status
    else:
        status = "running"
    return {
        "task_id": started.get("task_id") or snapshot.get("id"),
        "goal": started.get("goal") or snapshot.get("goal", ""),
        "device_id": started.get("device_id") or snapshot.get("device_id", ""),
        "ai": started.get("ai", ""),
        "started_at": events[0]["at"] if events else None,
        "ended_at": done["at"] if done else None,
        "status": status,
        "summary": done.get("summary") if done else None,
        "steps": len(snapshot.get("steps", [])),
    }


def list_tasks(data_dir: Path, device_id: str | None = None, limit: int = 50) -> list[dict]:
    folder = Path(data_dir) / "tasks"
    if not folder.exists():
        return []
    paths = sorted(folder.glob("t-*.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)
    tasks = []
    for path in paths:
        events = Notebook(path).read()
        if not events:
            continue
        summary = task_summary(events)
        if device_id and summary["device_id"] != device_id:
            continue
        tasks.append(summary)
        if len(tasks) >= limit:
            break
    return tasks


def task_story(data_dir: Path, task_id: str) -> dict | None:
    """Everything the app needs to show one task: the steps, the questions and how it ended."""
    events = Notebook.for_task(data_dir, task_id).read()
    if not events:
        return None
    snapshot = next((e["task"] for e in reversed(events) if e["event"] == "state"), {})
    done = next((e for e in reversed(events) if e["event"] == "done"), None)
    questions = []
    for event in events:
        if event["event"] == "ask":
            questions.append({"at": event["at"], "kind": event.get("kind"), "text": event.get("text"),
                              "answer": None})
        elif event["event"] == "answer" and questions and questions[-1]["answer"] is None:
            questions[-1]["answer"] = event.get("text") or event.get("status")
    steps = [{"n": step["n"], "action": step["tool"], "label": step["label"], "why": step["why"],
              "status": step["status"], "result": step.get("change") or step.get("error") or step["status"]}
             for step in snapshot.get("steps", [])]
    return {
        **task_summary(events),
        "proof": done.get("proof") if done else None,
        "corrections": snapshot.get("corrections", []),
        "done_for_good": snapshot.get("done_for_good", []),
        "unsure": snapshot.get("unsure", []),
        "questions": questions,
        "steps": steps,
    }
