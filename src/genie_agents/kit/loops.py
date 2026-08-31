"""열린 고리를 여닫는 도구 다섯.

리마인더도 메모도 아니다 — `loops.py` 첫머리가 셋을 갈라 둔 그대로다.
시점이 있으면 `reminder_*`, 알아 둘 것이면 `note_*`, **상태가 있으면 여기다.**

━━ 왜 다섯인가 ━━

상태는 넷인데(`열림·기다림·닫힘·접음`) 도구는 다섯이다. **접기는 도구를 안 낸다** —
`loop_move(state="접음")` 으로 간다. 상태를 안 늘리기로 한 자리에서 도구만 늘리면
같은 말을 두 군데서 해야 하고, 그러면 둘이 갈린다.

`loop_close` 는 그래서 예외로 보이지만 아니다. **닫기는 이 물건이 하려는 일 자체**라
(`docs/followup.md` — *닫힌 고리를 모아 회고 한 장*) 한 번에 부를 수 있어야 한다.

━━ 막힌 것을 예외로 안 던진다 ━━

★ 없는 id, 모르는 상태 — 둘 다 실제로 온다. 모델은 옛 목록을 보고 지워진 id 를
  부르고, 상태 이름을 지어낸다. **예외로 던지면 그 턴이 통째로 날아간다.**
  답으로 돌려주면 모델이 목록을 다시 보고 이어 간다.

━━ 목록이 무엇을 같이 내는가 ━━

★ `id` 와 `source` 를 항상 같이 낸다. `id` 가 없으면 모델이 **닫을 것을 지목할 수
  없고**(`docs/wiring.md` 겹 2 — *그 방에서 열려 있는 고리 목록 (id 포함)*),
  `source` 가 없으면 저녁 목록의 한 줄을 사람이 눌러 볼 수 없다.
"""

from __future__ import annotations

from .. import clock
from ..loops import DONE, DROPPED, ME, OPEN, STATES, WAITING
from ..tools import Tool


def _row(lp) -> dict:
    row = {
        "id": lp.id,
        "text": lp.text,
        "owner": lp.owner,
        "state": lp.state,
        "source": lp.source,          # 사람이 눌러 볼 자리
        "조용한 지": clock.ago(lp.moved_at),
        "sure": lp.sure,              # False 면 저녁 목록에서 사람이 고칠 것
    }
    # 기한 없는 고리가 대부분이다. 빈 칸을 매 줄에 실어 보내지 않는다.
    if lp.due:
        row["due"] = lp.due
    return row


def _없다(lid: str) -> dict:
    return {"id": lid, "결과": "그런 고리가 없다"}


def open_(ctx, text: str, source: str, owner: str = ME, due: str = "",
          sure: bool = True, note: str = "") -> dict:
    before = ctx.loops.by_source(source)
    lp = ctx.loops.open(text, source=source, owner=owner, due=due, sure=sure, note=note)
    out = _row(lp)
    if before is not None:
        # 새로 연 것과 이미 있던 것을 가른다. 안 가르면 모델이 같은 말을 계속
        # 다시 열려 들고, 매번 성공했다고 읽는다.
        out["결과"] = "이미 있던 고리다"
    return out


def move(ctx, id: str, note: str, state: str = "", owner: str = "") -> dict:
    if state and state not in STATES:
        return {
            "id": id,
            "결과": f"모르는 상태다: {state}. 쓸 수 있는 것은 {', '.join(STATES)}",
        }
    lp = ctx.loops.move(id, note, state=state, owner=owner)
    return _row(lp) if lp is not None else _없다(id)


def close(ctx, id: str, note: str = "닫힘") -> dict:
    lp = ctx.loops.close(id, note)
    return _row(lp) if lp is not None else _없다(id)


def confirm(ctx, id: str) -> dict:
    lp = ctx.loops.confirm(id)
    return _row(lp) if lp is not None else _없다(id)


def listing(ctx, owner: str = "", quiet_days: float = 0.0,
            sure_only: bool = False) -> dict:
    got = (
        ctx.loops.quiet(quiet_days, sure_only=sure_only)
        if quiet_days
        else ctx.loops.live(sure_only=sure_only)
    )
    if owner:
        got = [x for x in got if x.owner == owner]
    return {
        "loops": [_row(x) for x in got],
        # 빈 목록만 주면 "아직 아무것도 안 열었다" 와 "다 닫았다" 가 구분이 안 된다.
        "전체": len(ctx.loops),
    }


