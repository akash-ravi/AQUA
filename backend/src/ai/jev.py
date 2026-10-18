import logging

import httpx

log = logging.getLogger("jarvis.jev")

URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
TIMEOUT_SECONDS = 5
APPROVE_AT = 0.9
# Noise drops input silently, so it needs to be clear; a wrong thanks only closes the orb.
SPEECH_SURE_AT = {"thanks": 0.6, "noise": 0.85}

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
COMPLETION_QUESTION = {
    "type": "choice",
    "instructions": "The user was asked whether a phone task finished, and answered by voice "
                    "(the reply may be misheard). What does the reply say about that task?",
    "criteria": {
        "done": "Says the task finished, even if they add a comment or a complaint",
        "not_done": "Says it did not finish or still needs doing",
        "unsure": "Doesn't know, or the reply is unclear",
    },
}
PAUSE_QUESTION = {
    "type": "choice",
    "instructions": "The user paused a phone task and was asked what else they want, and answered by voice "
                    "(the reply may be misheard). What does the reply mean for the paused task?",
    "criteria": {
        "resume": "Nothing else: carry on, continue, never mind, or no",
        "stop": "Stop, cancel or abandon the task",
        "thanks": "Ends the conversation with nothing more to ask: thanks, bye, all set, that's all, "
                  "go away, shut up, a rude dismissal, stop listening or shut down",
        "noise": "Background noise, TV, music or other people talking, not the user answering",
        "change": "A new instruction, a change to the task, or anything else",
    },
}
SPEECH_QUESTION = {
    "type": "choice",
    "instructions": "The phone's microphone picked this up while waiting for the user to talk to their voice "
                    "assistant (the text may be misheard). What is it?",
    "criteria": {
        "talking": "A request, question, reply or remark aimed at the assistant, even if short",
        "thanks": "Ends the conversation with no request: thanks, bye, all set, that's all, nothing else, "
                  "go away, shut up, a rude dismissal, stop listening or shut down",
        "noise": "Background noise, TV, music, other people's conversation, or random words",
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

    async def says_done(self, question: str, reply: str) -> bool | None:
        answer = await self._ask(f"Question: {question}\nReply: {reply}", "completion", COMPLETION_QUESTION)
        return {"done": True, "not_done": False}.get(answer.get("choice")) if answer else None

    async def pause_reply(self, question: str, reply: str) -> str | None:
        answer = await self._ask(f"Question: {question}\nReply: {reply}", "pause", PAUSE_QUESTION)
        return answer.get("choice") if answer else None

    async def speech_kind(self, said: str) -> str:
        """talking, thanks or noise; anything unsure counts as talking."""
        answer = await self._ask(said, "speech", SPEECH_QUESTION)
        if not answer:
            return "talking"
        choice = answer.get("choice")
        sure = (answer.get("probabilities") or {}).get(choice, 0) >= SPEECH_SURE_AT.get(choice, 0)
        return choice if choice in ("thanks", "noise") and sure else "talking"

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
