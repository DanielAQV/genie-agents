"""Slack 어댑터 — 보는 자리. 읽기만 한다.

**토큰 없이 도는 시험이다.** `check` 가 키 없이 도는 것과 같은 이유다 —
자격 증명이 없는 자리에서 배선을 못 고치면, 고칠 수 있는 자리가 회사 PC
한 대뿐이 된다.

여기 있는 것 대부분은 *조용히 새는 길*을 누른다. Slack 읽기가 고장 나는 방식은
예외를 던지는 것이 아니라 **덜 읽고 다 읽은 척하는 것**이다.
"""

from __future__ import annotations

import json
import urllib.parse
from datetime import datetime, timedelta, timezone

import pytest

from genie_agents.channels.slack import (
    MAX_PAGES,
    Slack,
    SlackError,
    SlackWatch,
    key_of,
    parse_key,
    permalink,
    stamp,
)
from genie_agents.cursors import Cursors
from genie_agents.transcript import ME, Book

T0 = datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc)
TEAM = "https://aqv.slack.com/"
BASE = T0.timestamp()   # 말 ts 의 기준. 시험이 "지금" 근처에서 돈다


class Fake:
    """가짜 Slack. `handlers[method](params)` 가 답을 낸다.

    답은 dict(그대로 `ok:true` 로 나간다) 또는 `(status, headers, dict)`.
    """

    def __init__(self, **handlers) -> None:
        self.handlers = {
            "auth.test": lambda p: {"user_id": "U_ME", "url": TEAM},
            **handlers,
        }
        self.seen: list[tuple[str, dict]] = []
        self.slept: list[float] = []

    def http(self, url, headers, timeout):
        head, _, query = url.partition("?")
        method = head.rsplit("/", 1)[-1]
        params = {}
        for part in query.split("&") if query else []:
            k, _, v = part.partition("=")
            params[k] = urllib.parse.unquote(v)
        self.seen.append((method, params))
        got = self.handlers[method](params)
        if isinstance(got, tuple):
            status, hdrs, body = got
        else:
            status, hdrs, body = 200, {}, {"ok": True, **got}
        return status, hdrs, json.dumps(body).encode("utf-8")

    def sleep(self, seconds):
        self.slept.append(seconds)

    def client(self) -> Slack:
        return Slack("xoxp-test", http=self.http, sleep=self.sleep)

    def calls(self, method: str) -> list[dict]:
        return [p for m, p in self.seen if m == method]


def msg(ts: float, text: str = "말", *, user="U7", thread="", **extra) -> dict:
    got = {"ts": f"{BASE + ts:.6f}", "text": text, "user": user, **extra}
    if thread:
        got["thread_ts"] = thread
    return got


def hist(*messages, more=False, cursor="") -> dict:
    got = {"messages": list(messages), "has_more": more}
    if cursor:
        got["response_metadata"] = {"next_cursor": cursor}
    return got


@pytest.fixture
def book(tmp_path):
    return Book(tmp_path)


@pytest.fixture
def cursors(tmp_path):
    return Cursors(tmp_path)


@pytest.fixture
def frozen():
    """`Book.threads(newer_than_days=)` 는 진짜 시계를 본다. 안 세우면 이
    시험이 **오늘 날짜에 따라 통과했다 말았다** 한다."""
    from genie_agents import clock

    at = {"now": T0 + timedelta(minutes=10)}
    clock.set_clock(lambda: at["now"])
    yield at
    clock.set_clock(lambda: datetime.now(timezone.utc))


# ── 페이지를 끝까지 판다 ────────────────────────────────────────────
def test_첫_장만_읽으면_금요일_오후가_사라진다(book, cursors):
    """★ 상시 폴링에서는 한 장이면 끝이라 안 돌려도 티가 안 났다.
    월요일 아침엔 티가 난다."""
    장 = {"n": 0}

    def history(p):
        장["n"] += 1
        if 장["n"] == 1:
            return hist(msg(300, "월요일"), more=True, cursor="p2")
        if 장["n"] == 2:
            return hist(msg(200, "금요일 저녁"), more=True, cursor="p3")
        return hist(msg(100, "금요일 오후"))

    fake = Fake(**{"conversations.history": history})
    watch = SlackWatch(fake.client(), rooms=("C01",))
    watch.catchup(book, cursors, at=T0)
    assert [x.text for x in book.lines("C01")] == ["금요일 오후", "금요일 저녁", "월요일"]


