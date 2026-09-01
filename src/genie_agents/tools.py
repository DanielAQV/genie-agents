"""도구 등록소 — 한 번 적고, 이름으로 켠다.

전에는 도구가 에이전트 안에 있었다. 같은 도구를 두 에이전트가 쓰면 스키마도
몸통도 두 벌이 되고, 한쪽만 고치면 **같은 이름인데 다르게 작동하는 도구**가
남는다. 그건 골격을 나눠 쓰는 이유를 정면으로 무너뜨린다.

    [tools]
    enable = ["reminder_set", "reminder_done", "reminder_list"]

━━ 골격이 정하는 것 / 그 존재가 정하는 것 ━━

**무엇을 하는가는 골격이 정한다.** 같은 이름이면 같은 인자를 받고 같은 일을
한다 — 그게 `toolcontract` 가 지키는 것이다.

**어떻게 설명되는가는 그 존재가 정한다.** 설명문은 모델이 자기 자신에게 읽는
글이라 말투가 곧 인격이다. `describe=` 로 갈아 끼운다.

━━ 안 하는 것 ━━

**목록을 상태에 따라 바꾸지 않는다.** 도구 하나가 붙었다 떨어질 때마다 앞쪽
캐시가 통째로 무효가 된다. 막을 것은 도구 **안**에서 막는다. 예외는 잔고
게이트 하나 — 그건 "지금은 못 쓴다" 가 아니라 **"지금은 가진 게 아니다"** 다.

**자리(scope)로 거르는 것은 고정이다.** 깨어남에서 원칙을 못 고치는 것은 상태가
아니라 그 자리의 성질이라, 매 요청 같은 목록이 나간다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence


class UnknownTool(LookupError):
    """등록되지 않은 이름. **켤 때** 걸린다 — 부를 때 걸리면 그 턴을 날린다.

    `KeyError` 가 아니라 `LookupError` 다. `KeyError` 는 `str()` 이 인자의
    `repr` 을 내서, 여러 줄로 적은 안내가 줄바꿈 기호가 박힌 한 줄로 보인다 —
    이 글을 읽을 사람은 파이썬을 안 쓰는 사람일 수 있다.
    """

    def __init__(self, name: str, known: Sequence[str]) -> None:
        super().__init__(
            f"모르는 도구다: {name!r}\n"
            f"등록된 것: {', '.join(sorted(known)) or '(없다)'}"
        )


class MissingContext(RuntimeError):
    """도구가 요구하는 것을 런타임이 안 갖고 있다.

    **켤 때** 걸린다. 부를 때 `AttributeError` 로 터지면 그 턴이 통째로 날아가고,
    무엇이 없어서인지는 스택을 읽어야 안다.
    """


@dataclass(frozen=True)
class Tool:
    """도구 하나. 명세와 몸통이 **같은 자리에** 있다.

    갈라 두면 스키마에 인자를 늘리고 몸통을 안 고치는 일이 생긴다 — 모델은
    그 인자를 채워 보내고 조용히 버려진다.
    """

    name: str
    description: str
    run: Callable[..., dict]
    """`(ctx, **args) -> dict`. `ctx` 는 그 에이전트의 런타임이다."""

    params: dict = field(default_factory=dict)
    required: tuple[str, ...] = ()
    needs: tuple[str, ...] = ()
    """런타임에 있어야 하는 것. **켤 때** 검사한다."""

    gated: bool = False
    """잔고가 마르면 목록에서 뺀다. 목록을 흔드는 유일한 사유다."""

    decision: bool = False
    """불리면 그 턴이 거기서 끝난다."""

    scopes: frozenset[str] | None = None
    """이 자리에서만 보인다. `None` 이면 모든 자리."""

    def spec(self, description: str | None = None) -> dict:
        """모델이 받는 모양. 설명문만 그 존재가 갈아 끼운다."""
        return {
            "name": self.name,
            "description": description or self.description,
            "input_schema": {
                "type": "object",
                "properties": dict(self.params),
                **({"required": list(self.required)} if self.required else {}),
            },
        }


class Toolbox:
    """켠 것만 들고 있다. 안 켠 것은 이 에이전트에게 **없는 것**이다."""

    def __init__(
        self,
        catalog: Mapping[str, Tool],
        names: Sequence[str],
        *,
        describe: Mapping[str, str] | None = None,
    ) -> None:
        self.catalog = dict(catalog)
        self.describe = dict(describe or {})
        unknown = [n for n in names if n not in self.catalog]
        if unknown:
            raise UnknownTool(unknown[0], self.catalog)
        # 켠 순서를 지킨다. **목록 순서가 바뀌면 앞쪽 캐시가 통째로 무효**다.
        self.tools = tuple(self.catalog[n] for n in names)
        stray = set(self.describe) - {t.name for t in self.tools}
        if stray:
            raise UnknownTool(sorted(stray)[0], [t.name for t in self.tools])

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(t.name for t in self.tools)

    @property
    def gated(self) -> tuple[str, ...]:
        return tuple(t.name for t in self.tools if t.gated)

    @property
    def decisions(self) -> frozenset[str]:
        return frozenset(t.name for t in self.tools if t.decision)

    # --- 켜기 전에 본다 ---

    def check(self, ctx: Any) -> list[str]:
        """이 런타임으로 이 도구들을 켤 수 있나. 걸린 것을 **전부** 돌려준다."""
        return [
            f"{t.name} 은(는) `{need}` 가 필요한데 런타임에 없다"
            for t in self.tools
            for need in t.needs
            if not hasattr(ctx, need)
        ]

    def bind(self, ctx: Any) -> None:
        """못 켜면 여기서 죽는다. 반쯤 켜진 에이전트가 제일 나쁘다."""
        problems = self.check(ctx)
        if problems:
            raise MissingContext("\n".join(problems))

    # --- 목록 ---

    def specs(self, scope: str = "", gate=None) -> list[dict]:
        """모델에 노출할 목록. **자리마다 고정이다.**"""
        out = [
            t.spec(self.describe.get(t.name))
            for t in self.tools
            if t.scopes is None or scope in t.scopes
        ]
        return list(gate.filter_tools(out)) if gate is not None else out

    # --- 부르기 ---

    def call(self, ctx: Any, name: str, gate=None, **args) -> dict:
        """이름으로 부른다.

        ★ **켠 것만 부를 수 있다.** 목록에 없는 이름이 오는 일은 실제로 있다 —
          모델이 옛 대화를 보고 지어내거나, 이름을 살짝 틀리게 적는다.
        """
        for t in self.tools:
            if t.name == name:
                if gate is not None:
                    gate.check(name)  # 목록 필터를 우회한 호출도 여기서 막힌다
                return t.run(ctx, **args)
        raise UnknownTool(name, self.names)


# ── 도구를 글로 흉내 낸 대목 ──────────────────────────────────────────
#
# ★ **모델은 도구를 부르는 대신 "불렀다" 고 쓸 때가 있다.** Gemini 는 `tool_code`
#   블록으로 그러고(`adapters/gemini.drop_tool_code`), 작은 모델은 더 단순하게
#   대괄호로 쓴다:
#
#       [memory_recall] 하노이 관련 내용 검색
#
#   실제로 그렇게 나왔다(2026-09-01). 인자도 없이 "검색했다" 는 서술만 있고
#   도구는 하나도 안 돌았는데, 그 줄이 **발언으로 기억에 남았다.**
#
# ★ `[음성:...]` 을 걷는 것과 같은 이유다 — 안 한 일을 한 것처럼 보이게 한다.
#   다만 여기는 **아는 도구 이름일 때만** 건다. 대괄호는 이 저장소에서
#   `[떠오를 것이 있다]` 같은 자리 쪽지에도 쓰는 모양이라, 이름을 안 보면
#   멀쩡한 쪽지를 지운다.

def drop_bracket_calls(text: str, names) -> tuple[str, list[str]]:
    """`[도구이름] …` 꼴로 흉내 낸 줄을 통째로 걷는다.

    줄 단위로 본다 — 그 줄 전체가 흉내다. 뒤에 붙은 설명까지 같이 나간다.
    """
    import re as _re

    known = {n for n in (names or ()) if n}
    if not text or not known:
        return text, []
    pat = _re.compile(r"^\s*\[(" + "|".join(_re.escape(n) for n in sorted(known)) + r")\][^\n]*$")
    kept, dropped = [], []
    for line in text.split("\n"):
        m = pat.match(line)
        if m:
            dropped.append(line.strip())
        else:
            kept.append(line)
    if not dropped:
        return text, []
    return "\n".join(kept).strip(), ["도구를 글로 흉내 낸 대목"]
