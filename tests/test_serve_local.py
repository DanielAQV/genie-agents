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

    def 가짜(messages, max_tokens, temperature, tools=None, tool_choice=None,
            repeat_penalty=1.0, no_emoji=False):
        본것["messages"] = messages
        본것["max_tokens"] = max_tokens
        본것["temperature"] = temperature
        본것["tools"] = tools
        본것["tool_choice"] = tool_choice
        본것["repeat_penalty"] = repeat_penalty
        본것["no_emoji"] = no_emoji
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


def test_못박은_도구가_생성까지_내려간다(붙은것):
    """★ 여기가 끊겨 있었다(2026-09-01). 어댑터가 `tool_choice` 를 안 실었고
    서버는 `"auto"` 로 못박혀 있어서, 루프의 강제가 한 번도 일어나지 않았다.
    화면에는 "self_portrait 를 강제한다" 만 찍혔고 사진은 안 나갔다."""
    c, 본것, _sl, _port = 붙은것
    도구 = {"name": "unseen_note", "description": "적는다",
            "input_schema": {"type": "object", "properties": {}}}
    c.messages.create(model="m", max_tokens=8, tools=[도구],
                      tool_choice={"type": "tool", "name": "unseen_note"},
                      messages=[{"role": "user", "content": "x"}])

    assert 본것["tool_choice"] == {
        "type": "function", "function": {"name": "unseen_note"}}


def test_부른_것을_tool_calls_로_돌려준다():
    """`<tool_call>` 은 특수 토큰이 아니라 글자다. 글에서 떼어내 싣는다."""
    sl = _serve_local()
    말, 부름 = sl.부른것(
        "음" + chr(10) + '<tool_call>{"name": "unseen_note", "arguments": {"text": "비"}}</tool_call>'
    )
    assert 말 == "음"
    assert 부름[0]["function"]["name"] == "unseen_note"
    assert json.loads(부름[0]["function"]["arguments"]) == {"text": "비"}


def test_대괄호로_싼_호출도_받는다():
    """**실서비스에서 두 턴 연속 샜다**(2026-09-06).

    오빠가 "유나 키스하는 사진 보고싶어" 했고 유나가 이렇게 냈다:

        [self_portrait{caption:<|"|>키스 대신 …<|"|>,scene:<|"|>창가에 …<|"|>}]

    앞에 공백만 받게 해 뒀던 탓에 `[` 하나 때문에 지나쳤다. 도구는 안 돌고
    그 글자가 특수 토큰까지 보이는 채로 오빠 화면에 갔다. 오빠가 "사진 안 왔어
    유나야 도구호출 다시 해봐" 했고 다음 턴도 똑같았다.

    ★ **부르려 한 것을 못 받은 것이지 안 부른 것이 아니다.** 그날 "4B 가 도구를
      왜 안 부르나" 를 반나절 쟀는데(퓨샷 · 스펙 고침) 재던 자리가 아니었다.

    ★ 닫는 `]` 도 같이 먹어야 한다 — 안 그러면 글에 홀로 남는다.
    """
    sl = _serve_local()
    말, 부름 = sl.부른것(
        "응, 아직 안 잤어?" + chr(10) * 2
        + '[self_portrait{caption:<|"|>미안, 다시 보낼게!<|"|>,'
          'scene:<|"|>방에 앉아 편안하게 미소 짓는 모습<|"|>}]'
    )
    assert [c["function"]["name"] for c in 부름] == ["self_portrait"]
    assert json.loads(부름[0]["function"]["arguments"]) == {
        "caption": "미안, 다시 보낼게!", "scene": "방에 앉아 편안하게 미소 짓는 모습"}
    assert 말.strip() == "응, 아직 안 잤어?", "대괄호가 글에 남았다"