def test_커서가_없어도_무한히_안_돈다(book, cursors):
    """`has_more` 를 계속 주는데 커서가 없으면 같은 장을 영원히 판다."""
    fake = Fake(**{"conversations.history": lambda p: hist(msg(100), more=True)})
    watch = SlackWatch(fake.client(), rooms=("C01",))
    watch.catchup(book, cursors, at=T0)
    assert len(fake.calls("conversations.history")) == 1


def test_장이_너무_많으면_거기서_멈춘다(book, cursors):
    fake = Fake(**{"conversations.history":
                   lambda p: hist(msg(100), more=True, cursor="다음")})
    watch = SlackWatch(fake.client(), rooms=("C01",))
    watch.catchup(book, cursors, at=T0)
    assert len(fake.calls("conversations.history")) == MAX_PAGES


# ── 상한과 고장 ─────────────────────────────────────────────────────
def test_429_는_에러가_아니라_기다리라는_답이다():
    """★ Retry-After 를 믿는다. 우리가 정한 backoff 로 덮으면 그 창을 또
    두드려서 상한이 더 길어진다."""
    남은 = {"n": 2}

    def history(p):
        if 남은["n"]:
            남은["n"] -= 1
            return (429, {"Retry-After": "7"}, {"ok": False, "error": "ratelimited"})
        return hist(msg(100, "됐다"))

    fake = Fake(**{"conversations.history": history})
    watch = SlackWatch(fake.client(), rooms=("C01",), me="U_ME", team_url=TEAM)
    assert [x.text for x in watch.history("C01")] == ["됐다"]
    assert fake.slept == [7.0, 7.0]


def test_계속_막히면_결국_말한다():
    fake = Fake(**{"conversations.history":
                   lambda p: (429, {"Retry-After": "1"}, {"ok": False})})
    watch = SlackWatch(fake.client(), rooms=("C01",), me="U_ME", team_url=TEAM)
    with pytest.raises(SlackError) as e:
        watch.history("C01")
    assert e.value.error == "ratelimited"


def test_ok_false_는_무엇이_틀렸는지_들고_온다():
    fake = Fake(**{"conversations.history":
                   lambda p: {"ok": False, "error": "missing_scope",
                              "needed": "channels:history"}})
    # ok:false 는 200 으로 온다 — 위 Fake 는 dict 를 ok:true 로 덮으므로 튜플로 준다
    fake.handlers["conversations.history"] = lambda p: (
        200, {}, {"ok": False, "error": "missing_scope", "needed": "channels:history"})
    watch = SlackWatch(fake.client(), rooms=("C01",), me="U_ME", team_url=TEAM)
    with pytest.raises(SlackError) as e:
        watch.history("C01")
    assert e.value.error == "missing_scope"
    assert "channels:history" in str(e.value)


def test_방_하나가_죽어도_나머지는_들어온다(book, cursors):
    """★ 안 부른 단톡방 하나 때문에 DM 두 자리가 통째로 안 들어오면
    그날 원장이 통째로 빈다."""
    def history(p):
        if p["channel"] == "C_GONE":
            return (200, {}, {"ok": False, "error": "channel_not_found"})
        return hist(msg(100, "살아 있는 방"))

    fake = Fake(**{"conversations.history": history})
    watch = SlackWatch(fake.client(), rooms=("C_GONE", "C01"))
    got = watch.catchup(book, cursors, at=T0)
    assert got == {"C_GONE": 0, "C01": 1}
    assert "channel_not_found" in watch.problems["C_GONE"]
    assert len(book) == 1


