import re
from dataclasses import dataclass

YES_REPLIES = {"yes", "y", "yeah", "yep", "done", "it is done", "it's done", "it worked", "already done", "completed",
               "finished", "it finished", "it did"}
NO_REPLIES = {"no", "n", "nope", "not done", "it didn't work", "it did not work", "not yet"}


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
    # Only the opening clause counts: "It finished, but ..." is a yes.
    reply = re.split(r"[,.;!?]|\bbut\b", text.casefold(), maxsplit=1)[0].strip()
    if reply in YES_REPLIES:
        return True
    if reply in NO_REPLIES:
        return False
    return None
