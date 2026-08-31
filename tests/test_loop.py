"""도구 루프 — 갈리는 자리가 값으로 올라와 있는지.

여기서 제일 중요한 시험은 `test_기본값끼리는_똑같이_작동한다` 다. 골격을 나눠
쓰는 사람이 아무것도 안 정했을 때 서로 다르게 돌면, "이 골격은 이렇게 작동한다"
를 아무도 약속할 수 없다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from genie_agents import loop
from genie_agents.policy import DEFAULT, Policy


# ── 흉내 ─────────────────────────────────────────────────────────────
@dataclass
class Text:
    text: str
    type: str = "text"


@dataclass
class Use:
    id: str
    name: str
    input: dict
    type: str = "tool_use"


@dataclass
class Usage:
    input_tokens: int = 10
    output_tokens: int = 5
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class Resp:
    content: list
    stop_reason: str = "end_turn"
    usage: Usage = field(default_factory=Usage)


class FakeClient:
    """정해 둔 응답을 차례로 내놓는다."""

    def __init__(self, *responses):
        self.queue = list(responses)
        self.seen = []
        self.messages = self

    def create(self, **kw):
        self.seen.append(kw)
        return self.queue.pop(0) if self.queue else Resp([Text("끝")])


class FakeSession:
    def __init__(self, results=None):
        self.results = results or {}
        self.called = []

    def tools(self, scope):
        return [{"name": "look", "description": "", "input_schema": {"type": "object"}}]

    def call(self, name, **args):
        self.called.append((name, args))
        return self.results.get(name, {"ok": True})


# ── 기본값 ───────────────────────────────────────────────────────────


def test_기본값끼리는_똑같이_작동한다():
    """아무것도 안 정한 둘이 같은 입력에 같은 답을 내야 한다.
    아니면 골격이 약속할 수 있는 게 없다."""
    답 = lambda: [Resp([Text("안녕")])]  # noqa: E731

    가 = loop.run(FakeClient(*답()), FakeSession(), [{"role": "user", "content": "야"}],
                  model="m", policy=DEFAULT)
    나 = loop.run(FakeClient(*답()), FakeSession(), [{"role": "user", "content": "야"}],
                  model="m", policy=Policy())

    assert 가.text == 나.text == "안녕"
    assert 가.requests == 나.requests
    assert 가.stop_reason == 나.stop_reason


# ── 갈리는 자리들 ────────────────────────────────────────────────────


def test_도구를_부르는_턴의_글은_기본으로_버린다():
    """도구 부르기 직전에 붙는 혼잣말이다. 실제로 그런 줄이 사람에게 나갔다."""
    c = FakeClient(
        Resp([Text("찾아볼게"), Use("1", "look", {})], stop_reason="tool_use"),
        Resp([Text("찾아봤어")]),
    )
    t = loop.run(c, FakeSession(), [{"role": "user", "content": "야"}], model="m")
    assert t.text == "찾아봤어"


def test_남기기로_하면_남는다():
    c = FakeClient(
        Resp([Text("찾아볼게"), Use("1", "look", {})], stop_reason="tool_use"),
        Resp([Text("찾아봤어")]),
    )
    t = loop.run(c, FakeSession(), [{"role": "user", "content": "야"}], model="m",
                 policy=Policy(keep_text_with_tool_call=True))
    assert t.text == "찾아봤어"  # 마지막이 덮는다
    assert c.seen  # 그래도 두 번 돌았다


def test_다듬는_손이_걷어낸_것을_남긴다():
    def 지운다(s):
        return s.replace("[가짜]", "").strip(), (["[가짜]"] if "[가짜]" in s else [])

    c = FakeClient(Resp([Text("봤어 [가짜]")]))
    t = loop.run(c, FakeSession(), [{"role": "user", "content": "야"}], model="m",
                 policy=Policy(sanitizers=(지운다,)))
    assert t.text == "봤어"
    assert t.dropped == ["[가짜]"]


def test_통째로_걷어내지면_한_번만_다시_묻는다():
    def 전부(s):
        return "", [s]

    c = FakeClient(Resp([Text("가짜뿐")]), Resp([Text("진짜 답")]))
    t = loop.run(c, FakeSession(), [{"role": "user", "content": "야"}], model="m",
                 policy=Policy(sanitizers=(전부,)))
    assert t.requests == 2, "한 번은 다시 물어야 한다"


def test_다시_묻기를_끄면_안_묻는다():
    def 전부(s):
        return "", [s]

    c = FakeClient(Resp([Text("가짜뿐")]), Resp([Text("진짜 답")]))
    t = loop.run(c, FakeSession(), [{"role": "user", "content": "야"}], model="m",
                 policy=Policy(sanitizers=(전부,), retry_when_empty=False))
    assert t.requests == 1


def test_글이_남아도_걷어낸_것이_있으면_알려주고_다시_묻는다():
    """**떼기만 하면 모자란다.** 표시는 사라져도 "짠! 여기 있어" 는 남아서,
    받는 쪽에서는 보낸다고 해놓고 안 온 것이 된다. 무엇이 일어났는지 알려주면
    이번엔 도구를 부를 수도 있다."""
    def 표시만(s):
        return s.replace("[가짜]", "").strip(), (["[가짜]"] if "[가짜]" in s else [])

    c = FakeClient(Resp([Text("짠! 여기 있어 [가짜]")]), Resp([Text("다시 쓴 답")]))
    msgs = [{"role": "user", "content": "야"}]
    t = loop.run(c, FakeSession(), msgs, model="m",
                 policy=Policy(sanitizers=(표시만,), retry_note="그건 네가 적는 게 아니다"))

    assert t.requests == 2, "한 번은 다시 물어야 한다"
    assert t.text == "다시 쓴 답"
    # 캐시 경계가 마지막 사용자 글을 블록으로 다시 싸므로 글자로 확인한다.
    assert msgs[-1]["role"] == "user"
    assert "그건 네가 적는 게 아니다" in str(msgs[-1]["content"])


def test_알려줄_말이_없으면_안_묻는다():
    """`retry_note` 가 비어 있으면 지금까지처럼 조용히 떼기만 한다."""
    def 표시만(s):
        return s.replace("[가짜]", "").strip(), (["[가짜]"] if "[가짜]" in s else [])

    c = FakeClient(Resp([Text("짠! 여기 있어 [가짜]")]), Resp([Text("안 불려야 한다")]))
    t = loop.run(c, FakeSession(), [{"role": "user", "content": "야"}], model="m",
                 policy=Policy(sanitizers=(표시만,)))
    assert t.requests == 1 and t.text == "짠! 여기 있어"


def test_다시_묻는_것은_한_번뿐이다():
    """`retry_when_empty` 와 **한 번을 나눠 쓴다.** 두 번 이상 걸리면 여기서
    풀 문제가 아니고, 재시도가 그만큼 값을 태운다."""
    def 표시만(s):
        return s.replace("[가짜]", "").strip(), (["[가짜]"] if "[가짜]" in s else [])

    c = FakeClient(Resp([Text("한 번 [가짜]")]), Resp([Text("두 번 [가짜]")]),
                   Resp([Text("세 번")]))
    t = loop.run(c, FakeSession(), [{"role": "user", "content": "야"}], model="m",
                 policy=Policy(sanitizers=(표시만,), retry_note="그건 네가 적는 게 아니다"))
    assert t.requests == 2 and t.text == "두 번"


def test_판단_도구가_불리면_거기서_끝낸다():
    c = FakeClient(
        Resp([Use("1", "decide", {})], stop_reason="tool_use"),
        Resp([Text("안 불려야 한다")]),
    )
    t = loop.run(c, FakeSession(), [{"role": "user", "content": "야"}], model="m",
                 policy=Policy(decision_tools=frozenset({"decide"})))
    assert t.decided and t.requests == 1


def test_막힌_도구는_판단이_아니다():
    """부른 것과 선 것은 다르다. 막힌 것을 판단으로 치면 그 자리는 판단 없이 끝난다."""
    c = FakeClient(
        Resp([Use("1", "decide", {})], stop_reason="tool_use"),
        Resp([Text("그래서 다시 판단한다")]),
    )
    s = FakeSession({"decide": {"blocked": "지금은 막혔다"}})
    t = loop.run(c, s, [{"role": "user", "content": "야"}], model="m",
                 policy=Policy(decision_tools=frozenset({"decide"})))
    assert t.blocked == ["decide"]
    assert not t.decided
    assert t.requests == 2, "막혔으면 계속 돌아야 한다"


def test_도구가_던져도_루프는_안_끊긴다():
    """결과를 보고 판단하게 둔다."""
    class 터진다(FakeSession):
        def call(self, name, **args):
            raise RuntimeError("잔고 없음")

    c = FakeClient(
        Resp([Use("1", "look", {})], stop_reason="tool_use"),
        Resp([Text("그렇구나")]),
    )
    t = loop.run(c, 터진다(), [{"role": "user", "content": "야"}], model="m")
    assert t.text == "그렇구나"
    보낸_것 = c.seen[-1]["messages"][-1]["content"][0]
    assert 보낸_것["is_error"] and "잔고 없음" in 보낸_것["content"]


def test_첫_요청에만_도구를_강제한다():
    c = FakeClient(
        Resp([Use("1", "look", {})], stop_reason="tool_use"),
        Resp([Text("적었어")]),
    )
    loop.run(c, FakeSession(), [{"role": "user", "content": "야"}], model="m",
             policy=Policy(force_first="look"))
    assert c.seen[0]["tool_choice"] == {"type": "tool", "name": "look"}
    assert "tool_choice" not in c.seen[1], "한 번만 걸리고 풀려야 한다"


def test_max_turns_에서_끊는다():
    끝없이 = [Resp([Use(str(i), "look", {})], stop_reason="tool_use") for i in range(20)]
    t = loop.run(FakeClient(*끝없이), FakeSession(), [{"role": "user", "content": "야"}],
                 model="m", policy=Policy(max_turns=3))
    assert t.requests == 3


# ── 캐시 경계 ────────────────────────────────────────────────────────


def test_캐시_경계는_옮기는_것이지_더하는_것이_아니다():
    msgs = [{"role": "user", "content": "하나"}]
    edge = loop.move_cache_edge(msgs, None)
    msgs.append({"role": "assistant", "content": "응"})
    msgs.append({"role": "user", "content": "둘"})
    loop.move_cache_edge(msgs, edge)

    붙은_것 = [m for m in msgs if isinstance(m["content"], list)
              and m["content"] and "cache_control" in m["content"][-1]]
    assert len(붙은_것) == 1, "경계는 하나여야 한다"
    assert 붙은_것[0]["content"][-1]["text"] == "둘"


def test_붙일_자리가_없으면_조용히_넘어간다():
    """캐시가 안 걸린다고 판단이 멈출 이유는 없다."""
    assert loop.move_cache_edge([{"role": "assistant", "content": "응"}], None) is None
    assert loop.move_cache_edge([], None) is None


def test_경계를_끄면_아무것도_안_붙는다():
    msgs = [{"role": "user", "content": "하나"}]
    loop.run(FakeClient(Resp([Text("응")])), FakeSession(), msgs, model="m",
             policy=Policy(move_cache_edge=False))
    assert msgs[0]["content"] == "하나", "손 안 댔어야 한다"


def test_값은_여기서_안_센다():
    """모델마다 값 매기는 방식이 다르다. 토큰만 세고 환산은 쓰는 쪽이 한다."""
    t = loop.run(FakeClient(Resp([Text("응")])), FakeSession(),
                 [{"role": "user", "content": "야"}], model="m")
    assert t.input_tokens == 10 and t.output_tokens == 5
    assert not hasattr(t, "cost")
