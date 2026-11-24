"""LLM tool definitions (architecture 4.4), in OpenAI function-calling format.

Handlers call Session methods. The LLM may request a transition; the orchestrator validates it.
"""
_EMPTY = {"type": "object", "properties": {}, "additionalProperties": False}


def _tool(name: str, description: str, parameters: dict | None = None) -> dict:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": parameters or _EMPTY}}


TOOLS = [
    _tool("next_question", "Move to the next question card."),
    _tool("mark_answer_complete", "Candidate finished answering."),
    _tool("start_coding", "Begin the coding stage (pauses voice)."),
    _tool("end_interview", "Request wrap-up. May be refused."),
    _tool(
        "note_claimed_skill",
        "Call when the candidate says they are good or strong at a skill. Only record what they claim; do not test it.",
        {
            "type": "object",
            "properties": {"skill": {"type": "string", "enum": ["sql"]}},
            "required": ["skill"],
            "additionalProperties": False,
        },
    ),
]
