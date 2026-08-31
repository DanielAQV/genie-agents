"""판을 적어 두고 나란히 놓는다 — 눈금이 앉는 자리.

★ 이 묶음이 지키는 것은 **점수가 저 혼자 오르지 않는 것**이다. 평가 장치는
  조용히 후해지는 쪽으로 고장 난다 — 안 본 것을 맞은 것으로 세거나, 답이
  바뀌었는데 옛 판정을 물려주거나, 빈 답을 성한 답으로 세면 전부 점수가
  오른다. 그리고 오른 점수는 아무도 안 의심한다.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from genie_agents import clock
from genie_agents.cases import RIGHT, WRONG, CaseBook, sha

T0 = datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def book(tmp_path):
    return CaseBook(tmp_path)


@pytest.fixture
def frozen():
    clock.set_clock(lambda: T0)
    yield
    clock.set_clock(lambda: datetime.now(timezone.utc))


def 적기(book, run="1판", room="C01", thread="", raw='{"opens": []}', **kw):
    return book.add(run=run, prompt_sha="abc123", room=room, thread=thread,
                    body="묶음 글", raw=raw,
                    parsed=kw.pop("parsed", {"moves": [], "opens": [], "unresolved": []}),
                    applied={}, **kw)


# ── 적고 다시 읽는다 ────────────────────────────────────────────────
def test_한_줄이_한_판이다(book, frozen, tmp_path):
    """★ JSON 배열이 아니라 줄 단위다. 판이 길면 중간에 죽는데, 배열이면
    그때까지 돌린 것이 통째로 안 읽힌다."""
    적기(book); 적기(book)
    assert len(book) == 2
    assert (tmp_path / "cases.jsonl").read_text(encoding="utf-8").count("\n") == 2


def test_죽다_만_줄이_있어도_나머지는_읽는다(book, frozen, tmp_path):
    적기(book)
    with (tmp_path / "cases.jsonl").open("a", encoding="utf-8") as f:
        f.write('{"id": "잘림", "run": ')
    assert len(book) == 1


def test_입력과_출력이_같이_남는다(book, frozen):
    """**이게 있어야 슬랙 없이 다시 돌린다.** 하나만 남기면 재생이 안 된다."""
    c = 적기(book)
    got = book.all()[0]
    assert got.body == "묶음 글" and got.raw == '{"opens": []}'
    assert got.keys == [] and got.prompt_sha == "abc123"


# ── 점수가 저 혼자 오르지 않는다 ────────────────────────────────────
def test_안_본_것은_맞은_것이_아니다(book, frozen):
    """★ 안 본 것을 맞은 것으로 세면 판이 커질수록 점수가 오른다 —
    그때 그 점수는 품질이 아니라 게으름을 잰다."""
    for _ in range(5):
        적기(book)
    s = book.score("1판")
    assert s["묶음"] == 5 and s["본 것"] == 0
    assert s["정밀도"] is None and s["재현"] is None


def test_낸_줄마다_따로_센다(book, frozen):
    """★ 판 단위 판정은 너무 굵다. 고리 셋 중 둘이 맞고 하나가 쓰레기면
    그 판을 통째로 틀렸다고 셀 때 **잘한 둘까지 같이 벌을 받는다.**"""
    c = 적기(book, parsed={"moves": [], "unresolved": [],
                         "opens": [{"text": "가"}, {"text": "나"}, {"text": "쓰레기"}]})
    book.mark(c.id, RIGHT, item="opens:0")
    book.mark(c.id, RIGHT, item="opens:1")
    book.mark(c.id, WRONG, item="opens:2")
    s = book.score("1판")
    assert s["낸 줄"] == 3 and s["짚은 줄"] == 3
    assert s["정밀도"] == round(2 / 3, 3)


def test_빠뜨린_것을_따로_묻는다(book, frozen):
    """★ 낸 것이 맞는지(정밀도)와 빠뜨린 게 없는지(재현)는 다른 물음이다.
    묶음을 읽어야만 답할 수 있는 쪽은 뒤엣것이라, 안 물으면 영영 안 잰다."""
    a, b = 적기(book, room="C1"), 적기(book, room="C2")
    book.missed(a.id)                      # 없음
    book.missed(b.id, "Thịnh 부탁을 놓쳤다")
    s = book.score("1판")
    assert s["재현"] == 0.5


def test_빠뜨린_것을_답해야_본_것이다(book, frozen):
    """낸 줄만 짚고 넘어가면 재현이 영영 안 잰다 — 그리고 **안 재는 쪽이
    늘 좋아 보인다.**"""
    c = 적기(book, parsed={"moves": [], "unresolved": [], "opens": [{"text": "가"}]})
    book.mark(c.id, RIGHT, item="opens:0")
    assert book.score("1판")["본 것"] == 0      # 아직 안 봤다
    book.missed(c.id)
    assert book.score("1판")["본 것"] == 1


def test_빈_답과_못_읽은_답을_가른다(book, frozen):
    """★ 셋이 다 빈 것과 못 읽은 것은 다르다 — 앞은 아무 일도 안 일어난
    창이고, 뒤는 그 창을 놓친 것이다. 같게 세면 조용한 날이 성적을 올린다."""
    적기(book)                       # 성하게 비었다
    적기(book, parsed={})            # 못 읽었다
    assert book.score("1판")["형식 깨짐"] == 1


def test_모르는_판정은_안_받는다(book, frozen):
    c = 적기(book)
    with pytest.raises(ValueError, match="모르는 판정"):
        book.mark(c.id, "그럭저럭")


def test_짧은_id_로도_짚힌다(book, frozen):
    """사람이 손으로 치는 자리다. 여덟 자를 다 치게 하면 안 짚는다."""
    c = 적기(book)
    assert book.mark(c.id[:4], WRONG) is not None


# ── 판정을 물려준다 ────────────────────────────────────────────────
def test_같은_답이면_앞_판정을_물려받는다(book, frozen):
    """★ 지침만 바꿔 다시 돌리면 묶음은 그대로다. 사람이 이미 본 것을 또
    보게 하면 **두 번째 판부터 아무도 안 본다** — 눈금이 첫 판에서 멈춘다."""
    a = 적기(book, run="1판", room="C01", raw='{"opens": []}')
    book.missed(a.id)
    적기(book, run="2판", room="C01", raw='{"opens": []}')
    assert book.carry("1판", "2판") == 1
    assert book.score("2판")["본 것"] == 1


def test_답이_달라졌으면_안_물려받는다(book, frozen):
    """★ 여기가 이 장치가 조용히 후해지는 자리다. 답이 바뀌었는데 옛 판정을
    물려주면, 나빠진 판이 앞 판의 점수를 그대로 입는다."""
    a = 적기(book, run="1판", room="C01", raw='{"opens": []}')
    book.missed(a.id)
    적기(book, run="2판", room="C01", raw='{"opens": [{"text": "새로 지어낸 것"}]}')
    assert book.carry("1판", "2판") == 0
    assert book.score("2판")["본 것"] == 0


def test_이미_본_것은_안_덮는다(book, frozen):
    a = 적기(book, run="1판", room="C01")
    b = 적기(book, run="2판", room="C01")
    book.missed(a.id)
    book.missed(b.id, "사람이 직접 봤다")
    book.carry("1판", "2판")
    assert book.all()[-1].missed == "사람이 직접 봤다"


# ── 판을 가른다 ────────────────────────────────────────────────────
def test_판마다_따로_센다(book, frozen):
    적기(book, run="1판"); 적기(book, run="2판"); 적기(book, run="2판")
    assert book.runs() == ["1판", "2판"]
    assert book.score("2판")["묶음"] == 2
    assert book.last() == "2판"


def test_지침이_바뀐_것을_들고_있다(book, frozen):
    """★ 이게 없으면 나빠진 것이 지침 탓인지 모델 탓인지 못 가른다 —
    그리고 못 가르면 되돌릴 데를 못 찾는다."""
    book.add(run="1판", prompt_sha="옛것", room="C01", body="", raw="", parsed={}, applied={})
    book.add(run="2판", prompt_sha="새것", room="C01", body="", raw="", parsed={}, applied={})
    assert book.score("1판")["지침"] == ["옛것"]
    assert book.score("2판")["지침"] == ["새것"]


def test_지침_해시는_글이_같으면_같다():
    assert sha("같은 글") == sha("같은 글")
    assert sha("같은 글") != sha("같은 글 ")


# ── 팀원의 글이 여기 있다 ──────────────────────────────────────────
def test_옛_판은_버린다(book, frozen):
    """★ `transcript` 의 72시간이 여기엔 안 걸린다. 안 버리면 자라기만 하고,
    자라는 것이 팀원의 원문이다."""
    for i in range(7):
        적기(book, run=f"{i}판")
    assert book.prune(keep_runs=3) == 4
    assert book.runs() == ["4판", "5판", "6판"]


def test_안_버릴_것이_없으면_그대로(book, frozen):
    적기(book, run="1판")
    assert book.prune(keep_runs=5) == 0