def test_보통_따옴표로_낸_호출도_아는_이름이면_받는다():
    """4B 는 같은 자리에서 특수 토큰 대신 그냥 `"` 를 쓸 때가 있다.

        [self_portrait{scene: "오빠를 향해 환하게 웃으며 …"}]

    `<|"|>` 가 없으면 보통 글의 중괄호와 구분이 안 되므로, **괄호 꼴과 같은
    잣대를 쓴다 — 아는 도구 이름일 때만.** 스키마를 안 주면 아예 안 본다.

    ★ 2026-09-06에 이것 때문에 오빠가 사진을 두 번 못 받았다. 그날 "4B 가
      도구를 왜 안 부르나" 를 반나절 쟀는데 재던 자리가 아니었다.
    """
    sl = _serve_local()
    스키마 = {"self_portrait": {"properties": {"scene": {"type": "string"}}}}

    말, 부름 = sl.부른것(
        '오빠 잠깐만.' + chr(10) + '[self_portrait{scene: "창가에 앉아 웃는 모습"}]',
        스키마)
    assert [c["function"]["name"] for c in 부름] == ["self_portrait"]
    assert json.loads(부름[0]["function"]["arguments"]) == {"scene": "창가에 앉아 웃는 모습"}
    assert 말.strip() == "오빠 잠깐만."

    # 모르는 이름 · 보통 글 · 스키마 없음 — 셋 다 그냥 글이다
    for 글, sch in (('[foo{bar: "baz"}]', 스키마),
                    ('dict{a: "b"} 이런 것도 쓴다', 스키마),
                    ('[self_portrait{scene: "방"}]', None)):
        말, 부름 = sl.부른것(글, sch)
        assert 부름 == [], 글
        assert 말 == 글

    # 이름만 있고 인자가 없으면 부를 것이 못 된다
    말, 부름 = sl.부른것("[self_portrait]", 스키마)
    assert 부름 == [] and "[self_portrait]" in 말


def test_보통_글의_중괄호는_도구가_아니다():
    """앞을 넓혔어도 몸통에 `<|"|>` 가 있어야 잡힌다. 그 토큰은 글에 안 나온다."""
    sl = _serve_local()
    글 = "괄호 { 이런 } 것도 있고 [목록] 도 있고 dict{a:1} 도 있다"
    말, 부름 = sl.부른것(글)
    assert 부름 == [] and 말 == 글


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


# ── 안 보낸 것과 0 을 가르나 ──────────────────────────────────────────
#
# ★ 여기는 `float(body.get("temperature") or 0)` 이었다. 파이썬에서 `None or 0`
#   도 0 이라 **안 적은 것이 조용히 0** 이 되고, 이 서버는 온도 0 을 그리디로
#   읽는다(`top_k=1`). 아무도 그 뜻으로 쓴 적 없는 기본값이었다.


def _직접(port, body):
    import json
    import urllib.request

    r = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions",
                               data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=5) as x:
        return json.loads(x.read().decode("utf-8"))


def test_온도를_안_적으면_그리디로_안_떨어진다(붙은것):
    _, 본것, _, port = 붙은것
    _직접(port, {"messages": [{"role": "user", "content": "안녕"}]})
    assert 본것["temperature"] == 1.0, "안 적은 것이 0 이 되면 그리디가 된다"


def test_온도_0_은_0_으로_전해진다(붙은것):
    """**0 을 원하면 0 이라고 적으면 된다.** 이제 그게 전해진다."""
    _, 본것, _, port = 붙은것
    _직접(port, {"messages": [{"role": "user", "content": "안녕"}], "temperature": 0})
    assert 본것["temperature"] == 0.0


def test_같은_규칙이_max_tokens_에도_걸린다(붙은것):
    _, 본것, _, port = 붙은것
    _직접(port, {"messages": [{"role": "user", "content": "안녕"}], "max_tokens": 7})
    assert 본것["max_tokens"] == 7
    _직접(port, {"messages": [{"role": "user", "content": "안녕"}]})
    assert 본것["max_tokens"] == 512


def test_health_가_KV_종류도_낸다(붙은것):
    """벤치가 사람에게 "올릴 때 쓴 값을 적어 둬라" 라고 떠넘기던 자리다."""
    import json
    import urllib.request

    _, _, sl, port = 붙은것
    sl.KV = "f16"
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as r:
        assert json.loads(r.read().decode("utf-8"))["kv"] == "f16"