# ── 커서 ────────────────────────────────────────────────────────────
def test_커서를_쌓은_뒤에_옮긴다(book, cursors):
    """★ 먼저 옮기면 그 사이에 죽었을 때 그 창이 영영 안 온다.
    다시 긁는 것은 값이 싸고, 안 긁는 것은 값이 없다."""
    fake = Fake(**{"conversations.history": lambda p: hist(msg(100))})
    watch = SlackWatch(fake.client(), rooms=("C01",))

    def 죽는다(_):
        raise RuntimeError("디스크가 찼다")

    book.put_many = 죽는다
    with pytest.raises(RuntimeError):
        watch.catchup(book, cursors, at=T0)
    assert cursors.get("slack:C01") == ""     # 안 옮겼다. 다음에 그 창을 다시 판다


def test_커서는_history_의_ts_로만_옮긴다(book, cursors):
    """★ 스레드 답글의 ts 는 더 클 수 있다. 그걸로 커서를 밀면 다음번
    `oldest` 가 아직 안 읽은 윗줄을 건너뛸 자리가 생긴다."""
    fake = Fake(
        **{"conversations.history": lambda p: hist(msg(100, "윗줄", thread="t1")),
           "conversations.replies": lambda p: hist(msg(100, "윗줄", thread="t1"),
                                                   msg(9000, "한참 뒤 답글", thread="t1"))}
    )
    watch = SlackWatch(fake.client(), rooms=("C01",))
    watch.catchup(book, cursors, at=T0)
    assert cursors.get("slack:C01") == f"{BASE + 100:.6f}"
    assert len(book) == 2                     # 답글은 들어왔다. 커서만 안 밀렸다


def test_커서가_있으면_거기서부터_묻는다(book, cursors):
    fake = Fake(**{"conversations.history": lambda p: hist()})
    cursors.set("slack:C01", "1756000500.000000")
    SlackWatch(fake.client(), rooms=("C01",)).catchup(book, cursors, at=T0)
    assert fake.calls("conversations.history")[0]["oldest"] == "1756000500.000000"


def test_첫_날은_보존_창만큼_거슬러_올라간다(book, cursors):
    """더 긁어와도 `prune` 이 곧 버린다. 그래서 같은 값(72시간)이다."""
    fake = Fake(**{"conversations.history": lambda p: hist()})
    SlackWatch(fake.client(), rooms=("C01",), first_days=3).catchup(book, cursors, at=T0)
    oldest = float(fake.calls("conversations.history")[0]["oldest"])
    assert oldest == pytest.approx(T0.timestamp() - 3 * 86400)


# ── 스레드 ──────────────────────────────────────────────────────────
def test_옛_스레드에_붙은_답글을_찾아낸다(book, cursors, frozen):
    """★ 창을 넓히는 것으로는 절대 안 잡히는 구멍이다 — 원글이 창 밖이면
    `history` 는 그 스레드를 아예 안 준다."""
    옛것 = SlackWatch(Fake().client(), rooms=(), me="U_ME", team_url=TEAM)
    book.put(옛것.line("C01", msg(0, "지난주 PR", thread="t_old")))

    fake = Fake(
        **{"conversations.history": lambda p: hist(),          # 새 윗줄은 없다
           "conversations.replies": lambda p: hist(msg(0, "지난주 PR", thread="t_old"),
                                                   msg(500, "다 봤어요", thread="t_old"))}
    )
    watch = SlackWatch(fake.client(), rooms=("C01",))
    watch.catchup(book, cursors, at=T0)
    assert "다 봤어요" in [x.text for x in book.lines("C01")]
    assert fake.calls("conversations.replies")[0]["ts"] == "t_old"


def test_이번에_긁힌_스레드도_판다(book, cursors):
    fake = Fake(
        **{"conversations.history": lambda p: hist(msg(100, "원글", thread="t1")),
           "conversations.replies": lambda p: hist(msg(100, "원글", thread="t1"),
                                                   msg(120, "답", thread="t1"))}
    )
    SlackWatch(fake.client(), rooms=("C01",)).catchup(book, cursors, at=T0)
    assert [x.text for x in book.lines("C01")] == ["원글", "답"]


