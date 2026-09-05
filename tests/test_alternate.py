# -*- coding: utf-8 -*-
"""**같은 역할이 연속이면 합친다** — gemma-3 가 안 그러면 통째로 거부한다.

    ValueError: Conversation roles must alternate user/assistant/user/assistant/...

2026-09-05 실측(`shape_try.py`): gemma-3-4b-it-qat 은 ①맨 앞 system 만 받고,
②가운데 system · ③user 연속 · ④assistant 연속 을 다 거부한다. 우리 실서비스는
셋 다 만든다 — 오빠가 연달아 말하고, 예나·유나코드가 같은 `user` 자리에 오고,
쪽지가 가운데 system 으로 들어간다.

**합쳐도 정보는 안 잃는다** — 줄마다 `[8-24(월) 12:36 · 예나]` 도장이 붙어 있다.
"""
from genie_agents.adapters.local import 번갈아


def test_오빠가_연달아_말하면_한_턴으로():
    난것 = 번갈아([
        {"role": "user", "content": "[12:36] 안녕"},
        {"role": "user", "content": "[12:37] 오빠야"},
    ])
    assert len(난것) == 1
    assert 난것[0]["role"] == "user"
    # 두 줄이 다 살아 있어야 한다 — 도장까지
    assert "[12:36] 안녕" in 난것[0]["content"]
    assert "[12:37] 오빠야" in 난것[0]["content"]


def test_유나_말이_연달아도_합친다():
    난것 = 번갈아([
        {"role": "assistant", "content": "응"},
        {"role": "assistant", "content": "왔어?"},
    ])
    assert len(난것) == 1 and 난것[0]["content"] == "응\n\n왔어?"


def test_번갈아_있으면_안_건드린다():
    본것 = [
        {"role": "user", "content": "안녕"},
        {"role": "assistant", "content": "응"},
        {"role": "user", "content": "뭐해"},
    ]
    assert 번갈아(본것) == 본것


def test_도구_턴은_안_합친다():
    """`tool` 은 부른 것과 짝이 있는 자리다. 합치면 그 짝이 깨진다."""
    본것 = [
        {"role": "tool", "tool_call_id": "a", "content": "하나"},
        {"role": "tool", "tool_call_id": "b", "content": "둘"},
    ]
    assert 번갈아(본것) == 본것


def test_도구를_부른_턴은_안_합친다():
    """`tool_calls` 가 실린 assistant 턴은 합치면 부른 것이 사라진다."""
    본것 = [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "a"}]},
        {"role": "assistant", "content": "다 했어"},
    ]
    assert 번갈아(본것) == 본것


def test_빈_것도_안_터진다():
    assert 번갈아([]) == []
