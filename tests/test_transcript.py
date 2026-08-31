"""대화 기록 — 오간 말을 그대로 든다.

`world` 로 안 간 이유가 셋이었다(적재 시각 · 제목 중복 · 브리핑 소음).
그 셋이 여기서 시험이 된다 — 고친 자리가 다시 새는지는 그 자리를 시험이
누르고 있을 때만 안다.

★ 이 묶음도 대부분 "틀리는 길이 막혀 있나" 를 본다. 원문이 조용히 사라지는
  것이 여기서 제일 나쁜 고장이다 — 없어진 말은 없어진 줄도 모른다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from genie_agents import clock
from genie_agents.transcript import (
    ME,
    RECENT_MAX,
    THREAD_MAX,
    Book,
    Line,
)

T0 = datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def book(tmp_path):
    return Book(tmp_path)


@pytest.fixture
def frozen():
    at = {"now": T0}
    clock.set_clock(lambda: at["now"])
    yield at
    clock.set_clock(lambda: datetime.now(timezone.utc))


def at(minutes: float = 0) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat()


def line(n: float, text: str = "말", *, room="C01", who="U9", thread="", edited="") -> Line:
    return Line(key=f"slack:{room}:{1756000000 + n * 60:.6f}", room=room, who=who,
                text=text, ts=at(n), thread=thread, edited=edited)


# ── 신원은 근거 키다 ────────────────────────────────────────────────
def test_같은_키는_두_번_안_들어간다(book):
    """폴링이 겹치고, 창이 벌어져 다시 훑는다. `LoopBook.open` 과 같은 규칙이
    한 층 아래에 있는 것이다."""
    assert book.put(line(0, "ㅇㅋ")) is True
    assert book.put(line(0, "ㅇㅋ")) is False
    assert len(book) == 1


def test_같은_말을_두_번_쳐도_둘_다_남는다(book):
    """★ `world` 로 갔으면 여기서 뒷것이 사라졌다 — 거기는 `(source, title)`
    로 중복을 막는다. 짧은 말이 신호의 핵심인 물건에서 "ㅇㅋ" 두 개가
    하나가 되면, 상태를 바꾼 순간이 통째로 없어진다."""
    book.put(line(0, "ㅇㅋ"))
    book.put(line(5, "ㅇㅋ"))
    assert len(book) == 2


def test_고쳐진_말은_덮어쓴다(book):
    """같은 자리의 말이 바뀐 것이지 새 말이 아니다. 두 벌로 두면 추출이
    옛 문장과 새 문장을 둘 다 읽고, 둘이 다른 판단을 낸다."""
    book.put(line(0, "내가 볼게"))
    assert book.put(line(0, "내가 내일 볼게", edited="1756000100.0")) is False
    assert len(book) == 1
    assert book.get(line(0).key).text == "내가 내일 볼게"


def test_안_고쳐졌으면_안_덮어쓴다(book):
    """수정 표시가 없으면 그냥 다시 온 것이다. 덮어쓰면 폴링이 겹칠 때마다
    파일을 다시 쓴다."""
    book.put(line(0, "먼저"))
    book.put(line(0, "나중"))
    assert book.get(line(0).key).text == "먼저"


# ── 시각은 말한 시각이다 ────────────────────────────────────────────
def test_시각은_적재_시각이_아니다(book, frozen):
    """★ `world.ingest` 는 여기서 `now` 를 찍는다. 그러면 사흘 전 말이 전부
    오늘 것이 되고, 시간창도 `quiet_days` 도 통째로 틀린다."""
    frozen["now"] = T0 + timedelta(days=3)
    book.put(line(0, "사흘 전 말"))
    assert book.get(line(0).key).ts == at(0)


def test_시간대가_섞여도_순서가_안_뒤집힌다(book):
    """글자로 비교하면 "+09:00" 과 "+00:00" 이 글자 순서로 비교돼 조용히
    뒤집힌다(`wake.last` 가 같은 자리에서 한 번 데었다)."""
    utc = Line(key="slack:C01:2", room="C01", who="U9", text="나중",
               ts="2026-08-31T01:00:00+00:00")
    kst = Line(key="slack:C01:1", room="C01", who="U9", text="먼저",
               ts="2026-08-31T09:30:00+09:00")   # = 00:30 UTC
    book.put_many([utc, kst])
    assert [x.text for x in book.lines("C01")] == ["먼저", "나중"]


# ── 내 말인지를 원장이 안다 ─────────────────────────────────────────
def test_본인_말은_이름으로_적힌다(book):
    """겹 1 규칙 넷이 전부 "내가 친 말인가" 를 묻는다. 원시 id 로 두고 매번
    비교하게 하면 비교를 빠뜨린 자리 하나가 남의 말을 내 말로 센다."""
    book.put(line(0, "내가 볼게", who=ME))
    book.put(line(1, "넵", who="U7"))
    mine = [x for x in book.lines("C01") if x.mine]
    assert [x.text for x in mine] == ["내가 볼게"]


def test_멘션은_본문에서_꺼낸다(book):
    """저장하면 두 군데가 되고, 갈리면 멘션을 놓치는 쪽으로 갈린다."""
    x = line(0, "<@U7> 이거 좀 봐줄래? <@W12> 도")
    assert x.mentions == ["U7", "W12"]
    assert x.calls("U7") and not x.calls("U99")


# ── 묶음 ────────────────────────────────────────────────────────────
def test_스레드는_원글이_같이_온다(book):
    """가리키는 대상이 거의 항상 원글이다."""
    book.put(line(0, "PR #42 올렸어요", thread="t1"))
    book.put_many([line(i, f"답{i}", thread="t1") for i in range(1, 4)])
    book.put(line(9, "딴 얘기"))
    got = book.bundle("C01", thread="t1")
    assert got.thread == "t1"
    assert [x.text for x in got.lines] == ["PR #42 올렸어요", "답1", "답2", "답3"]


def test_스레드가_넘치면_원글을_남기고_가운데를_버린다(book):
    """★ 앞을 자르면 안 된다. 원글이 없으면 "다 확인했어" 가 무엇인지
    영영 못 찾는다."""
    book.put(line(0, "원글", thread="t1"))
    book.put_many([line(i, f"답{i}", thread="t1") for i in range(1, 40)])
    got = book.bundle("C01", thread="t1")
    assert len(got) == THREAD_MAX
    assert got.lines[0].text == "원글"
    assert got.lines[-1].text == "답39"


def test_조용한_방은_창이_비어도_직전_것을_싣는다(book):
    """★ 시간창만 쓰면 조용한 방에서 묶음이 통째로 빈다. 30분에 한 마디
    오는 DM 이 정확히 그렇다."""
    book.put_many([line(i * 120, f"말{i}") for i in range(5)])
    got = book.bundle("C01")
    assert len(got) == 5           # 창(30분)엔 하나뿐이지만 직전 10개로 간다
    assert got.lines[-1].text == "말4"


def test_시끄러운_방은_창이_열개보다_넓으면_창을_싣는다(book):
    """반대쪽. 10개가 30분을 못 덮는 방에서는 창이 이긴다."""
    book.put_many([line(i, f"말{i}") for i in range(25)])
    got = book.bundle("C01", window_minutes=30, recent=RECENT_MAX)
    assert len(got) == THREAD_MAX  # 창 안에 25개지만 상한이 20에서 자른다
    assert got.lines[-1].text == "말24"


def test_묶음은_방을_안_섞는다(book):
    """읽는 범위를 세 자리로 좁힌 것이 결정이다. 묶음이 방을 섞으면
    그 결정이 여기서 무의미해진다."""
    book.put(line(0, "여기", room="C01"))
    book.put(line(1, "저기", room="D02"))
    assert [x.text for x in book.bundle("C01").lines] == ["여기"]


def test_묶음은_그_시점까지만_본다(book):
    """저녁에 아침 묶음을 다시 만들 수 있어야 한다 — 못 하면 사람이
    "그때 뭘 보고 그랬냐" 를 물을 자리가 없다."""
    book.put_many([line(i * 10, f"말{i}") for i in range(5)])
    got = book.bundle("C01", at=at(25))
    assert [x.text for x in got.lines] == ["말0", "말1", "말2"]


# ── 다시 팔 스레드 ──────────────────────────────────────────────────
def test_살아있는_스레드를_새것부터_안다(book, frozen):
    """★ `conversations.history` 는 스레드 답글을 안 준다. 옛 스레드에 붙은
    답글을 찾는 유일한 길이 이 목록이다."""
    book.put(line(0, "옛 스레드", thread="t_old"))
    book.put(line(60 * 24 * 5, "닷새 뒤 딴 스레드", thread="t_new"))
    frozen["now"] = T0 + timedelta(days=5, minutes=1)
    assert book.threads("C01") == ["t_new", "t_old"]
    assert book.threads("C01", newer_than_days=3) == ["t_new"]


def test_스레드_없는_말은_목록에_안_든다(book):
    book.put(line(0, "혼잣말"))
    assert book.threads("C01") == []


# ── 원문은 오래 안 둔다 ─────────────────────────────────────────────
def test_따라잡기_창_밖은_버린다(book, frozen):
    """§9 의 "원문은 따라잡기 창(72시간)만" 이 실제로 걸리는 한 줄."""
    book.put(line(0, "나흘 전"))
    book.put(line(60 * 24 * 4, "지금"))
    frozen["now"] = T0 + timedelta(days=4)
    assert book.prune(hours=72) == 1
    assert [x.text for x in book.lines()] == ["지금"]


def test_버린_뒤에도_같은_키가_다시_안_들어온다는_보장은_없다(book, frozen):
    """★ 여기는 **못 막는 것을 적어 두는 자리**다.

    원문을 버리면 그 키를 잊는다. 커서가 뒤로 가면 같은 말이 다시 들어온다 —
    막는 것은 `LoopBook.open` 이지 이 원장이 아니다. 두 층에 같은 규칙이
    있는 이유가 이거다.
    """
    book.put(line(0, "옛말"))
    frozen["now"] = T0 + timedelta(days=4)
    book.prune(hours=72)
    assert book.put(line(0, "옛말")) is True   # 다시 들어온다. 알고 있는 것이다


def test_안_버릴_것이_없으면_파일을_안_쓴다(book, frozen, tmp_path):
    book.put(line(0))
    쓴때 = (tmp_path / "transcript.json").stat().st_mtime_ns
    assert book.prune(hours=72) == 0
    assert (tmp_path / "transcript.json").stat().st_mtime_ns == 쓴때


# ── 다시 떠도 그대로 ────────────────────────────────────────────────
def test_다시_읽어도_같다(tmp_path):
    a = Book(tmp_path)
    a.put_many([line(0, "하나", thread="t1"), line(1, "둘")])
    b = Book(tmp_path)
    assert len(b) == 2
    assert [x.text for x in b.lines("C01")] == ["하나", "둘"]
    assert b.threads("C01") == ["t1"]


def test_필드가_늘어도_옛_파일이_안_죽는다(tmp_path):
    """`store.from_dict` 가 모르는 키를 버린다. 쓰는 쪽과 읽는 쪽이 항상 같은
    버전이라는 보장이 없다."""
    import json

    (tmp_path / "transcript.json").write_text(
        json.dumps({"lines": [{"key": "slack:C01:1", "room": "C01", "who": "U9",
                               "text": "말", "ts": at(0), "나중에생긴칸": 1}]}),
        encoding="utf-8",
    )
    assert len(Book(tmp_path)) == 1


# ── 스레드 자국은 원문보다 오래 산다 ────────────────────────────────
def test_원문을_버려도_스레드는_기억한다(book, frozen):
    """★ 실측: 한 방의 대화 **44%가 스레드 안**에 있었다. 원문에서 목록을
    만들면 오래 가는 스레드일수록 안 보이게 된다 — `history` 는 창 밖 원글을
    안 주고, 다시 팔 목록마저 사라지면 그 스레드는 영영 안 들어온다."""
    book.put(line(0, "지난주 원글", thread="t_old"))
    frozen["now"] = T0 + timedelta(days=4)
    assert book.prune(hours=72) == 1
    assert len(book) == 0                     # 글은 버렸다
    assert book.threads("C01") == ["t_old"]   # 자국은 남았다


def test_자국은_글이_아니라_id와_시각뿐이다(book, tmp_path):
    """§9 의 "그 뒤엔 고리와 근거 키만" 이 이 모양이다."""
    import json

    book.put(line(0, "비밀스러운 원글 내용", thread="t1"))
    book.prune(hours=0, at=at(60))   # 시계를 손으로 민다 — 진짜 지금에 기대면
                                      # 이 시험이 시각에 따라 통과했다 말았다 한다
    raw = json.loads((tmp_path / "transcript.json").read_text(encoding="utf-8"))
    assert raw["lines"] == []
    assert raw["threads"] == {"C01": {"t1": at(0)}}
    assert "비밀스러운" not in json.dumps(raw, ensure_ascii=False)


def test_한_달_조용한_스레드는_잊는다(book, frozen):
    """자국도 영영 안 들고 있지는 않는다 — 안 그러면 파일이 자라기만 한다."""
    book.put(line(0, "옛것", thread="t_old"))
    book.put(line(60 * 24 * 29, "덜 옛것", thread="t_mid"))
    frozen["now"] = T0 + timedelta(days=31)
    book.prune(hours=72, thread_days=30)
    assert book.threads("C01") == ["t_mid"]


def test_마지막으로_본_데를_안다(book):
    """다시 팔 때 `oldest` 로 쓴다 — 매시 스레드를 통째로 다시 안 받는다."""
    book.put_many([line(0, "원글", thread="t1"), line(5, "답", thread="t1")])
    assert book.thread_at("C01", "t1") == at(5)
    assert book.thread_at("C01", "없는것") == ""


def test_다시_읽어도_자국이_남는다(tmp_path):
    a = Book(tmp_path)
    a.put(line(0, "원글", thread="t1"))
    assert Book(tmp_path).threads("C01") == ["t1"]