def test_스레드가_없으면_replies_를_안_부른다(book, cursors):
    fake = Fake(**{"conversations.history": lambda p: hist(msg(100, "혼잣말"))})
    SlackWatch(fake.client(), rooms=("C01",)).catchup(book, cursors, at=T0)
    assert fake.calls("conversations.replies") == []


# ── 옮기기 ──────────────────────────────────────────────────────────
@pytest.fixture
def watch():
    return SlackWatch(Fake().client(), rooms=("C01",), me="U_ME", team_url=TEAM)


def test_본인_말은_이름으로_적힌다(watch):
    assert watch.line("C01", msg(1, "내가 볼게", user="U_ME")).who == ME
    assert watch.line("C01", msg(1, "넵", user="U7")).who == "U7"


def test_들어왔다_나갔다는_신호가_아니다(watch):
    assert watch.line("C01", msg(1, "누가 들어옴", subtype="channel_join")) is None


def test_빈_말은_안_싣는다(watch):
    """파일만 올린 말. 첨부는 이 물건이 읽는 것이 아니다."""
    assert watch.line("C01", msg(1, "  ")) is None


def test_봇이_친_말은_버린다(watch):
    """★ 값으로 올려 둔 판단이다 — 근거는 GitHub API 에서 받기로 했다."""
    assert watch.line("C01", {"ts": "1.0", "text": "빌드 성공", "bot_id": "B1"}) is None
    켠다 = SlackWatch(Fake().client(), rooms=(), me="U_ME", team_url=TEAM, skip_bots=False)
    assert 켠다.line("C01", {"ts": "1.0", "text": "빌드 성공", "bot_id": "B1"}).who == "B1"


def test_표기를_안_푼다(watch):
    """사람 이름으로 바꾸는 것은 추출이 묶음을 실을 때 하는 일이다.
    여기서 풀면 원문이 아니게 되고, `Line.mentions` 가 읽을 것이 없어진다."""
    got = watch.line("C01", msg(1, "<@U7> 이거 <https://x|여기> 봐줄래?"))
    assert got.text == "<@U7> 이거 <https://x|여기> 봐줄래?"
    assert got.mentions == ["U7"]


def test_고쳐진_말은_표시를_들고_온다(watch):
    got = watch.line("C01", msg(1, "내일 볼게", edited={"user": "U7", "ts": "1756000900.0"}))
    assert got.edited == "1756000900.0"


def test_시각은_ISO_로_옮긴다(watch):
    assert watch.line("C01", msg(0)).ts == T0.isoformat()
    assert stamp("1756000000.000000") == "2025-08-24T01:46:40+00:00"


# ── 근거 키에서 링크가 복원된다 ─────────────────────────────────────
def test_링크를_한_번_더_안_묻고_만든다(watch):
    got = watch.line("C01", msg(0, "말"))
    assert got.url == f"{TEAM}archives/C01/p" + f"{BASE:.6f}".replace(".", "")


def test_스레드_답글은_스레드까지_가리킨다(watch):
    got = watch.line("C01", msg(120, "답", thread=f"{BASE:.6f}"))
    assert f"thread_ts={BASE:.6f}" in got.url and "cid=C01" in got.url


def test_팀_주소를_모르면_링크를_안_만든다():
    """★ 틀린 링크는 없는 링크보다 나쁘다 — 한 번 눌러서 안 열리면
    그 다음부터 아무도 안 누른다."""
    assert permalink("", "C01", "1.0") == ""


def test_키에서_자리가_되돌아온다():
    assert parse_key(key_of("C01", "1756.1")) == ("C01", "1756.1")
    assert parse_key("mail:회사:t1") == ("", "")


# ── 나는 누구인가 ───────────────────────────────────────────────────
def test_본인을_한_번만_묻는다(book, cursors):
    fake = Fake(**{"conversations.history": lambda p: hist(msg(100), msg(200))})
    watch = SlackWatch(fake.client(), rooms=("C01", "D02"))
    watch.catchup(book, cursors, at=T0)
    assert len(fake.calls("auth.test")) == 1
    assert watch.me == "U_ME" and watch.team_url == TEAM


