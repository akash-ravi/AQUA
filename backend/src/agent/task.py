import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

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
    duplicate_sensitive: bool = False
    action_key: str = ""
    signature: str = ""

    @property
    def result(self) -> str:
        return self.change or self.error or self.status


@dataclass
class Task:
    goal: str
    id: str
    earlier: list[str] = field(default_factory=list)
    corrections: list[str] = field(default_factory=list)
    answers: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    completed_irreversible: list[str] = field(default_factory=list)
    uncertain_actions: list[str] = field(default_factory=list)
    screens_seen: list[str] = field(default_factory=list)
    typed_texts: list[str] = field(default_factory=list)
    model_calls: int = 0
    consecutive_invalid_replies: int = 0
    invalid_replies: int = 0
    rejected_finishes: int = 0
    wait_seconds: float = 0.0
    completion_evidence: str | None = None

    def add_note(self, text: str | None) -> None:
        text = (text or "").strip()[:MAX_NOTE_CHARS]
        if not text or text in self.notes:
            return
        self.notes.append(text)
        del self.notes[:-MAX_NOTES]

    def record_screen(self, title: str) -> None:
        if title and (not self.screens_seen or self.screens_seen[-1] != title):
            self.screens_seen.append(title)
            del self.screens_seen[:-MAX_SCREENS_SEEN]

    def snapshot(self) -> dict:
        return asdict(self)


class Notebook:
    """One JSON line per event, flushed to disk before anything is sent to the phone."""

    def __init__(self, data_dir: Path, task_id: str):
        self.path = Path(data_dir) / "tasks" / f"{task_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: str, **data) -> None:
        line = {"at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "event": event, **data}
        with open(self.path, "a", encoding="utf-8") as file:
            file.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
            file.flush()
            os.fsync(file.fileno())
