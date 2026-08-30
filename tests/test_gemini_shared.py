"""Gemini 위에서 도는 에이전트가 공통으로 쓰는 것.

값표·흉내 걷기·그림 자리는 **모델 사정**이지 그 에이전트가 누구인지가 아니다.
에이전트 파일 안에 두면 Gemini 위의 두 번째 에이전트가 값표부터 다시 적는다.
"""

from __future__ import annotations

from types import SimpleNamespace

from genie_agents.adapters import anthropic, gemini
from genie_agents.loop import Turn


def turn(**kw) -> Turn:
    t = Turn(model=kw.pop("model", "gemini-2.5-pro"))
    for k, v in kw.items():
        setattr(t, k, v)
    return t


# ── 어댑터는 같은 이름을 낸다 ────────────────────────────────────────
def test_어댑터는_같은_이름을_낸다():
    """러너가 이름 하나로 부른다. 하나만 빠져도 그 어댑터에서만 죽는다."""
    for mod in (anthropic, gemini):
        for name in ("available", "default_model", "client"):
            assert callable(getattr(mod, name)), f"{mod.__name__}.{name}"


# ── 값 ───────────────────────────────────────────────────────────────
def test_모르는_모델이면_값이_None():
    """토큰만 남기고 돈은 안 찍는다. 0 으로 찍으면 공짜로 돈 것처럼 보인다."""
    assert gemini.cost(turn(model="gemini-9-미래")) is None


def test_문턱을_넘으면_비싼_단가():
    cheap = turn(input_tokens=1_000_000)
    dear = turn(input_tokens=1_000_000)
    dear.extra["max_prompt_tokens"] = 300_000
    assert gemini.cost(cheap) == 1.25
    assert gemini.cost(dear) == 2.50


def test_문턱은_한_요청으로_본다():
    """도구를 여러 번 돈다고 넘어가면, 어느 요청도 안 넘었는데 턴 전체가
    비싼 단가로 계산된다."""
    t = turn(input_tokens=300_000)
    gemini.meter(t, SimpleNamespace(usage=SimpleNamespace(input_tokens=150_000)))
    gemini.meter(t, SimpleNamespace(usage=SimpleNamespace(input_tokens=150_000)))
    assert t.extra["max_prompt_tokens"] == 150_000
    assert gemini.cost(t) == 1.25 * 0.3  # 싼 단가로 남는다


def test_meter_는_usage_가_없어도_안_죽는다():
    t = turn()
    gemini.meter(t, SimpleNamespace())
    assert t.extra.get("max_prompt_tokens", 0) == 0


# ── 흉내 낸 대목 ─────────────────────────────────────────────────────
def test_도구를_글로_흉내_낸_대목을_걷는다():
    said, notes = gemini.sanitize_tool_code("좋아 ✨tool_code\nprint(self_portrait())\n```")
    assert said == "좋아"
    assert notes == ["도구를 글로 흉내 낸 대목"]


def test_안_걷었으면_적지_않는다():
    said, notes = gemini.sanitize_tool_code("그냥 한 말")
    assert (said, notes) == ("그냥 한 말", [])


def test_비슷한_말은_안_걷는다():
    """`\\b` 가 빠지면 `tool_codex` 같은 말이 통째로 사라진다."""
    assert gemini.drop_tool_code("tool_codex 얘기") == "tool_codex 얘기"


# ── 그림은 결과 옆에 ─────────────────────────────────────────────────
def test_그림은_도구_결과_옆에_선다():
    """안에 넣으면 Gemini 가 못 받는다 — function_response 는 글만 받는다."""
    blocks = gemini.blocks_beside({"ok": True, "_images": [(b"\x89PNG", "image/png")]}, "u1")
    assert blocks[0]["type"] == "tool_result" and blocks[0]["tool_use_id"] == "u1"
    assert "_images" not in blocks[0]["content"]
    assert blocks[1] == {"type": "media", "mime": "image/png", "data": b"\x89PNG"}


def test_그림이_없으면_결과만():
    blocks = gemini.blocks_beside({"ok": True}, "u1")
    assert len(blocks) == 1
