"""`deploy/serve_local.py` ↔ `adapters/local.py` — 둘이 같은 말을 하나.

★ **GPU 도 모델도 없이 돈다.** 가중치를 올리는 부분만 갈아 끼우면 그 위의
  계약은 전부 볼 수 있다. 이 시험이 지키는 것은 성능이 아니라 **모양**이다 —
  서버와 어댑터를 따로 고치다 한쪽만 어긋나면, 그때 나는 고장은 "답이 이상하다"
  가 아니라 "왜 빈 답이 오지" 라서 찾는 데 오래 걸린다.

★ 진짜 소켓을 연다. 직렬화·HTTP 헤더·인코딩까지 지나가야 계약을 봤다고 할 수
  있다 — 한글이 오가는 자리라 인코딩이 실제로 틀릴 수 있는 자리다.
"""

from __future__ import annotations

import importlib.util
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from genie_agents.adapters import local

ROOT = Path(__file__).resolve().parents[1]


def _serve_local():
    """`deploy/` 는 패키지가 아니다. 경로로 읽는다 —
    **골격에 안 넣기로 한 것**이라 그 자리에 있다(호스트 쪽 도구)."""
    path = ROOT / "deploy" / "serve_local.py"
    spec = importlib.util.spec_from_file_location("serve_local", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def 붙은것(monkeypatch):
    """가짜 가중치로 진짜 서버를 띄우고, 어댑터를 거기에 붙인다."""
    sl = _serve_local()
    본것 = {}

    def 가짜(messages, max_tokens, temperature, tools=None):
        본것["messages"] = messages
        본것["max_tokens"] = max_tokens
        본것["temperature"] = temperature
        본것["tools"] = tools
        return {"text": '{"opens": [], "moves": [], "unresolved": []}',
                "in": 123, "out": 45, "finish": "stop"}

    sl.generate = 가짜
    sl.NAME = "가짜-4B"
    # ★ 프리픽스를 걷는다. 한 프로세스에 한 번 걸리면 안 풀려서(`env.use`),
    #   앞서 돈 시험이 정한 것을 물려받으면 `LOCAL_URL` 을 엉뚱한 이름으로
    #   찾는다 — 그러면 통과가 **순서에 딸리게** 된다(`env.py` 첫머리).
    from genie_agents import env

    monkeypatch.delenv(env.VAR, raising=False)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), sl.Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    port = srv.server_address[1]
    monkeypatch.setenv("LOCAL_URL", f"http://127.0.0.1:{port}/v1/chat/completions")
    try:
        yield local.LocalClient(f"http://127.0.0.1:{port}/v1/chat/completions"), 본것, sl, port
    finally:
        srv.shutdown()
        srv.server_close()


# ── 둘이 같은 말을 하나 ────────────────────────────────────────────
def test_어댑터가_보낸_것을_서버가_알아듣는다(붙은것):
    c, 본것, _, _ = 붙은것
    r = c.messages.create(model="m", max_tokens=256, system="지침",
                          messages=[{"role": "user", "content": "묶음"}])
    assert 본것["messages"] == [{"role": "system", "content": "지침"},
                              {"role": "user", "content": "묶음"}]
    assert 본것["max_tokens"] == 256
    assert r.content[0].text.startswith("{")
    assert r.usage.input_tokens == 123 and r.usage.output_tokens == 45
    assert r.stop_reason == "end_turn"


def test_한글이_오가도_안_깨진다(붙은것):
    """UTF-8 을 양쪽에서 못 박아 둔 자리다. 한 쪽만 어긋나면 묶음이
    통째로 뭉개져 모델에 들어가고, 그건 답을 보고서야 안다."""
    c, 본것, _, _ = 붙은것
    c.messages.create(model="m", max_tokens=8, system="한글 지침 · 베트남어 tiếng Việt",
                      messages=[{"role": "user", "content": "다 확인했어"}])
    assert 본것["messages"][0]["content"] == "한글 지침 · 베트남어 tiếng Việt"
    assert 본것["messages"][1]["content"] == "다 확인했어"


def test_길이로_끊긴_것이_끝까지_전해진다(붙은것):
    """서버의 `length` 가 어댑터에서 `max_tokens` 가 돼야 루프가 알아듣는다."""
    c, _, sl, _ = 붙은것
    sl.generate = lambda *a: {"text": "잘림", "in": 1, "out": 8, "finish": "length"}
    r = c.messages.create(model="m", max_tokens=8, messages=[{"role": "user", "content": "x"}])
    assert r.stop_reason == "max_tokens"


