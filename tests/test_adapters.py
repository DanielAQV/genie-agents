"""어댑터 — 어느 모델이든 루프가 보는 모양은 하나다."""

from __future__ import annotations

import pytest

from genie_agents import env
from genie_agents.adapters import Client, Response, TextBlock, ToolUseBlock, Usage


@pytest.fixture(autouse=True)
def _prefix(monkeypatch):
    monkeypatch.setenv(env.VAR, "SCRIBE")
    for k in ("SCRIBE_MODEL", "SCRIBE_FAST_MODEL"):
        monkeypatch.delenv(k, raising=False)


def test_응답_모양은_한_가지다():
    """루프가 읽는 것은 셋뿐이다 — content · stop_reason · usage."""
    r = Response(content=[TextBlock("안녕")], stop_reason="end_turn", usage=Usage())
    assert r.content[0].type == "text"
    assert r.stop_reason and r.usage.input_tokens == 0

    u = ToolUseBlock(id="1", name="look", input={})
    assert u.type == "tool_use"


def test_모델은_프리픽스로_갈아끼운다(monkeypatch):
    """한 호스트에 에이전트가 여럿 산다. 서로의 설정을 안 밟아야 한다."""
    from genie_agents.adapters import anthropic, gemini

    assert anthropic.default_model() == anthropic.DEFAULT_MODEL
    assert gemini.default_model() == gemini.DEFAULT_MODEL

    monkeypatch.setenv("SCRIBE_MODEL", "고른-것")
    assert anthropic.default_model() == "고른-것"
    assert gemini.default_model() == "고른-것"

    # 다른 에이전트의 설정은 안 본다.
    monkeypatch.setenv("WARDEN_MODEL", "남의-것")
    assert anthropic.default_model() == "고른-것"


def test_빠른_자리는_따로_고를_수_있다(monkeypatch):
    from genie_agents.adapters import gemini

    assert gemini.default_model(fast=True) == gemini.DEFAULT_FAST_MODEL
    monkeypatch.setenv("SCRIBE_FAST_MODEL", "싼-것")
    assert gemini.default_model(fast=True) == "싼-것"


def test_SDK_가_없어도_골격은_뜬다():
    """늦게 import 한다. 없으면 그 어댑터를 부를 때만 죽는다."""
    from genie_agents.adapters import anthropic, gemini

    assert callable(anthropic.client) and callable(gemini.default_model)
    assert issubclass(anthropic.AnthropicUnavailable, RuntimeError)
    assert issubclass(gemini.GeminiUnavailable, RuntimeError)


def test_어댑터가_무엇을_버리는지_적혀_있다():
    """무시하는 것을 안 적으면 도는 줄 알고 유지보수한다."""
    import pathlib

    from genie_agents.adapters import gemini

    src = pathlib.Path(gemini.__file__).read_text(encoding="utf-8")
    머리 = src[: src.index('"""', src.index('"""') + 3)]
    assert "cache_control" in 머리 and "무시" in 머리
    assert "move_cache_edge" in 머리, "정책 이름으로 적어야 찾는다"


def test_루프는_어느_클라이언트든_받는다():
    """`Client` 는 구조로만 본다 — `messages.create` 가 있으면 된다."""

    class 아무거나:
        class messages:
            @staticmethod
            def create(**kw):
                return Response(content=[TextBlock("응")])

    assert isinstance(아무거나(), Client)
