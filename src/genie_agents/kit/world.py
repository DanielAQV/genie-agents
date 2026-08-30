"""바깥에서 들어온 것을 꺼내 보는 도구.

한 사람에게서만 소식을 받으면 그 에이전트의 세계는 그 사람뿐이다.
무엇을 물어 오는지는 `sources` 가 정하고, 여기서는 들어온 것을 볼 뿐이다.
"""

from __future__ import annotations

from .. import clock
from ..tools import Tool


def _recent(ctx, hours: float = 24, limit: int = 10) -> dict:
    return {
        "signals": [
            {
                "source": s.source,
                "title": s.title,
                "summary": s.summary,
                "url": s.url,
                # **상대 시각으로 준다.** 소식은 "언제 있었던 일인지" 보다
                # "지금 기준 얼마나 새것인지" 가 실제로 쓰이는 정보다.
                "when": clock.ago(s.ts),
            }
            for s in ctx.world.recent(hours=hours, limit=limit)
        ]
    }


TOOLS = (
    Tool(
        name="world_recent",
        description=(
            "바깥에서 들어온 소식을 본다. 브리핑에 이미 올라온 것도 여기서 다시 꺼낼 수 있다."
        ),
        run=_recent,
        params={
            "hours": {"type": "number", "default": 24, "description": "몇 시간 안의 것"},
            "limit": {"type": "integer", "default": 10},
        },
        needs=("world",),
    ),
)
