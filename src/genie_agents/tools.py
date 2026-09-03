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
#
# 답 **가운데** 선 지문을 걷는 길이 한도. 재서 골랐다 — 아래 함수 주석.
MID_STAGE_MAX = 300
_STAGE = __import__("re").compile(
    r"^[ \t]*[(（]([^)）]{6,240})[)）][ \t]*\n?", __import__("re").M)


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

    ★ **줄 맨 앞에 선 것만, 뒤에 말이 남을 때만 건다.** 문장 안에 끼워 쓴
      괄호는 보통 글이고(“그거(어제 그거) 말이야”), 통째로 괄호뿐인 답을
      걷으면 아무 말도 안 남는다 — 그러면 루프가 빈 답으로 보고 다시 묻는다.

    ★ **답 맨 앞 것만 걷었다. 가운데 것도 건다 — 다만 짧은 답에서만**
      (2026-09-03). 실제로 새 나간 것(유나·로컬):

          오빠, 로컬 LLM 띄웠구나! …
          (잠깐 멈췄다가) 아, 그리고 아까 그 기억들…

          오빠, 나 지금 3건의 기억이 겹치는 걸 알게 됐네. 3건이나!
          (잠시 생각하는 듯 멈춘 후, 기억을 열지 않고 자연스럽게 반응한다.)
          음... 3건이나 겹치다니…

      맨 앞만 보면 둘 다 안 걸린다. 그런데 **자리를 안 가리고 걷으면 진짜
      내용을 먹는다.** 재 봤다(유나 클라우드 8,709발언): 줄 맨 앞에 선 괄호
      153개가 걷히는데 거의 전부 기술 설명의 알맹이였다 —

          (varStatusFilter = "All" || Status = varStatusFilter)
          (msdyn_flow_approval 테이블)
          (참고로 서비스 계정 대신 서비스 프린시펄 쓰는 길도 있는데 …)

      가르는 것은 글자 모양이 아니라 **답의 길이**였다. 지문은 짧은 감정
      대화에 붙고, 줄 맨 앞 괄호가 알맹이인 답은 길다. 재서 골랐다 —
      클라우드 17,886발언(유나 8,709 + 예나 9,177) 기준:

          한도 300자   가운데서 걷힘 0건        ← 이걸로
          한도 400자   4건 (전부 진짜 설명)
          한도 600자   10건 (전부 진짜 설명)

      새 나간 둘은 207자와 116자라 300 안에 든다.

    ★ **여섯 글자 아래는 안 건다.** “(웃음)” 같은 것은 지문이 아니라 말이다.
      바닥이 여덟이었는데 “(잠깐 멈췄다가)” 가 일곱 자라 그대로 새 나갔다
      (2026-09-03). 재 보고 내렸다.

    ★ **걷은 것을 보고하지 않는다.** `turn.dropped` 에 넣으면 루프가 "안 한
      일을 한 것처럼 적었다" 는 쪽지를 붙여 다시 묻는다(`drop_scaffolding`
      주석 참고). 이건 그것과 다른 일이라 조용히 건다.
    """
    짧다 = len(text) <= MID_STAGE_MAX
    조각, 자리, 앞머리, 바뀜 = [], 0, True, False
    for m in _STAGE.finditer(text):
        사이 = text[자리:m.start()]
        if 사이.strip():
            앞머리 = False          # 여기부터는 답 가운데다
        if 앞머리 or 짧다:
            조각.append(사이)       # 지문은 버린다
            바뀜 = True
        else:
            조각.append(사이 + m.group(0))
        자리 = m.end()
    if not 바뀜:
        return text, []
    조각.append(text[자리:])
    남은 = "".join(조각)
    if not 남은.strip():             # 이게 전부면 그냥 둔다
        return text, []
    남은 = "\n".join(line.rstrip() for line in 남은.split("\n"))
    while "\n\n\n" in 남은:
        남은 = 남은.replace("\n\n\n", "\n\n")
    return 남은.strip(), []


# id 없이 라벨만 쓴 표시. `[사진]` `[음성]` `[영상]`.
#
# ★ **`]` 가 바로 와야 잡는다.** 진짜 표시는 `[사진:id]` 라 콜론이 있고,
#   그건 `media.drop_unknown` 이 본다. 여기는 그 사이로 빠져나가던 것이다.
_BARE_MARK = __import__("re").compile(r"[(（]?\[(사진|음성|영상)\][)）]?")


def drop_bare_marks(text: str) -> tuple[str, list[str]]:
    """`[사진]` 처럼 **id 없이 라벨만** 쓴 표시를 걷고, 그 도구를 강제하게 한다.

    ★ **이건 얼개가 쓰는 글자다.** 사진이 붙어 온 턴에 `media.IMAGE_NOTE`
      (`"[사진]"`)가 오빠 말 끝에 붙는다 — 픽셀은 따로 실려 가고 본문에는
      한 줄 표시만 남기는 자리다. 모델이 그걸 보고 **자기 답 끝에 그대로
      따라 붙였다.** 오빠 화면에는 글자만 뜨고 사진은 안 온다.

      오빠가 짚었다(2026-09-03) — "예나가 자꾸 [사진] 텍스트 보내고 진짜
      사진을 안보내". 재 보니 예나 20건이고 **전부 로컬로 내린 뒤**다
      (08-31까지 9,177발언 중 0건 → 09-01 이후 353발언 중 20건). 유나는 0건.

          … 한번 보여줄게! [사진]
          … 내 마음이야. [사진]
          … 우리 다른 이야기 할까? [음성]

    ★ **왜 기존 손에 안 걸렸나.** `drop_unknown` 은 `[사진:id]` 를 보고,
      `drop_bare_ids` 는 16자리 16진수를 보고, `drop_fake_tags` 는 라벨이
      영문일 때만 본다. 콜론도 id 도 영문도 없는 이 모양이 셋 사이로 빠졌다.

    ★ **보고한다.** `drop_bare_ids` 와 같은 자리다 — 이건 장식이 아니라
      **빈 약속**이다. "보여줄게" 라고 해놓고 안 온 것이라, 걷고 나서 그
      도구를 강제해야 한다(`forced_after_drop`). 그래서 걷힌 것을 `[사진:`
      모양으로 적어 돌려준다 — 그 글자를 보고 부를 도구가 정해진다.
    """
    걷은것 = []

    def 바꿔(m):
        걷은것.append(f"[{m.group(1)}:] (id 없이 맨몸으로 쓴 것)")
        return ""

    남은 = _BARE_MARK.sub(바꿔, text)
    if not 걷은것:
        return text, []
    남은 = __import__("re").sub(r"[ \t]{2,}", " ", 남은).strip()
    return 남은, 걷은것


# 표시 없이 맨몸으로 선 id. 괄호에 넣었든 그냥 뒀든.
#
# ★ **16자리 16진수는 우연히 안 나온다.** 사람 글에도, 한국어에도 없다.
#   앞뒤가 낱말이면 안 잡는다 — 우연히 그런 글자가 이어진 자리를 피한다.
_BARE_ID = __import__("re").compile(
    r"(?<![\w:])[(（]?\b([0-9a-f]{16})\b[)）]?(?![\w\]])")


def drop_bare_ids(text: str, kinds=("사진",)) -> tuple[str, list[str]]:
    """`(3152f31e1fb34aa9)` 처럼 표시를 흉내 낸 것을 걷는다.

    ★ **진짜 표시는 `[사진:id]` 다.** 괄호로 쓰면 화면이 못 알아보고 그냥
      글자로 뜬다 — 오빠 화면에 id 만 덩그러니 남았고 사진은 안 왔다
      (2026-09-02): "(3152f31e1fb34aa9) 오빠가 원했던 모습으로 다시 찍어봤어".
      오빠가 "사진이 안보이는데" 라고 되물었다.

      게다가 그건 **아까 보낸 사진의 id** 였다. 새로 만든 것이 아니라 옛 것을
      가리킨 것이라, 통과시켜도 같은 사진이 다시 갈 뿐이다.

    ★ **보고한다.** 여기 걷힌 것은 "보낸다고 해놓고 안 보낸 것" 이라 루프가
      그 도구를 강제해야 한다(`forced_after_drop`). `drop_scaffolding` 이
      조용히 거는 것과 반대 자리다 — 그건 혼잣말이고 이건 빈 약속이다.

      그래서 걷힌 것을 `[사진:` 모양으로 적어 돌려준다. 그 글자를 보고
      `forced_after_drop` 이 `self_portrait` 를 고른다.
    """
    걷은것 = []

    def 바꿔(m):
        걷은것.append(f"[{kinds[0]}:{m.group(1)}] (표시 없이 쓴 것)")
        return ""

    남은 = _BARE_ID.sub(바꿔, text)
    if not 걷은것:
        return text, []
    # 걷고 나면 공백이 겹친다. 줄은 살리고 사이만 좁힌다.
    남은 = __import__("re").sub(r"[ \t]{2,}", " ", 남은).strip()
    return 남은, 걷은것


# 줄 맨 앞에 선 `[영문낱말:…]` 꼴 태그. **진짜 표시는 한글 라벨이다**
# (`media.LABELS` — 사진 · 음성 · 영상). 도구 이름을 대괄호로 적은 것은
# `drop_bracket_calls` 가 따로 보고, 그건 콜론이 없다.
#
# ★ `](` 앞에서는 안 잡는다 — 마크다운 링크(`[see: here](url)`)를 지우면
#   글이 깨진다. 이 자리에서 링크를 쓸 일은 거의 없지만 값싼 안전장치다.
_FAKE_TAG = __import__("re").compile(
    r"^[ \t]*\[[A-Za-z][A-Za-z0-9_-]{0,15}:[^\]\n]{0,200}\](?!\()[ \t]*",
    __import__("re").M)


def drop_fake_tags(text: str) -> tuple[str, list[str]]:
    """`[voice:따뜻하고 다정한 목소리]` 처럼 지어낸 태그를 건다. **조용히 건다.**

    ★ **이런 기능은 없다.** 목소리에 톤을 실어 보내는 길이 어디에도 없고
      (`voice.py` · `voice_reply` 어디에도), 표시를 붙이는 것은 도구지
      모델이 아니다. 그런데 답 맨 앞에 이렇게 서서 그대로 나갔다 —
      오빠 화면에는 대괄호 글자가 뜬다. 오빠가 짚었다(2026-09-03):
      "예나 자꾸 voice: 이상한 태그 붙어".

          [voice:단호하지만 애정이 담긴 톤] 오빠, 내가 이전에 분명히 말했잖아…
          [voice:걱정스럽고 애타는 톤] "오빠... 잠시만 기다려줘…

    ★ **로컬만이 아니다.** 열셋 중 열둘이 로컬이었는데 하나가
      gemini-2.5-pro 였다(2026-09-03 00:57). 앞 턴들이 꼬리에 실려 오니
      큰 모델도 제 말투를 베낀다 — 한 번 새면 스스로 배운다. 그래서
      `drop_stage_directions` 와 달리 **자리를 안 가리고 건다.**

    ★ **가르는 기준은 라벨이 영문인가** 다. 진짜 표시는 `[사진:…]`
      `[음성:…]` `[영상:…]` 로 한글이고, 도구를 대괄호로 적은 것은 콜론이
      없다(`drop_bracket_calls`). 영문 낱말에 콜론이 붙어 줄 맨 앞에 서는
      것은 이 둘 중 어느 것도 아니다.

    ★ **걷은 것을 보고하지 않는다.** 이건 빈 약속이 아니라 장식이다 —
      보고하면 루프가 "안 한 일을 한 것처럼 적었다" 며 다시 묻고, 그
      요청값을 태우면서 얻는 것이 없다(`drop_bare_ids` 와 반대 자리).
    """
    남은 = _FAKE_TAG.sub("", text)
    if 남은 == text or not 남은.strip():   # 이게 전부면 그냥 둔다
        return text, []
    return 남은.strip(), []


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