def test_손으로_준_것이_이긴다(book, cursors):
    """회사 PC 에서 도는 물건이라 호출 하나를 안 하는 것에도 값이 있다."""
    fake = Fake(**{"conversations.history": lambda p: hist(msg(100))})
    watch = SlackWatch(fake.client(), rooms=("C01",), me="U_X", team_url="https://t/")
    watch.catchup(book, cursors, at=T0)
    assert fake.calls("auth.test") == []


# ── 방 id 를 찾는다 ─────────────────────────────────────────────────
def _folks(p):
    return {"members": [
        {"id": "U7", "profile": {"display_name": "팀원A", "real_name": "김에이"}},
        {"id": "U8", "profile": {"real_name": "이비"}},          # 표시 이름이 없으면 본명
        {"id": "U9", "name": "handle"},                          # 둘 다 없으면 핸들
    ]}


def _convos(p):
    return {"channels": [
        {"id": "C01", "name": "개발", "is_private": False, "num_members": 8},
        {"id": "G02", "name": "팀장방", "is_private": True, "num_members": 4},
        {"id": "D03", "is_im": True, "user": "U7"},
        {"id": "D04", "is_im": True, "user": "U8"},
        {"id": "G05", "is_mpim": True, "name": "mpdm-a--b--c-1", "num_members": 3},
        {"id": "C99", "name": "옛방", "is_private": False, "num_members": 2},
    ]}


def test_DM_상대를_이름으로_푼다():
    from genie_agents.channels.slack import rooms

    fake = Fake(**{"users.list": _folks, "conversations.list": _convos})
    got = {r["id"]: r["name"] for r in rooms(fake.client())}
    assert got["D03"] == "팀원A"      # 표시 이름
    assert got["D04"] == "이비"       # 본명으로 떨어진다
    assert got["C01"] == "개발"


def test_종류별로_모아_준다():
    """DM 이 먼저다 — 이 비서가 보는 세 자리 중 둘이 DM 계열이다."""
    from genie_agents.channels.slack import rooms

    fake = Fake(**{"users.list": _folks, "conversations.list": _convos})
    assert [r["kind"] for r in rooms(fake.client())] == [
        "im", "im", "mpim", "private_channel", "public_channel", "public_channel"]


def test_보관된_방은_안_묻는다():
    """★ 안 빼면 목록이 옛 방으로 차고, 거기서 죽은 방을 하나 고르게 된다 —
    그러면 매시 그 방을 헛되이 두드린다."""
    from genie_agents.channels.slack import rooms

    fake = Fake(**{"users.list": _folks, "conversations.list": _convos})
    rooms(fake.client())
    assert fake.calls("conversations.list")[0]["exclude_archived"] == "true"


def test_사람_표를_한_번만_묻는다():
    from genie_agents.channels.slack import people, rooms

    fake = Fake(**{"users.list": _folks, "conversations.list": _convos})
    who = people(fake.client())
    rooms(fake.client(), who=who)
    assert len(fake.calls("users.list")) == 1


def test_이름_표는_id에서_이름으로만_간다():
    """4단계 묶음에 싣는 것은 이것이다 — `<@U123>` 이 누구인지."""
    from genie_agents.channels.slack import names

    fake = Fake(**{"users.list": _folks})
    assert names(fake.client())["U7"] == "팀원A"


# ── 고를 수 있는 목록이어야 한다 ────────────────────────────────────
def _crowd(p):
    return {"members": [
        {"id": "U7", "profile": {"display_name": "팀원A"}},
        {"id": "U8", "profile": {"display_name": "퇴사자"}, "deleted": True},
        {"id": "B1", "profile": {"display_name": "Jira"}, "is_bot": True},
        {"id": "USLACKBOT", "profile": {"display_name": "Slackbot"}},
    ]}


def _dms(p):
    return {"channels": [
        {"id": "D01", "is_im": True, "user": "U7"},
        {"id": "D02", "is_im": True, "user": "U8"},
        {"id": "D03", "is_im": True, "user": "B1"},
        {"id": "D04", "is_im": True, "user": "USLACKBOT"},
        {"id": "D05", "is_im": True, "user": "U_모르는사람"},
        {"id": "C01", "name": "개발", "num_members": 8},
    ]}


