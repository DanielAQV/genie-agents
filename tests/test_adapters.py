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
    # 버리는 것만 적으면 "그럼 나머지는 다 도나" 로 읽는다.
    assert "tool_choice" in 머리, "옮기는 것도 적어야 한다"


def test_강제_호출은_버리지_않고_옮긴다():
    """★ 이게 조용히 안 걸리고 있었다.

    루프의 `Policy.force_first` 는 `tool_choice` 로 온다. Gemini 어댑터가 그걸
    `**ignored` 로 흘려버려서, 예나의 "사진이 오면 image_note 를 반드시 불러라"
    가 **한 번도 안 걸렸다**(2026-08-31에 알았다). 부르는 쪽은 걸린 줄 알았다.

    버릴 것과 옮길 것을 가르는 기준은 "저쪽에 같은 말이 있나" 이지 "우리가 안
    쓰나" 가 아니다. Gemini 에는 `tool_config` 가 있다.
    """
    from genie_agents.adapters.gemini import tool_config

    cfg = tool_config({"type": "tool", "name": "voice_reply"})["function_calling_config"]
    assert cfg["mode"] == "ANY"
    assert cfg["allowed_function_names"] == ["voice_reply"]

    # 이름 없이 "아무거나 하나는 불러라"
    assert tool_config({"type": "any"})["function_calling_config"] == {"mode": "ANY"}
    assert tool_config({"type": "auto"})["function_calling_config"]["mode"] == "AUTO"


def test_모르는_모양이면_아예_안_건다():
    """넘겨짚어 엉뚱한 것을 강제하느니 안 거는 편이 낫다 — 안 걸린 것은
    로그에 남지만, 엉뚱하게 걸린 것은 답만 이상해지고 어디에도 안 남는다."""
    from genie_agents.adapters.gemini import tool_config

    assert tool_config(None) is None
    assert tool_config("voice_reply") is None
    assert tool_config({"type": "처음보는것"}) is None


def test_루프는_어느_클라이언트든_받는다():
    """`Client` 는 구조로만 본다 — `messages.create` 가 있으면 된다."""

    class 아무거나:
        class messages:
            @staticmethod
            def create(**kw):
                return Response(content=[TextBlock("응")])

    assert isinstance(아무거나(), Client)