TOOLS = (
    Tool(
        name="loop_open",
        description=(
            "아직 안 끝난 것 하나를 원장에 연다. 약속·부탁·기다리는 것에 쓴다. "
            "**근거(source)를 반드시 같이 든다** — 어느 말, 어느 PR 에서 나왔는지. "
            "근거가 없으면 나중에 확인할 길이 없다. "
            f"내 차례면 owner 를 '{ME}', 남에게 넘어갔으면 그 사람 이름으로. "
            "말 속에 대상이 없어 자리로 추측한 것이면 sure=false 로 연다 — "
            "그러면 먼저 말을 걸지 않고 하루 한 번 훑는 목록에만 올린다."
        ),
        run=open_,
        params={
            "text": {"type": "string", "description": "무엇이 안 끝났나. 한 줄"},
            "source": {
                "type": "string",
                "description": "근거. slack:<채널>:<ts> · mail:<계정>:<스레드> · gh:<저장소>#<번호>",
            },
            "owner": {"type": "string", "description": f"누구 차례인가. 기본 '{ME}'",
                      "default": ME},
            "due": {"type": "string", "description": "기한이 있으면 2026-09-01. 없는 것이 대부분이다"},
            "sure": {"type": "boolean", "default": True,
                     "description": "명시적이면 true, 자리로 추측했으면 false"},
            "note": {"type": "string", "description": "왜 이렇게 봤는지 한 줄"},
        },
        required=("text", "source"),
        needs=("loops",),
    ),
    Tool(
        name="loop_move",
        description=(
            "고리가 움직였다고 적는다. 상태나 차례가 바뀌면 같이 준다. "
            f"'{WAITING}' 은 남이 할 차례라 내가 할 건 없다는 뜻이고, "
            f"'{DROPPED}' 은 안 하기로 했다는 뜻이다 — 닫은 것과 다르다. 성과가 아니다. "
            "**찌른 것도 여기 적는다.** 안 적으면 조용한 날짜만 보고 매일 같은 것을 다시 찌른다."
        ),
        run=move,
        params={
            "id": {"type": "string"},
            "note": {"type": "string", "description": "무슨 일이 있었나"},
            "state": {"type": "string", "enum": list(STATES),
                      "description": f"바뀌었으면. {', '.join(STATES)}"},
            "owner": {"type": "string", "description": "차례가 넘어갔으면 그 사람"},
        },
        required=("id", "note"),
        needs=("loops",),
    ),
    Tool(
        name="loop_close",
        description=(
            "끝났다고 표시한다. **지우지 않는다** — 닫힌 고리가 곧 한 일이고, "
            "회고는 이걸 모아서 쓴다. "
            f"안 하기로 한 것은 닫는 게 아니라 loop_move 로 '{DROPPED}' 이다."
        ),
        run=close,
        params={
            "id": {"type": "string"},
            "note": {"type": "string", "description": "무엇을 보고 닫았나", "default": "닫힘"},
        },
        required=("id",),
        needs=("loops",),
    ),
    Tool(
        name="loop_list",
        description=(
            "아직 안 끝난 고리를 본다. 오래 조용한 것이 앞에 온다 — 그게 먼저 봐야 할 것이다. "
            "owner 로 누구 차례인지, quiet_days 로 멈춘 것만, "
            "sure_only 로 확실한 것만 좁혀 볼 수 있다. "
            "**먼저 말을 걸 때는 sure_only=true 로 좁히고**, 하루 한 번 훑을 때는 넓게 본다."
        ),
        run=listing,
        params={
            "owner": {"type": "string", "description": f"이 사람 차례인 것만. '{ME}' 도 된다"},
            "quiet_days": {"type": "number",
                           "description": "이만큼 조용한 것만. 멈춘 것을 찾을 때"},
            "sure_only": {"type": "boolean", "default": False,
                          "description": "추측으로 연 것을 뺀다"},
        },
        needs=("loops",),
    ),
    Tool(
        name="loop_confirm",
        description=(
            "추측으로 열었던 고리를 사람이 맞다고 했다. 그때부터는 먼저 말을 걸어도 된다. "
            "사람이 '어 그거 맞아' 라고 한 자리에서 부른다."
        ),
        run=confirm,
        params={"id": {"type": "string"}},
        required=("id",),
        needs=("loops",),
    ),
)

__all__ = ["TOOLS", "OPEN", "WAITING", "DONE", "DROPPED"]