def test_앱_DM_과_퇴사자_DM_을_뺀다():
    """★ 이 워크스페이스에는 앱 DM 과 퇴사자 DM 이 **사람 DM 보다 많다.**
    백 줄에서 셋을 고르는 일과 스무 줄에서 셋을 고르는 일은 다른 일이다."""
    from genie_agents.channels.slack import rooms

    fake = Fake(**{"users.list": _crowd, "conversations.list": _dms})
    assert [r["id"] for r in rooms(fake.client())] == ["D01", "C01"]


def test_슬랙봇은_봇_표시가_없어도_봇이다():
    """`USLACKBOT` 은 `is_bot` 이 안 붙어 온다. 이름으로 안다."""
    from genie_agents.channels.slack import people

    fake = Fake(**{"users.list": _crowd})
    assert people(fake.client())["USLACKBOT"]["bot"] is True


def test_전부_보고_싶으면_볼_수_있다():
    """거르는 것은 감추는 것이 아니다. 되돌릴 길을 같이 둔다."""
    from genie_agents.channels.slack import rooms

    fake = Fake(**{"users.list": _crowd, "conversations.list": _dms})
    got = rooms(fake.client(), humans_only=False)
    assert len(got) == 6


def test_거르기는_DM_에만_건다():
    """방은 사람이 아니다. 같은 잣대를 대면 단톡방이 통째로 사라진다."""
    from genie_agents.channels.slack import rooms

    fake = Fake(**{"users.list": _crowd, "conversations.list": _dms})
    assert "C01" in [r["id"] for r in rooms(fake.client())]


def test_자기_자신과의_DM_을_표시한다():
    """★ 표시 이름이 본인 이름이라 목록에서 팀원과 구별이 안 간다.
    실제로 이 워크스페이스에서 헷갈렸다."""
    from genie_agents.channels.slack import rooms

    def me_too(p):
        return {"channels": [{"id": "D00", "is_im": True, "user": "U7"}]}

    fake = Fake(**{"users.list": _crowd, "conversations.list": me_too})
    assert "나 자신" in rooms(fake.client(), me="U7")[0]["name"]
    assert "나 자신" not in rooms(fake.client())[0]["name"]


# ── 스레드를 빠뜨리지 않는다 ────────────────────────────────────────
def test_상한에_걸려도_아무거나_안_빠진다(book, cursors, frozen):
    """★ 집합으로 모아 `[:상한]` 을 씌우면 파이썬 집합 순서대로 잘려서
    **매번 아무 스레드나 빠진다.** 실측으로 한 방에 살아 있는 스레드가 22개,
    상한이 20이었다 — 조용히 둘씩 사라지는 중이었다."""
    from genie_agents.transcript import Line

    # 원장이 아는 스레드 다섯. 새것부터 t4 · t3 · t2 · t1 · t0
    book.put_many([
        Line(key=f"slack:C01:old{i}", room="C01", who="U7", text=f"원글{i}",
             ts=(T0 + timedelta(hours=i)).isoformat(), thread=f"t{i}")
        for i in range(5)
    ])
    fake = Fake(**{"conversations.history": lambda p: hist(),
                   "conversations.replies": lambda p: hist()})
    watch = SlackWatch(fake.client(), rooms=("C01",), max_threads=3)
    watch.catchup(book, cursors, at=T0)
    판것 = [p["ts"] for p in fake.calls("conversations.replies")]
    assert 판것 == ["t4", "t3", "t2"]          # 새것 셋. 순서가 정해져 있다


