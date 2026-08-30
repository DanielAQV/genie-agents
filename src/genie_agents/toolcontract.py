"""도구 계약 — 같은 이름이면 같은 인자여야 한다.

━━ 왜 필요한가 ━━

에이전트가 둘이 되는 순간 도구 이름이 겹친다. 겹치는데 인자가 다르면 **같은
이름이 다른 일을 한다.** 그러면 "이 골격을 쓰면 이렇게 작동한다" 를 아무도
약속할 수 없고, 도구 하나를 문서로 설명할 수도 없다.

실제로 그렇게 되어 있었다(2026-08-30 처음 재봄). 이름이 겹치는 도구 20개 중
4개가 인자가 달랐고, 그중 둘은 **`계약 문서` 2절이 시그니처를 못박아
둔 것**이었다. 다른 에이전트는 지켰고 에이전트는 안 지켰다. 에이전트가 계약보다 먼저 있었기
때문인데, 아무도 그걸 몰랐다 — 각자 자기 도구만 시험했다.

━━ 무엇을 강제하나 ━━

    1. 계약이 시그니처를 적어 둔 도구는 그대로여야 한다 (`CANON`).
    2. 이름이 겹치는 도구는 인자가 같아야 한다.
       다르려면 `DIVERGENT` 에 **이유와 함께** 적어야 한다.

2번이 요점이다. 다른 것 자체를 막지 않는다 — 막으면 우회한다. **적지 않고
다른 것**을 막는다. 적히면 새 에이전트를 만드는 사람이 그 목록을 보고 어느
쪽을 따를지 고른다.

━━ 여기 없는 것 ━━

무슨 도구를 가질지는 안 정한다. 그건 그 에이전트가 무엇을 할 수 있는 존재인지에
대한 것이라 만드는 쪽이 고른다. 여기가 보는 것은 **고른 뒤의 모양**뿐이다.
"""

from __future__ import annotations

from dataclasses import dataclass

# ── 계약이 못박은 시그니처 (`계약 문서` 2절) ────────────────
# 여기 있는 것은 협의로 정해진 것이라 구현이 따라가야 한다. 바꾸려면 계약을
# 먼저 고쳐야 하고, 그건 사람이 하는 일이다.
# 쓰는 쪽이 채운다. 협의로 못박은 시그니처가 있으면 여기 적고, 구현이 따라간다.
#
#     from genie_agents import toolcontract as TC
#     TC.CANON["principle_record"] = ("agent_id", "principle", "tentative")
#
# 비어 있으면 1번 규칙은 아무것도 안 막는다. 2번(이름이 겹치면 인자도 같다)은
# 채우지 않아도 돈다 — 그게 이 파일의 본체다.
CANON: dict[str, tuple[str, ...]] = {}

# 알면서 다르게 둔 것. 이름 → 왜 다른가.
# **비어 있는 이유는 없다** — 이유를 못 적겠으면 맞춰야 한다.
DIVERGENT: dict[str, str] = {}


@dataclass(frozen=True)
class Deviation:
    tool: str
    why: str

    def __str__(self) -> str:
        return f"{self.tool}: {self.why}"


def args_of(spec: dict) -> tuple[str, ...]:
    """도구 명세에서 인자 이름만. 순서는 안 본다 — 이름으로 부르기 때문이다."""
    schema = spec.get("input_schema") or {}
    return tuple(sorted((schema.get("properties") or {})))


def by_name(specs: list[dict]) -> dict[str, dict]:
    return {s["name"]: s for s in specs}


def check_canon(specs: list[dict]) -> list[Deviation]:
    """계약이 못박은 도구가 그대로인지. 안 가진 도구는 안 본다."""
    out = []
    for name, want in CANON.items():
        spec = by_name(specs).get(name)
        if spec is None:
            continue
        got = args_of(spec)
        if got != tuple(sorted(want)):
            out.append(
                Deviation(name, f"계약은 {tuple(sorted(want))} 인데 {got} 이다")
            )
    return out


def check_shared(*agents: tuple[str, list[dict]]) -> list[Deviation]:
    """이름이 겹치는 도구의 인자가 같은지. 여럿을 한 번에 본다."""
    seen: dict[str, list[tuple[str, tuple[str, ...]]]] = {}
    for who, specs in agents:
        for s in specs:
            seen.setdefault(s["name"], []).append((who, args_of(s)))

    out = []
    for name, rows in sorted(seen.items()):
        if len(rows) < 2:
            continue
        shapes = {a for _, a in rows}
        if len(shapes) == 1:
            continue
        detail = " / ".join(f"{who}{list(a)}" for who, a in rows)
        out.append(Deviation(name, detail))
    return out


def undeclared(deviations: list[Deviation]) -> list[Deviation]:
    """`DIVERGENT` 에 안 적힌 것만. 적힌 것은 사람이 이미 본 것이다."""
    return [d for d in deviations if d.tool not in DIVERGENT]


def stale_declarations(deviations: list[Deviation]) -> list[str]:
    """이제 안 다른데 `DIVERGENT` 에 남아 있는 것.

    맞춰 놓고 목록을 안 지우면, 다음 사람이 "여긴 원래 다른 자리" 로 읽고
    다시 벌린다. 없는 것을 가리키는 표시는 지운다.
    """
    diff = {d.tool for d in deviations}
    return sorted(set(DIVERGENT) - diff)