def test_서버가_죽으면_무엇이_없는지_말한다(붙은것):
    c, _, sl, _ = 붙은것

    def 터짐(*a):
        raise RuntimeError("CUDA out of memory")

    sl.generate = 터짐
    with pytest.raises(local.LocalUnavailable):
        c.messages.create(model="m", max_tokens=8, messages=[{"role": "user", "content": "x"}])


# ── 떠 있나 ────────────────────────────────────────────────────────
def test_available_가_진짜_포트를_본다(붙은것):
    """`check` 가 "안 떠 있다" 를 말할 때 근거가 되는 자리다."""
    _, _, _, port = 붙은것
    assert local.available() is True


def test_사람이_눌러_볼_자리도_있다(붙은것):
    """어댑터는 포트만 두드리지만, 사람은 무엇이 올라와 있는지 보고 싶다."""
    import json
    import urllib.request

    _, _, _, port = 붙은것
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as r:
        d = json.loads(r.read().decode("utf-8"))
    assert d["status"] == "ok" and d["model"] == "가짜-4B"


def test_없는_자리는_404(붙은것):
    import urllib.error
    import urllib.request

    _, _, _, port = 붙은것
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/embeddings", timeout=5)
    assert e.value.code == 404


# ── 도구 · 임베딩 (2026-09-01) ──────────────────────────────────────
#
# ★ **이 자리에 시험이 없어서 조용히 깨진 적이 있다.** `generate` 에 `tools` 를
#   더했는데 위 픽스처의 가짜가 인자 셋만 받아서 500 을 냈고, 그 커밋이 시험을
#   안 돌리고 나갔다. 서명이 갈리는 자리는 시험이 잡아야 한다.


def test_도구가_생성까지_내려간다(붙은것):
    c, 본것, _sl, _port = 붙은것
    도구 = {"name": "unseen_note", "description": "적는다",
            "input_schema": {"type": "object", "properties": {}}}
    c.messages.create(model="m", max_tokens=8, tools=[도구],
                      messages=[{"role": "user", "content": "x"}])

    보낸 = 본것["tools"]
    assert 보낸 and 보낸[0]["function"]["name"] == "unseen_note"


def test_부른_것을_tool_calls_로_돌려준다():
    """`<tool_call>` 은 특수 토큰이 아니라 글자다. 글에서 떼어내 싣는다."""
    sl = _serve_local()
    말, 부름 = sl.부른것(
        "음" + chr(10) + '<tool_call>{"name": "unseen_note", "arguments": {"text": "비"}}</tool_call>'
    )
    assert 말 == "음"
    assert 부름[0]["function"]["name"] == "unseen_note"
    assert json.loads(부름[0]["function"]["arguments"]) == {"text": "비"}


def test_망가진_JSON_은_글로_남는다():
    """4B 는 인자를 어긋나게 낼 때가 있다. 버리면 그 판이 아무 말 없이 끝난다."""
    sl = _serve_local()
    말, 부름 = sl.부른것("앞 <tool_call>{망가</tool_call> 뒤")
    assert 부름 == []
    assert "망가" in 말


def test_임베딩도_같은_구멍에서_난다(monkeypatch):
    """회상 임베딩을 이 기계 밖으로 안 내보내기로 했다(2026-09-01).
    카드가 6GB 라 프로세스를 안 늘리고 자물쇠를 나눠 쓴다."""
    import urllib.request

    sl = _serve_local()
    본것 = {}

    def 가짜(texts, model_id):
        본것["texts"], 본것["model"] = texts, model_id
        return [[0.1, 0.2, 0.3] for _ in texts]

    sl.embed = 가짜
    srv = ThreadingHTTPServer(("127.0.0.1", 0), sl.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/embeddings",
            data=json.dumps({"input": ["안녕", "잘 지내"]}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            got = json.loads(r.read().decode("utf-8"))
    finally:
        srv.shutdown()

    assert 본것["texts"] == ["안녕", "잘 지내"]
    assert [d["embedding"] for d in got["data"]] == [[0.1, 0.2, 0.3]] * 2
    assert [d["index"] for d in got["data"]] == [0, 1]
    # 토큰 수를 세는 값이 여기 없다. **0 을 보낸다** — 지어내면 값 기록이 틀린다.
    assert got["usage"]["total_tokens"] == 0
