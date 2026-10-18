import logging

import httpx

log = logging.getLogger("jarvis.jev")

URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
TIMEOUT_SECONDS = 5
APPROVE_AT = 0.9

APPROVAL_QUESTION = {
    "type": "choice",
    "instructions": "The user was asked to approve an action that can't be undone, and answered by voice "
                    "(the reply may be misheard). What does the reply mean?",
    "criteria": {
        "approve": "Clearly says yes to exactly this action, with no changes or conditions",
        "decline": "Says no, stop or cancel",
        "change": "Asks for something different, adds a condition, or is unclear",
    },
}
PHONE_TASK_QUESTION = {
    "type": "noul",
    "instructions": "Is this a request to do something on a phone (open an app, message, call, search, buy, check)?",
    "criteria": {
        "true": "Names something to do, even if short or slightly misheard",
        "false": "A greeting, a filler word, a fragment of a sentence, or a question about the assistant",
    },
}


class Jev:
    """TypeSafe's System One model: fast typed answers for small decisions. Errors return the safe default."""

    def __init__(self, api_key: str):
        self.client = httpx.AsyncClient(headers={"Authorization": f"Bearer {api_key}"}, timeout=TIMEOUT_SECONDS)

    async def approves(self, question: str, reply: str) -> bool:
        answer = await self._ask(f"Question: {question}\nReply: {reply}", "approval", APPROVAL_QUESTION)
        if not answer or answer.get("choice") != "approve":
            return False
        return (answer.get("probabilities") or {}).get("approve", 0) >= APPROVE_AT

    async def phone_task_likelihood(self, goal: str) -> float:
        answer = await self._ask(goal, "phone_task", PHONE_TASK_QUESTION)
        noul = answer.get("noul") if answer else None
        return noul if isinstance(noul, (int, float)) else 1.0

    async def _ask(self, state: str, name: str, question: dict) -> dict | None:
        try:
            response = await self.client.post(URL, json={"state": state, "model": MODEL, "questions": {name: question}})
            response.raise_for_status()
            return response.json()["answers"][name]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            log.info("Jev %s failed: %s", name, error)
            return None
