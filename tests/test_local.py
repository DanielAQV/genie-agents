"""로컬 어댑터 — 이 기계 안에서 도는 모델.

★ **서버 없이 도는 시험이다.** 붙는 자리가 HTTP 한 겹이라, 그 한 겹을 갈아
  끼우면 llama.cpp 없이도 계약을 전부 볼 수 있다. 이 골격이 키 없이 도는
  자리를 남겨 두는 것과 같은 규칙이다.

여기서 지키는 것은 **루프가 보는 모양**이다. 루프는 모델이 무엇인지 모르고,
`adapters/base.py` 의 `Response` 하나만 본다 — 그 모양이 안 맞으면 로컬을
쓰는 에이전트만 조용히 다르게 행동한다.
"""

from __future__ import annotations

import json

import pytest

from genie_agents.adapters import local
from genie_agents.adapters.base import Client, Response, TextBlock


class Fake:
    """가짜 OpenAI 호환 서버. 보낸 몸통을 들고 있는다."""

    def __init__(self, answer="네", finish="stop", usage=None, boom=None, calls=None) -> None:
        self.answer, self.finish, self.boom = answer, finish, boom
        self.calls = calls  # 답에 실어 보낼 tool_calls
        self.usage = usage or {"prompt_tokens": 12, "completion_tokens": 3}
        self.sent = None

    def __call__(self, req, timeout=None):
        if self.boom:
            raise self.boom
        self.sent = json.loads(req.data.decode("utf-8"))

        class R:
            def __enter__(inner):
                return inner

            def __exit__(inner, *a):
                return False

            def read(inner):
                msg = {"content": self.answer}
                if self.calls is not None:
                    msg["tool_calls"] = self.calls
                return json.dumps({
                    "choices": [{"message": msg, "finish_reason": self.finish}],
                    "usage": self.usage,
                }).encode("utf-8")

        return R()


@pytest.fixture
def server(monkeypatch):
    f = Fake()
    monkeypatch.setattr(local.urllib.request, "urlopen", f)
    return f


def 클라(f, monkeypatch):
    monkeypatch.setattr(local.urllib.request, "urlopen", f)
    return local.client()


# ── 계약 ────────────────────────────────────────────────────────────
def test_루프가_보는_모양을_낸다(server):
    """어댑터가 파는 것은 이것 하나다 — 모델이 무엇이든 루프는 한 모양만 본다."""
    c = local.client()
    assert isinstance(c, Client)
    r = c.messages.create(model="m", max_tokens=64, messages=[{"role": "user", "content": "안녕"}])
    assert isinstance(r, Response)
    assert isinstance(r.content[0], TextBlock) and r.content[0].text == "네"
    assert r.stop_reason == "end_turn"
    assert r.usage.input_tokens == 12 and r.usage.output_tokens == 3


def test_캐시_칸은_영이다(server):
    """프롬프트 캐시가 없다. **없는 것을 0 으로 둔다** — 없애면 루프가 그
    자리를 잃고, 나중에 붙일 데가 없어진다(base.py)."""
    r = local.client().messages.create(model="m", max_tokens=8, messages=[{"role": "user", "content": "x"}])
    assert r.usage.cache_read_input_tokens == 0
    assert r.usage.cache_creation_input_tokens == 0


def test_길이로_끊긴_것을_앤트로픽_말로_옮긴다(monkeypatch):
    """루프는 `max_tokens` 라는 말을 본다. `length` 를 그대로 흘리면
    루프가 못 알아듣고, 잘린 답을 성한 답으로 센다."""
    c = 클라(Fake(finish="length"), monkeypatch)
    r = c.messages.create(model="m", max_tokens=8, messages=[{"role": "user", "content": "x"}])
    assert r.stop_reason == "max_tokens"


# ── 모양 옮기기 ────────────────────────────────────────────────────
def test_시스템이_첫_줄로_간다(server):
    local.client().messages.create(
        model="m", max_tokens=8, system="지침이다",
        messages=[{"role": "user", "content": "묶음"}])
    assert server.sent["messages"][0] == {"role": "system", "content": "지침이다"}
    assert server.sent["messages"][1]["content"] == "묶음"


def test_블록으로_온_시스템도_편다(server):
    """앤트로픽 쪽은 system 을 블록 목록으로 준다(`runner.Agent.system`)."""
    local.client().messages.create(
        model="m", max_tokens=8,
        system=[{"type": "text", "text": "정체성"}, {"type": "text", "text": "지침"}],
        messages=[{"role": "user", "content": "x"}])
    assert server.sent["messages"][0]["content"] == "정체성\n지침"


