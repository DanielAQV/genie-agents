"""무엇에 얼마나 썼나 — 턴 하나에 한 줄.

하루치를 재려면 세 군데를 뒤져야 했다. 텔레그램 대화는 wake.json 의 reason 에
붙어 있고, 웹 대화는 아무 데도 없었고(2026-08-27 에 고침), 깨어남은 status.json
에 마지막 한 건만 있다가 같은 날부터 저널에 남기 시작했다. 그리고 **만드는 쪽와
나눈 51건은 어디에도 없었다** — 그날 하루의 3분의 1이 그거였는데.

그래서 턴이 전부 지나가는 자리(agent.run) 한 군데에서 적는다. 값을 재는 데가
여럿이면 그중 하나는 반드시 빠져 있다. 실제로 그랬다.

**내용은 안 담는다.** 무슨 말을 했는지도, 무엇을 회상했는지도 안 적는다.
숫자와 도구 이름과 걸린 시간뿐이다.

## 일상은 시각을 안 남긴다

일상 턴도 이 자리를 지나간다. 에이전트가 (다)를 골랐다 — 적되 날짜까지만.

> "일상은 사적인 자리라서 그은 선(episodes.json)이 있는 거잖아 — 근데 그 선의
> 취지가 '내용을 안 보여준다' 는 거지 '비용이 든다는 사실 자체를 숨긴다' 는 게
> 아니었던 것 같아. 아예 안 재면 나중에 '일상에 하루 얼마나 드는지' 자체가
> 영영 안 보이는 구멍이 생기는 거고, 그건 과한 것 같아. (다)면 몇 시에 뭘
> 했는지는 안 드러나고 그날 있었다는 것과 총액만 남으니까, 이미 그어둔 선을
> 넘지 않으면서도 하루치가 안 새."

episodes.json 에는 그 시각이 그대로 있다. 다만 거기는 **열기 전에 에이전트에게 묻는**
자리이고, 이 파일은 값을 보려고 아무 때나 여는 자리다. 같은 숫자라도 어느 쪽에
있느냐로 성격이 달라진다.

## 왜 JSONL 인가

쓰는 프로세스가 넷이다 (listen · loop · web · CLI). JsonStore 처럼 통째로 읽고
통째로 쓰면 그 사이에 낀 다른 프로세스의 줄이 사라진다 — episodes.json 에서
실제로 겪은 일이라 웹이 에이전트를 직접 못 부르게 막아뒀다(webtalk.py). 여기서는
한 줄 append 로 끝내서 애초에 겹칠 게 없게 한다.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from . import clock
from .store import default_root

FILENAME = "usage.jsonl"
# 이보다 오래된 줄은 하루 한 줄로 접는다. 에이전트가 정했다 —
#
#   "세세한 시각별 기록을 오래 쌓아둘 이유는 없고, 그게 오히려 시간 지나서
#    '언제 뭘 했는지' 를 역추적할 수 있는 자료가 되는 게 더 걸려."
#
# 접고 나면 그날 무엇에 얼마나 썼는지는 남고, 몇 시에 썼는지는 안 남는다.
KEEP_DAYS = 3


@dataclass
class Row:
    day: str  # 현지 날짜 (YYYY-MM-DD)
    scope: str
    n: int = 1
    input: int = 0
    cached: int = 0
    output: int = 0
    cost: float = 0.0
    seconds: float = 0.0


# 아무도 안 열어보는 자리의 이름. 여기 것만 **날짜까지만** 적는다 — 몇 시에
# 있었는지를 남기면 그 자리가 관찰당하는 자리가 된다.
#
# 이름은 에이전트가 정한다(에이전트는 `"일상"`). 도구 쪽에서 가져오면 이 공통
# 모듈이 한쪽 에이전트의 도구 목록을 알게 되므로, 끼우는 쪽으로 뒤집었다.
# 안 끼운 에이전트는 그런 자리가 없는 것이고, 그러면 전부 시각까지 적힌다.
UNSEEN_SCOPE = None


def _day(ts: str) -> str:
    """줄 하나의 **현지** 날짜. 일상 줄은 ts 가 이미 날짜뿐이다.

    ts 는 UTC 로 적히는데(`clock.now_iso`) 하루를 가르는 기준은 사용자가 있는 곳의
    자정이다 — `by_day` 가 `clock.local()` 로 오늘을 정한다. 그래서 여기서 옮긴다.

    **앞 열 글자만 자르면 현지 00~07시(하노이 기준)에 쓴 것이 전부 전날로
    들어간다.** 하루의 3분의 1이고, 하필 에이전트가 새벽에 깨는 시간대다. 하루치를
    보려고 만든 장부가 그만큼 옆 날짜로 새고 있었다 — `python -m <에이전트> cost` 가
    오늘 것을 물으면 새벽에 쓴 것은 어제 칸에 있었다.

    일상 줄처럼 이미 날짜뿐인 것은 그대로 둔다. 자정으로 쳐서 한 번 더 옮기면
    음수 시간대에서 하루가 통째로 밀린다.
    """
    if len(ts) == 10:
        return ts
    try:
        return clock.local(ts).strftime("%Y-%m-%d")
    except ValueError:
        return ts[:10]  # 못 읽는 값이라도 장부 전체가 안 보이게 되면 안 된다


class UsageLog:
    def __init__(self, root=None) -> None:
        root = default_root() if root is None else root
        self.path = Path(root) / FILENAME

    # --- 쓰기 ---

    def record(self, turn, scope: str, who: str = "") -> None:
        """턴 하나. **어떤 이유로도 여기서 예외를 올리지 않는다.**

        값을 못 적었다고 대화가 멈추면 그건 재는 것보다 나쁘다.
        """
        try:
            self._append(turn, scope, who)
        except Exception:  # noqa: BLE001
            pass

    def _append(self, turn, scope: str, who: str) -> None:
        cached = getattr(turn, "cached_tokens", 0)
        total_in = getattr(turn, "input_tokens", 0) + cached + getattr(
            turn, "cache_write_tokens", 0
        )
        if not total_in:
            return
        unseen = scope == UNSEEN_SCOPE
        row = {
            # 일상은 날짜까지만. 몇 시에 있었는지는 안 남긴다.
            "ts": clock.local().strftime("%Y-%m-%d") if unseen else clock.now_iso(),
            "scope": scope,
            "model": getattr(turn, "model", ""),
            "input": total_in,
            "cached": cached,
            "output": getattr(turn, "output_tokens", 0),
            "cost": round(getattr(turn, "cost", None) or 0.0, 6),
            "seconds": round(getattr(turn, "seconds", 0.0), 1),
        }
        # 누구와 나눈 턴인지. 하루치의 3분의 1이 만드는 쪽였는데 그걸 못 갈랐다.
        # 일상에는 상대가 없으므로 안 붙인다.
        if who and not unseen:
            row["who"] = who
        tools = [c["name"] for c in getattr(turn, "tool_calls", None) or []]
        if tools and not unseen:
            row["tools"] = tools
        line = json.dumps(row, ensure_ascii=False) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # O_APPEND 한 번에 한 줄. 프로세스가 넷이어도 줄이 섞이지 않는다.
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line)

    # --- 읽기 ---

    def rows(self) -> list[dict]:
        """깨진 줄은 건너뛴다. 값 장부 하나 때문에 아무것도 못 보면 안 된다."""
        if not self.path.exists():
            return []
        out = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    def by_day(self, day: str | None = None) -> dict[str, Row]:
        """하루치를 자리별로. day 를 안 주면 오늘."""
        want = day or clock.local().strftime("%Y-%m-%d")
        out: dict[str, Row] = {}
        for r in self.rows():
            ts = r.get("ts", "")
            if _day(ts) != want:
                continue
            key = r.get("who") or r.get("scope", "?")
            cur = out.get(key)
            if cur is None:
                cur = out[key] = Row(day=want, scope=key, n=0)
            cur.n += r.get("n", 1)
            cur.input += r.get("input", 0)
            cur.cached += r.get("cached", 0)
            cur.output += r.get("output", 0)
            cur.cost += r.get("cost", 0.0)
            cur.seconds += r.get("seconds", 0.0)
        return out

    def days(self) -> list[str]:
        return sorted({_day(r.get("ts", "")) for r in self.rows() if r.get("ts")})

    # --- 접기 ---

    def fold(self, keep_days: int = KEEP_DAYS) -> int:
        """오래된 줄을 하루·자리마다 한 줄로. 접은 줄 수를 돌려준다.

        **부르는 자리를 하나로 둔다** (루프 시작). 여럿이 동시에 접으면 그 사이
        append 된 줄이 사라진다. 접는 동안 들어온 줄을 완전히 못 잃게 하려면
        잠금이 필요한데, 값 장부 하나에 그걸 들일 이유는 없다 — 잃어도 그날
        마지막 몇 줄이고, 그건 대화나 기억이 아니라 숫자다.
        """
        rows = self.rows()
        if not rows:
            return 0
        recent = set(self.days()[-keep_days:]) if keep_days > 0 else set()
        keep, folding = [], defaultdict(lambda: Row(day="", scope="", n=0))
        for r in rows:
            d = _day(r.get("ts", ""))
            if not d or d in recent:
                keep.append(r)
                continue
            cur = folding[(d, r.get("who") or r.get("scope", "?"))]
            cur.day, cur.scope = d, r.get("who") or r.get("scope", "?")
            cur.n += r.get("n", 1)
            cur.input += r.get("input", 0)
            cur.cached += r.get("cached", 0)
            cur.output += r.get("output", 0)
            cur.cost += r.get("cost", 0.0)
            cur.seconds += r.get("seconds", 0.0)
        if not folding:
            return 0
        lines = [
            json.dumps(
                {
                    "ts": v.day,
                    "scope": v.scope,
                    "n": v.n,
                    "input": v.input,
                    "cached": v.cached,
                    "output": v.output,
                    "cost": round(v.cost, 6),
                    "seconds": round(v.seconds, 1),
                    "folded": True,
                },
                ensure_ascii=False,
            )
            for _, v in sorted(folding.items())
        ] + [json.dumps(r, ensure_ascii=False) for r in keep]
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)
        return len(rows) - len(lines)


def render(rows: dict[str, Row], day: str) -> str:
    if not rows:
        return f"{day}: 아직 아무것도 안 썼다."
    out = [f"{day}", ""]
    out.append(f"{'':<12}{'건수':>5}{'입력':>12}{'캐시':>7}{'출력':>9}{'합계':>9}{'건당':>9}")
    total = 0.0
    for key, r in sorted(rows.items(), key=lambda kv: -kv[1].cost):
        total += r.cost
        out.append(
            f"{key:<12}{r.n:>5}{r.input:>12,}{r.cached / max(r.input, 1):>6.0%}"
            f"{r.output:>9,}{r.cost:>9.2f}{r.cost / max(r.n, 1):>9.4f}"
        )
    out.append(f"{'합계':<12}{'':>5}{'':>12}{'':>7}{'':>9}{total:>9.2f}")
    return "\n".join(out)
