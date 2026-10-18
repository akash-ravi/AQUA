from dataclasses import dataclass


@dataclass
class Attempt:
    count: int = 0
    status: str = ""
    duplicate_sensitive: bool = False
    progressed: bool = False
    user_verified: bool = False
    retry_authorized: bool = False
    recovery_requested: bool = False


def yes_no(text: str) -> bool | None:
    reply = text.casefold().strip(" .,!?")
    if reply in {"yes", "y", "yeah", "yep", "done", "it is done", "it worked", "already done", "completed"}:
        return True
    if reply in {"no", "n", "nope", "not done", "it didn't work", "it did not work", "not yet"}:
        return False
    return None