def test_이번에_움직인_스레드가_먼저다(book, cursors, frozen):
    """지금 말이 오간 스레드가 상한에 밀려나면 안 된다."""
    from genie_agents.transcript import Line

    book.put_many([
        Line(key=f"slack:C01:old{i}", room="C01", who="U7", text=f"원글{i}",
             ts=(T0 + timedelta(hours=i)).isoformat(), thread=f"t{i}")
        for i in range(5)
    ])
    fake = Fake(**{"conversations.history": lambda p: hist(msg(600, "새 원글", thread="t_now")),
                   "conversations.replies": lambda p: hist()})
    watch = SlackWatch(fake.client(), rooms=("C01",), max_threads=2)
    watch.catchup(book, cursors, at=T0)
    assert [p["ts"] for p in fake.calls("conversations.replies")][0] == "t_now"


def test_원글을_들고_있으면_그_뒤부터만_받는다(book, cursors, frozen):
    """★ 안 그러면 매시 스레드를 통째로 다시 받아 온다 — 백 줄짜리 스레드가
    몇 개만 있어도 그게 쓰는 값의 대부분이 된다."""
    fake = Fake(**{"conversations.history": lambda p: hist(msg(100, "원글", thread=f"{BASE + 100:.6f}")),
                   "conversations.replies": lambda p: hist()})
    watch = SlackWatch(fake.client(), rooms=("C01",))
    watch.catchup(book, cursors, at=T0)          # 원글이 들어온다
    watch.catchup(book, cursors, at=T0)          # 두 번째
    부른것 = fake.calls("conversations.replies")
    assert부터 = [p.get("oldest") for p in 부른것]
    assert 부른것[0].get("oldest") in (None, "")           # 처음엔 통째로
    assert float(부른것[-1]["oldest"]) == pytest.approx(BASE + 100)


def test_원글이_없으면_통째로_다시_받는다(book, cursors, frozen):
    """원문이 버려진 뒤(72시간)에는 원글부터 다시 받아야 한다 — 답글만
    쌓이면 묶음이 가리키는 대상을 잃는다."""
    from genie_agents.transcript import Line

    book.put(Line(key="slack:C01:없는원글", room="C01", who="U7", text="답",
                  ts=T0.isoformat(), thread="t_orphan"))
    book.prune(hours=0, at=T0 + timedelta(hours=1))   # 글은 버리고 자국만 남는다
    fake = Fake(**{"conversations.history": lambda p: hist(),
                   "conversations.replies": lambda p: hist()})
    SlackWatch(fake.client(), rooms=("C01",)).catchup(book, cursors, at=T0)
    assert fake.calls("conversations.replies")[0].get("oldest") in (None, "")


def test_ISO_와_slack_표기를_오간다():
    from genie_agents.channels.slack import epoch

    assert epoch(stamp("1756000000.000000")) == "1756000000.000000"


# ── 그때인 척하기 ───────────────────────────────────────────────────
def test_그때인_척할_때_천장이_걸린다(book, cursors):
    """★ `at` 은 창의 바닥만이 아니라 **천장이기도 하다.** 전에는 `_since` 에만
    걸려서 `at=8/15` 로 불러도 8/12~오늘을 긁어 왔다 — 그때인 척하는 것이
    아니라 그냥 더 많이 긁는 것이었다. 리허설이 성립하려면 천장이 있어야 한다."""
    fake = Fake(**{"conversations.history": lambda p: hist(msg(100, "말", thread="t1")),
                   "conversations.replies": lambda p: hist()})
    SlackWatch(fake.client(), rooms=("C01",)).catchup(book, cursors, at=T0)
    부른것 = fake.calls("conversations.history")[0]
    assert float(부른것["latest"]) == pytest.approx(T0.timestamp())
    assert float(부른것["oldest"]) == pytest.approx(T0.timestamp() - 3 * 86400)
    # 스레드도 같은 천장을 받는다 — 안 그러면 답글만 미래에서 온다
    assert float(fake.calls("conversations.replies")[0]["latest"]) == pytest.approx(T0.timestamp())


def test_at_을_안_주면_천장이_없다(book, cursors, frozen):
    """실제로 돌 때는 지금까지 다 가져온다."""
    fake = Fake(**{"conversations.history": lambda p: hist()})
    SlackWatch(fake.client(), rooms=("C01",)).catchup(book, cursors)
    assert fake.calls("conversations.history")[0].get("latest") in (None, "")