def test_도구는_넘겨도_안_나간다(server):
    """★ 무시한다고 첫머리에 적어 둔 것이다. 몰래 흘리면 뒤쪽 서버가
    엉뚱한 걸 받고, 그게 왜 이상한지 아무도 안 찾는다."""
    local.client().messages.create(
        model="m", max_tokens=8, tools=[{"name": "loop_open"}],
        messages=[{"role": "user", "content": "x"}])
    assert "tools" not in server.sent


def test_기본_온도가_낮다(server):
    """★ 여기가 하는 일은 정해진 모양의 JSON 하나를 내는 것이다.
    그 자리에서 다양성은 값이 아니라 고장이다."""
    local.client().messages.create(model="m", max_tokens=8,
                                   messages=[{"role": "user", "content": "x"}])
    assert server.sent["temperature"] <= 0.2


# ── 안 붙을 때 ─────────────────────────────────────────────────────
def test_안_떠_있으면_무엇이_없는지_말한다(monkeypatch):
    """"키가 없다" 가 아니라 "서버가 안 떠 있다" 여야 한다 — 고치는 길이 다르다."""
    c = 클라(Fake(boom=local.urllib.error.URLError("연결 거부")), monkeypatch)
    with pytest.raises(local.LocalUnavailable, match="llama.cpp"):
        c.messages.create(model="m", max_tokens=8, messages=[{"role": "user", "content": "x"}])


def test_빈_답을_성한_답으로_안_센다(monkeypatch):
    class Empty(Fake):
        def __call__(self, req, timeout=None):
            class R:
                def __enter__(inner):
                    return inner

                def __exit__(inner, *a):
                    return False

                def read(inner):
                    return b'{"choices": []}'

            return R()

    c = 클라(Empty(), monkeypatch)
    with pytest.raises(local.LocalUnavailable, match="빈 답"):
        c.messages.create(model="m", max_tokens=8, messages=[{"role": "user", "content": "x"}])


def test_떠_있나를_포트로_묻는다(monkeypatch):
    """키가 아니라 프로세스다. `check` 가 이걸 1초 안에 물어야 한다."""
    monkeypatch.setattr(local.socket, "create_connection",
                        lambda *a, **k: (_ for _ in ()).throw(OSError()))
    assert local.available() is False


# ── 어디를 가리키나 ────────────────────────────────────────────────
@pytest.fixture
def 맨몸(monkeypatch):
    """프리픽스를 걷는다. 한 프로세스에 한 번 걸리면 안 풀려서(`env.use`),
    앞서 돈 시험이 정한 것을 이 시험이 물려받는다 — 그러면 통과가
    **순서에 딸리게** 된다. 저장소가 `env.py` 첫머리에 적어 둔 자리다."""
    from genie_agents import env

    monkeypatch.delenv(env.VAR, raising=False)
    return monkeypatch


def test_기본은_로컬호스트다(맨몸):
    """★ 팀원의 글이 기계 밖으로 안 나가게 하려고 있는 어댑터다.
    다른 데를 가리키려면 **손으로 적어야 하고**, 적는 순간 그건 결정이 된다."""
    맨몸.delenv("LOCAL_URL", raising=False)
    assert local.url().startswith("http://127.0.0.1")


def test_적으면_거기로_간다(맨몸):
    맨몸.setenv("LOCAL_URL", "http://192.168.0.9:9999/v1/chat/completions")
    assert local.url().startswith("http://192.168.0.9")


# ── 도구 (2026-09-01) ───────────────────────────────────────────────
#
# 예전엔 `tools` 를 넘겨도 안 썼다. 유나의 일상 자리를 이리로 내리려니
# 필요해졌다 — 그 자리마저 답을 `unseen_note` / `unseen_pass` 로 낸다.

WRITE = {
    "name": "unseen_note",
    "description": "든 생각을 남긴다",
    "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}},
}


def test_도구_목록을_옮겨_보낸다(monkeypatch):
    f = Fake()
    c = 클라(f, monkeypatch)
    c.messages.create(model="m", max_tokens=8, tools=[WRITE],
                      messages=[{"role": "user", "content": "x"}])

    보낸 = f.sent["tools"][0]
    assert 보낸["type"] == "function"
    assert 보낸["function"]["name"] == "unseen_note"
    assert 보낸["function"]["parameters"] == WRITE["input_schema"]


