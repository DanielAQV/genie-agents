"""보는 자리를 한 번 긁는다 — `agent.toml` 부터 원장까지.

조각마다 시험이 있어도(`test_slack.py` · `test_transcript.py` · `test_cursors.py`)
**이어 붙인 것이 도는지는 이어 붙여 봐야 안다.** 실제로 도는 방식이 단발
실행이라 여기가 그 한 번이다.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from genie_agents import clock, env
from genie_agents.channels import catchup
from genie_agents.channels import slack as slackmod
from genie_agents.cursors import Cursors
from genie_agents.spec import FILE, load
from genie_agents.transcript import ME, Book

T0 = datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc)
BASE = T0.timestamp()

TOML = """
[agent]
id = "scribe"
adapter = "anthropic"
timezone = "Asia/Seoul"

[prompt]
instructions = "prompt.md"

[watch]
slack = ["C0123"]
keep_hours = 72
"""


def folder(tmp_path, toml=TOML):
    d = tmp_path / "scribe"
    d.mkdir(exist_ok=True)
    (d / FILE).write_text(toml, encoding="utf-8")
    (d / "prompt.md").write_text("짧게 답한다.", encoding="utf-8")
    return d


@pytest.fixture
def clean(monkeypatch):
    monkeypatch.delenv(env.VAR, raising=False)
    monkeypatch.setenv("SLACK_USER_TOKEN", "xoxp-test")
    return monkeypatch


def serve(monkeypatch, **handlers):
    """`_urlopen` 을 갈아 끼운다 — `catchup` 이 안에서 `Slack(token)` 을 만들기
    때문에 여기가 유일하게 손이 닿는 자리다."""
    base = {"auth.test": lambda p: {"user_id": "U0ME", "url": "https://aqv.slack.com/"}}
    base.update(handlers)
    seen = []

    def fake(url, headers, timeout):
        head, _, query = url.partition("?")
        method = head.rsplit("/", 1)[-1]
        params = dict(
            (k, v) for k, _, v in (part.partition("=") for part in query.split("&") if query)
        )
        seen.append((method, params))
        return 200, {}, json.dumps({"ok": True, **base[method](params)}).encode("utf-8")

    monkeypatch.setattr(slackmod, "_urlopen", fake)
    return seen


def msg(ts: float, text: str, *, user="U7", thread="") -> dict:
    got = {"ts": f"{BASE + ts:.6f}", "text": text, "user": user}
    if thread:
        got["thread_ts"] = thread
    return got


def test_토큰_하나로_방을_긁어_원장에_쌓는다(clean, tmp_path):
    serve(clean, **{"conversations.history": lambda p: {
        "messages": [msg(100, "내가 볼게", user="U0ME"), msg(50, "<@U0ME> 이거 봐줄래?")],
        "has_more": False,
    }})
    got = catchup(load(folder(tmp_path)), at=T0)
    assert got == {"C0123": 2}

    book = Book(load(folder(tmp_path)).state_root)
    말 = book.lines("C0123")
    assert [x.text for x in 말] == ["<@U0ME> 이거 봐줄래?", "내가 볼게"]
    assert 말[1].who == ME                       # 본인 말이 이름으로 적혔다
    assert 말[0].mentions == ["U0ME"]            # 겹 1 의 "나를 멘션한 말"
    assert 말[0].url.startswith("https://aqv.slack.com/archives/C0123/p")


def test_두_번_긁어도_두_번_안_쌓인다(clean, tmp_path):
    """단발 실행이 겹치는 일은 실제로 있다 — 로그온 트리거와 매시 트리거가
    같은 분에 뜬다(`singleton` 이 막지만 막기 전에도 안전해야 한다)."""
    serve(clean, **{"conversations.history": lambda p: {
        "messages": [msg(100, "ㅇㅋ")], "has_more": False}})
    d = folder(tmp_path)
    assert catchup(load(d), at=T0) == {"C0123": 1}
    assert catchup(load(d), at=T0) == {"C0123": 0}
    assert len(Book(load(d).state_root)) == 1


def test_커서가_다음_번_창을_좁힌다(clean, tmp_path):
    seen = serve(clean, **{"conversations.history": lambda p: {
        "messages": [msg(100, "ㅇㅋ")], "has_more": False}})
    d = folder(tmp_path)
    catchup(load(d), at=T0)
    catchup(load(d), at=T0)
    묻는것 = [p.get("oldest") for m, p in seen if m == "conversations.history"]
    assert float(묻는것[0]) == pytest.approx(T0.timestamp() - 3 * 86400)   # 첫 날
    assert 묻는것[1] == f"{BASE + 100:.6f}"                                # 그 다음
    assert Cursors(load(d).state_root).get("slack:C0123") == f"{BASE + 100:.6f}"


def test_긁은_뒤에_창_밖을_버린다(clean, tmp_path):
    """★ §9 의 "원문은 따라잡기 창만" 이 `agent.toml` 의 값 하나로 도는 자리.
    **이 사람이 얼마를 남기기로 했는지가 기록**이 된다."""
    serve(clean, **{"conversations.history": lambda p: {
        "messages": [msg(0, "나흘 전 말")], "has_more": False}})
    d = folder(tmp_path)
    got = catchup(load(d), at=T0 + timedelta(days=4))
    assert got["_버린 원문"] == 1
    assert len(Book(load(d).state_root)) == 0


def test_짧게_남기기로_하면_짧게_남는다(clean, tmp_path):
    serve(clean, **{"conversations.history": lambda p: {
        "messages": [msg(0, "여섯 시간 전"), msg(60 * 60 * 5.5, "삼십 분 전")],
        "has_more": False}})
    d = folder(tmp_path, TOML.replace("keep_hours = 72", "keep_hours = 1"))
    catchup(load(d), at=T0 + timedelta(hours=6))
    assert [x.text for x in Book(load(d).state_root).lines()] == ["삼십 분 전"]


def test_토큰이_없으면_무엇이_없는지_말한다(clean, tmp_path):
    clean.delenv("SLACK_USER_TOKEN")
    with pytest.raises(RuntimeError, match="SLACK_USER_TOKEN"):
        catchup(load(folder(tmp_path)), at=T0)


def test_보는_자리가_없으면_아무것도_안_긁는다(clean, tmp_path):
    """읽기는 켜야 도는 것이다."""
    d = folder(tmp_path, TOML.split("[watch]")[0])
    assert catchup(load(d), at=T0) == {}


def test_막힌_방을_삼키지_않는다(clean, tmp_path):
    """★ 조용히 안 읽히는 방이 있으면 원장이 틀린 채로 자란다."""
    def history(p):
        raise KeyError  # 아래에서 갈아 끼운다

    seen = serve(clean, **{"conversations.history": history})

    def fake(url, headers, timeout):
        if "auth.test" in url:
            return 200, {}, json.dumps(
                {"ok": True, "user_id": "U0ME", "url": "https://aqv.slack.com/"}
            ).encode()
        return 200, {}, json.dumps({"ok": False, "error": "not_in_channel"}).encode()

    clean.setattr(slackmod, "_urlopen", fake)
    got = catchup(load(folder(tmp_path)), at=T0)
    assert "not_in_channel" in got["_막힌 방"]["C0123"]


def test_시각을_안_주면_지금으로_돈다(clean, tmp_path, monkeypatch):
    """`at` 은 시험이 미는 손잡이다. 실제로 돌 때는 안 준다."""
    clock.set_clock(lambda: T0)
    try:
        serve(clean, **{"conversations.history": lambda p: {
            "messages": [msg(0, "말")], "has_more": False}})
        assert catchup(load(folder(tmp_path))) == {"C0123": 1}
    finally:
        clock.set_clock(lambda: datetime.now(timezone.utc))
