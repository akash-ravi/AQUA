from dataclasses import dataclass


@dataclass(frozen=True)
class Param:
    name: str
    type: str  # string | integer | boolean | array
    description: str
    required: bool = True
    choices: tuple[str, ...] = ()


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    params: tuple[Param, ...]


WHY = Param("why", "string", "What you are doing now, shown as text. One short sentence under 8 words.")
TARGET = Param("target", "integer", "Number of the item on the current screen, like 12 for [12].")
TARGET_TEXT = Param("target_text", "string", "That item's label as shown on the screen.")
REMEMBER = Param("remember", "string", "A fact to keep for later steps.", required=False)

TOOLS: list[Tool] = [
    Tool("tap", "Tap one item on the current screen.", (WHY, TARGET, TARGET_TEXT, REMEMBER)),
    Tool("type_text", "Replace the text in a box. submit=true runs a search.",
         (WHY, TARGET, TARGET_TEXT, Param("text", "string", "The full text for the box."),
          Param("submit", "boolean", "Press Enter after typing, to run a search.", required=False), REMEMBER)),
    Tool("scroll", "Scroll a list. down shows what is below. No target means the main list.",
         (WHY, Param("direction", "string", "Which way to move the content into view.",
                     choices=("down", "up", "left", "right")),
          Param("target", "integer", "Number of the list to scroll.", required=False), REMEMBER)),
    Tool("go_back", "Press the phone's Back button.", (WHY, REMEMBER)),
    Tool("go_home", "Go to the phone's home screen.", (WHY,)),
    Tool("open_app", "Open an app on the phone by its name.",
         (WHY, Param("name", "string", "The app's name, like DoorDash."), REMEMBER)),
    Tool("wait", "Wait for the screen to finish loading, then look again.",
         (WHY, Param("seconds", "integer", "How long to wait, 1 to 10 seconds.", required=False))),
    Tool("look", "Get a picture of the screen when the text is not enough.", (WHY,)),
    Tool("ask_user", "Ask the user a question and wait for the answer.",
         (Param("question", "string", "The question, spoken aloud. A few casual words, like \"Which size?\""),
          Param("choices", "array", "Up to 6 short answers the user can pick.", required=False), REMEMBER)),
    Tool("confirm_and_tap", "Ask approval only for an action that costs the user money, then recheck and tap.",
         (WHY, TARGET, TARGET_TEXT,
          Param("summary", "string", "Charge amount/currency, payee or order, and recurring terms shown on screen."))),
    Tool("request_secret", "Ask the user to speak a secret for this field or enter it directly on the phone. Never generate the value.",
         (TARGET, TARGET_TEXT)),
    Tool("finish", "End the task when the current screen proves it is done.",
         (Param("summary", "string", "What was done, spoken aloud. A few casual words, like \"Done, pizza's on the way.\""),
          Param("proof", "array", "1 to 3 short phrases copied exactly from the current screen."))),
    Tool("give_up", "Say you're stuck or the task can't be done. The user is asked what to do next.",
         (Param("reason", "string", "Why you're stuck, spoken aloud. A few casual words."),)),
]

def _schema(param: Param) -> dict:
    if param.type == "array":
        schema = {"type": "array", "items": {"type": "string"}}
    else:
        schema = {"type": param.type}
    if param.choices:
        schema["enum"] = list(param.choices)
    return schema


def tool_schemas() -> list[dict]:
    def prop(param: Param) -> dict:
        if param.required:
            return {**_schema(param), "description": param.description}
        return {"anyOf": [_schema(param), {"type": "null"}], "description": param.description}

    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": {
                "type": "object",
                "properties": {p.name: prop(p) for p in tool.params},
                "required": [p.name for p in tool.params],
                "additionalProperties": False,
            },
            "strict": True,
        }
        for tool in TOOLS
    ]
