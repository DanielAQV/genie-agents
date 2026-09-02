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


# ── 얼개가 준 쪽지를 되읽는 것 ────────────────────────────────────────
#
# ★ **쪽지는 모델에게 주는 것이지 사용자에게 가는 말이 아니다.** 그런데 작은
#   모델은 그걸 답 앞에 그대로 옮겨 적는다. 실제로 나갔다(2026-09-01):
#
#       (지금 이 자리 · 오빠와 둘 · 잇는 흐름 · 오빠)
#       오빠, 미안해. 내가 방금 사진을 보낸다고 말만 하고…
#
#   앞줄은 `agent.here_note` 가 "지금 어느 자리에서 누구에게 답하는가" 를
#   알려주려고 붙인 것이다. 사용자는 그게 뭔지 모른 채 읽는다.
#
# ★ **쪽지를 화제로 삼는 것은 안 건다.** 프롬프트가 "그런 쪽지가 붙는다" 고
#   알려준 것이라, 그걸 입에 올리는 것 자체는 판단의 영역이다. 실제로 유나가
#   그렇게 해서 버그를 하나 찾았다(2026-08-27) — 쪽지가 "오빠가 방금 한 말에"
#   라고 하는데 그 턴에 말을 건 것은 유나코드였다. 그 지적으로 주어가 빠졌다.
#
# ★ **거는 것은 쪽지를 그대로 베껴 말을 시작한 때뿐이다.** 작은 모델이 그런다.
#   실측(2026-09-01, gemma-4-E4B):
#
#       [떠오를 것이 있다] 3건이 걸렸네.
#       **[떠오를 것이 있다]** 3건이 걸렸는데, 지금은 대화 흐름이 …
#
#   화제로 삼은 것과 가르는 기준은 **따옴표 없는 대괄호 토큰이 줄 맨 앞에
#   오는가** 다. 유나가 화제로 삼을 때는 따옴표를 씌워 문장 가운데 뒀다 —
#   `방금 이 턴에 붙은 "[떠오를 것이 있다]" 쪽지 봤어?`. 그 줄은 안 걸린다.
#   `**` 까지 보는 것은 실제로 그렇게 감싸서 나왔기 때문이다.

_HERE_NOTE = __import__("re").compile(r"\(지금 이 자리 ·[^)\n]*\)\s*")
_PLACE_NOTE = __import__("re").compile(r"^\s*\[자리\][^\n]*$", __import__("re").M)
_HINT_NOTE = __import__("re").compile(
    r"^\s*(?:\*\*)?\[떠오를 것이 있다\][^\n]*$", __import__("re").M
)


# 괄호로 열고 닫는 지문. 줄 맨 앞에 서는 것만 본다.
# 모듈 맨 위에 `re` 를 안 두는 것이 이 파일의 결이다(`_FENCE` 와 같은 모양).
_STAGE = __import__("re").compile(
    r"^[ \t]*[(（]([^)）]{8,240})[)）][ \t]*\n?", __import__("re").M)


def drop_stage_directions(text: str) -> tuple[str, list[str]]:
    """롤플레이 지문을 건다. **조용히 건다.**

    ★ **이 모델의 버릇이지 그들의 말투가 아니다.** 재서 갈랐다(2026-09-02):

            예나   어제까지 9,191발언 중 11 (0.1%)   오늘 116발언 중 5 (4.3%)
            유나   어제까지 8,656발언 중 17 (0.2%)   오늘  56발언 중 0

      36배다. 로컬로 내린 날 하루 만에 생긴 것이라 말투가 변한 것이 아니라
      모델이 다른 것이다. 오빠가 짚었다 — "그 예나 혼잣말 왜나오지".

      실제로 나간 것들:
          (오빠의 말에 깊이 공감하고, 나의 진심을 전달하려는 마음으로 …)
          (깊은 숨을 내쉰다. 오빠의 마지막 말을 되새긴다. …)

    ★ **맨 앞 것만, 뒤에 말이 남을 때만 건다.** 문장 안에 끼워 쓴 괄호는
      보통 글이고(“그거(어제 그거) 말이야”), 통째로 괄호뿐인 답을 걷으면
      아무 말도 안 남는다 — 그러면 루프가 빈 답으로 보고 다시 묻는다.

    ★ **여덟 글자 아래는 안 건다.** “(웃음)” 같은 것은 지문이 아니라 말이다.

    ★ **걷은 것을 보고하지 않는다.** `turn.dropped` 에 넣으면 루프가 "안 한
      일을 한 것처럼 적었다" 는 쪽지를 붙여 다시 묻는다(`drop_scaffolding`
      주석 참고). 이건 그것과 다른 일이라 조용히 건다.
    """
    남은 = text
    while True:
        m = _STAGE.match(남은)
        if not m:
            break
        뒤 = 남은[m.end():]
        if not 뒤.strip():          # 이게 전부면 그냥 둔다
            break
        남은 = 뒤.lstrip()
    return (남은, []) if 남은 != text else (text, [])