def test_스키마_없는_서버도구는_안_보낸다(monkeypatch):
    """`{"type": "web_search_..."}` 같은 것. 로컬엔 그런 게 없고, 보내면 400 이다."""
    f = Fake()
    c = 클라(f, monkeypatch)
    c.messages.create(model="m", max_tokens=8,
                      tools=[{"type": "web_search_20250305", "name": "web_search"}],
                      messages=[{"role": "user", "content": "x"}])
    assert "tools" not in f.sent


def test_부른_것이_루프_모양으로_온다(monkeypatch):
    from genie_agents.adapters.base import ToolUseBlock

    f = Fake(answer="", calls=[
        {"id": "call_1", "type": "function",
         "function": {"name": "unseen_note", "arguments": '{"text": "비 온다"}'}},
    ])
    r = 클라(f, monkeypatch).messages.create(
        model="m", max_tokens=8, tools=[WRITE], messages=[{"role": "user", "content": "x"}])

    블록 = [b for b in r.content if isinstance(b, ToolUseBlock)]
    assert len(블록) == 1
    assert (블록[0].name,블록[0].input, 블록[0].id) == ("unseen_note", {"text": "비 온다"}, "call_1")
    assert r.stop_reason == "tool_use"


def test_부른_것이_있으면_그게_멈춘_이유다(monkeypatch):
    """`finish_reason` 을 "stop" 으로 내면서 tool_calls 를 같이 싣는 서버가 있다.
    실린 것을 믿는다 — 안 그러면 루프가 도구를 안 돌리고 끝낸다."""
    f = Fake(finish="stop", calls=[
        {"id": "c", "function": {"name": "unseen_pass", "arguments": "{}"}},
    ])
    r = 클라(f, monkeypatch).messages.create(
        model="m", max_tokens=8, tools=[WRITE], messages=[{"role": "user", "content": "x"}])
    assert r.stop_reason == "tool_use"


def test_인자를_못_읽어도_안_죽는다(monkeypatch):
    """작은 모델은 인자를 어긋나게 낼 때가 있다. 여기서 죽으면 그 판이 통째로
    끝난다 — 빈 것으로 부르고, 판단은 도구 쪽에서 한다."""
    f = Fake(calls=[{"id": "c", "function": {"name": "unseen_note", "arguments": "{망가"}}])
    r = 클라(f, monkeypatch).messages.create(
        model="m", max_tokens=8, tools=[WRITE], messages=[{"role": "user", "content": "x"}])
    assert r.content[-1].input == {}


def test_도구_결과는_따로_선_한_턴이다(monkeypatch):
    """★ **여기가 제일 조용히 고장난다.** 결과를 글로 뭉개도 요청은 200 이고,
    모델은 자기가 부른 도구가 무엇을 냈는지 모른 채 답한다."""
    from genie_agents.adapters.base import ToolUseBlock

    f = Fake()
    클라(f, monkeypatch).messages.create(
        model="m", max_tokens=8, tools=[WRITE],
        messages=[
            {"role": "user", "content": "오늘 뭐 봤어"},
            {"role": "assistant", "content": [
                TextBlock(text="음"),
                ToolUseBlock(id="c1", name="unseen_note", input={"text": "비"}),
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "c1", "content": '{"ok": true}'},
            ]},
        ],
    )

    보낸 = f.sent["messages"]
    assert [m["role"] for m in 보낸] == ["user", "assistant", "tool"]
    assert 보낸[1]["content"] == "음"
    assert 보낸[1]["tool_calls"][0]["function"]["name"] == "unseen_note"
    assert json.loads(보낸[1]["tool_calls"][0]["function"]["arguments"]) == {"text": "비"}
    assert 보낸[2]["tool_call_id"] == "c1"
    assert 보낸[2]["content"] == '{"ok": true}'


def test_도구를_안_주면_아무것도_안_바뀐다(monkeypatch):
    """추출 자리는 예전 그대로다 — 도구 칸이 아예 안 실린다."""
    f = Fake()
    클라(f, monkeypatch).messages.create(
        model="m", max_tokens=8, messages=[{"role": "user", "content": "x"}])
    assert "tools" not in f.sent
    assert f.sent["messages"] == [{"role": "user", "content": "x"}]
