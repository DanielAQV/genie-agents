"""남기고 찾는 도구 둘.

찾기가 있어야 남기기가 뜻을 가진다. 남기기만 켜면 쌓이기만 하고 아무도
안 읽는 파일이 된다.
"""

from __future__ import annotations

from .. import clock
from ..tools import Tool


def _write(ctx, text: str, tags: list[str] | None = None) -> dict:
    n = ctx.notes.write(text, tags)
    return {"id": n.id, "남겼다": n.text, "tags": n.tags, "when": clock.stamp(n.ts)}


def _recall(ctx, query: str = "", limit: int = 5, tag: str = "") -> dict:
    got = ctx.notes.recall(query, limit=limit, tag=tag)
    return {
        "notes": [
            {"id": n.id, "text": n.text, "tags": n.tags, "when": clock.ago(n.ts)}
            for n in got
        ],
        # 없을 때 빈 목록만 주면 "안 남겼다" 와 "못 찾았다" 가 구분이 안 된다.
        "전체": len(ctx.notes),
    }


TOOLS = (
    Tool(
        name="note_write",
        description=(
            "나중에 다시 보려고 한 줄 남긴다. 지금 하는 말이 아니라 **남기는 말**이다 — "
            "상대에게 안 간다. 알아 둘 것, 되짚고 싶은 것, 다음에 확인할 것에 쓴다."
        ),
        run=_write,
        params={
            "text": {"type": "string", "description": "남길 말"},
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "나중에 묶어 찾을 이름들",
            },
        },
        required=("text",),
        needs=("notes",),
    ),
    Tool(
        name="note_recall",
        description=(
            "남겨 둔 것을 찾는다. 글자로 맞춘다 — 뜻이 비슷한 것을 찾아주지는 않는다. "
            "질의를 비우면 최근 것부터 나온다."
        ),
        run=_recall,
        params={
            "query": {"type": "string", "description": "찾을 말. 비우면 최근 것"},
            "limit": {"type": "integer", "default": 5},
            "tag": {"type": "string", "description": "이 이름이 붙은 것만"},
        },
        needs=("notes",),
    ),
)
