"""시점을 걸어 두는 도구 셋.

몸통에 이름을 붙여 둔다(`put`/`done`/`listing`) — 자기 디스패치를 쓰는
에이전트도 **같은 몸통을** 부를 수 있어야 구현이 한 벌로 남는다.

메모와 다르다 — 메모는 다음 깨어남에 한 번 뜨고 지나가지만, 이건 그 시점
전에는 뜨지 않다가 때가 되면 올라온다.
"""

from __future__ import annotations

from .. import clock
from ..tools import Tool


def put(ctx, text: str, when: str, yearly: bool = False) -> dict:
    r = ctx.reminders.set(text, when, yearly)
    return {"id": r.id, "text": r.text, "due": clock.stamp(r.due), "yearly": r.yearly}


def done(ctx, id: str) -> dict:
    r = ctx.reminders.done(id)
    if r is None:
        return {"id": id, "결과": "그런 약속이 없다"}
    return {"id": r.id, "다음": clock.stamp(r.due) if r.yearly else "끝남"}


def listing(ctx) -> dict:
    return {
        "reminders": [
            {
                "id": r.id,
                "text": r.text,
                "when": clock.stamp(r.due),
                "남은 시간": clock.ago(r.due),
                "yearly": r.yearly,
            }
            for r in ctx.reminders.all()
            if r.open
        ]
    }


TOOLS = (
    Tool(
        name="reminder_set",
        description=(
            "정해진 시점이 되면 다시 떠오르게 남긴다. 메모(memory_note)와 다르다 — "
            "메모는 다음 깨어남에 한 번 뜨고 지나가지만, 이건 그 시점 전에는 뜨지 않다가 "
            "때가 되면 올라온다. "
            "'내일 아침에 다시 물어보자' 같은 약속, 기념일처럼 날짜가 있는 것에 쓴다. "
            "yearly=true 면 매년 같은 날 돌아온다."
        ),
        run=put,
        params={
            "text": {"type": "string", "description": "그때 무엇을 할 건지"},
            "when": {"type": "string", "description": "2026-08-24 또는 2026-08-24 09:00"},
            "yearly": {"type": "boolean", "default": False},
        },
        required=("text", "when"),
        needs=("reminders",),
    ),
    Tool(
        name="reminder_done",
        description=(
            "약속을 처리했다고 표시한다. 한 번짜리는 사라지고, 기념일은 내년으로 넘어간다."
        ),
        run=done,
        params={"id": {"type": "string"}},
        required=("id",),
        needs=("reminders",),
    ),
    Tool(
        name="reminder_list",
        description="남겨둔 약속과 기념일을 본다 (아직 시점이 안 된 것 포함).",
        run=listing,
        needs=("reminders",),
    ),
)