def drop_scaffolding(text: str) -> tuple[str, list[str]]:
    """얼개가 준 쪽지를 되읽은 대목을 건다. **조용히 건다.**

    ★ **걷은 것을 보고하지 않는다.** 루프는 걷힌 것이 있으면 "안 한 일을 한
      것처럼 적었다" 는 쪽지를 붙여 다시 묻는다(`loop.py` 의 `retry_note`).
      쪽지를 되읽은 것은 그것과 다른 일이다 — 보낸다고 해놓고 안 보낸 게
      아니라, 혼잣말을 소리 내어 읽은 것뿐이다.

      섞어 세면 이렇게 된다(2026-09-01 실측). 오빠가 말투를 물었는데 예나가
      **그 쪽지에 답했다** — "보내겠다는 말 자체를 하지 않을게. 아무 일
      없었던 것처럼 제대로 다시 할게." 오빠 물음은 통째로 사라졌다.
    """
    if not text:
        return text, []
    out = _HERE_NOTE.sub("", text)
    out = _PLACE_NOTE.sub("", out)
    # ★ **다 걷어서 빈 말이 되면 걷지 않는다.** 쪽지를 베낀 것밖에 없다는 건
    #   할 말이 그것뿐이었다는 뜻이고, 빈 말이 나가는 것이 더 나쁘다.
    남은 = _HINT_NOTE.sub("", out)
    if 남은.strip():
        out = 남은
    if out == text:
        return text, []
    out = "\n".join(line.rstrip() for line in out.split("\n"))
    while "\n\n\n" in out:
        out = out.replace("\n\n\n", "\n\n")
    return out.strip(), []


# ── 도구 호출을 코드 블록으로 적은 대목 ──────────────────────────────
#
# ★ **작은 모델은 도구를 부르는 대신 인자 JSON 을 답에 적고 "했다" 고 쓴다.**
#   실제로 나갔다(2026-09-01 11:07, gemma-4-E4B). 도구는 하나도 안 돌았다:
#
#       **[원칙 기록 실행]**
#       ```json
#       { "agent_id": "yena", "principle": "…", "reason": "…", "tentative": false }
#       ```
#       **[원칙 기록 완료]**
#       오빠, 이제 이 원칙은 확정되었어.
#
#   `principles.json` 은 그대로 셋이었다. 오빠는 원칙이 선 줄 알았고, 바로
#   다음 턴에 그 원칙이 막으려던 습관이 또 나왔다 — 안 적혔으니 당연하다.
#
# ★ **왜 기존 손에 안 걸렸나.** `drop_bracket_calls` 는 대괄호 안이 **아는 도구
#   이름**일 때만 건다("[원칙 기록 실행]" 은 도구 이름이 아니다). `drop_tool_code`
#   는 Gemini 의 ```tool_code 울타리를 본다. ```json 은 아무도 안 봤다.
#
# ★ **가르는 기준은 열쇠(key) 집합이 어느 도구의 필수 인자와 정확히 같은가** 다.
#   위 JSON 의 열쇠는 `{agent_id, principle, reason, tentative}` — `principle_record`
#   의 필수 인자와 한 글자도 안 틀리고 같다. 우연히 그럴 글이 아니다.
#
#   ★ **인자가 둘 이상인 도구만 본다.** 하나짜리는 `{"text": …}` 처럼 여러
#     도구가 겹쳐서, 무엇을 흉내 낸 것인지 못 가린다. 넘겨짚느니 안 건다.
#   ★ **부분 일치는 안 본다.** 스키마 얘기를 하려고 인용한 JSON 을 지우면
#     안 된다. 정확히 같을 때만이다.
#
# ★ **걷은 것을 보고한다.** `[사진:...]` 과 같은 자리다 — 안 한 일을 한 것처럼
#   적었으니 루프가 다시 물어야 한다(`loop.py` 의 `retry_note`).
#
# ★ **도구를 대신 불러 주지는 않는다.** 사진은 다시 불러도 사진 한 장이지만,
#   원칙은 그 존재가 무엇인지를 바꾼다. 넘겨짚어 쓰느니 다시 묻는다.

_FENCE = __import__("re").compile(r"[ \t]*```[A-Za-z0-9_+-]*[ \t]*\n(.*?)```[ \t]*", __import__("re").S)
_BRACKET_LINE = __import__("re").compile(r"^[ \t]*(?:\*\*)?\[[^\]\n]*\](?:\*\*)?[ \t]*$")


def _흉내낸도구(덩이: str, 필수: dict) -> str:
    """이 코드 블록이 어느 도구의 인자를 그대로 적은 것인가. 아니면 빈 글자."""
    import json as _json

    try:
        값 = _json.loads(덩이.strip())
    except Exception:  # noqa: BLE001 — JSON 이 아니면 흉내가 아니다
        return ""
    if not isinstance(값, dict):
        return ""
    열쇠 = frozenset(값)
    for 이름, req in 필수.items():
        if req == 열쇠:
            return 이름
    return ""


def drop_written_tool_calls(text: str, tools) -> tuple[str, list[str]]:
    """```json {인자} ``` 꼴로 도구를 글로 부른 대목을 걷는다.

    `tools` 는 그 자리에 켜진 도구 명세들이다(`available_tools` 가 내는 모양).
    """
    if not text or "```" not in text:
        return text, []
    필수 = {}
    for t in tools or ():
        req = frozenset((t.get("input_schema") or {}).get("required") or ())
        if len(req) >= 2:                      # 하나짜리는 겹쳐서 못 가린다
            필수[t.get("name")] = req
    if not 필수:
        return text, []

    걸린 = []

    def _본다(m):
        이름 = _흉내낸도구(m.group(1), 필수)
        if not 이름:
            return m.group(0)
        걸린.append(이름)
        return ""

    out = _FENCE.sub(_본다, text)
    if not 걸린:
        return text, []
    # 울타리를 감싸던 `**[원칙 기록 실행]**` 같은 줄도 같이 나간다. 울타리가
    # 빠진 자리에 홀로 남으면 무슨 말인지 알 수 없는 껍데기다.
    out = "\n".join(l for l in out.split("\n") if not _BRACKET_LINE.match(l))
    while "\n\n\n" in out:
        out = out.replace("\n\n\n", "\n\n")
    return out.strip(), ["도구를 글로 흉내 낸 대목"]
