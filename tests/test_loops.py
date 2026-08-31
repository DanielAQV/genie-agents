"""열린 고리 원장 — 에이전트가 쓰고, 사람은 틀린 것만 고친다.

`loops.py` 는 오래 초안이었다(*"시험이 없고 아무 데도 안 붙어 있다"*). 여기가
그 시험이고, `kit/loops.py` 가 붙는 자리다.

무엇을 만들려는 것인지는 `docs/followup.md`, 어떻게 이어지는지는 `docs/wiring.md`.

★ 이 묶음이 지키는 것은 기능이 아니라 **신뢰**다. 원장이 한 번 틀리면 사람은
  목록 전체를 안 믿고, 이 물건은 신뢰 말고 파는 게 없다. 그래서 여기 있는
  시험은 대부분 "틀리는 길이 막혀 있나" 를 본다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from genie_agents import clock
from genie_agents.kit import CATALOG
from genie_agents.loops import (
    DONE,
    DROPPED,
    LIVE,
    ME,
    OPEN,
    STATES,
    WAITING,
    LoopBook,
)
from genie_agents.tools import MissingContext, Toolbox, UnknownTool

T0 = datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def book(tmp_path):
    return LoopBook(tmp_path)


@pytest.fixture
def frozen():
    """시각을 손으로 민다. `quiet_days` 는 실제로 며칠을 기다려야 도는 자다."""
    at = {"now": T0}
    clock.set_clock(lambda: at["now"])
    yield at
    clock.set_clock(lambda: datetime.now(timezone.utc))


def days(n: float) -> str:
    return (T0 + timedelta(days=n)).isoformat()


class Ctx:
    """도구가 요구하는 것만 든 런타임."""

    def __init__(self, root):
        self.loops = LoopBook(root)


@pytest.fixture
def ctx(tmp_path):
    return Ctx(tmp_path)


# ── 근거 없이는 안 연다 ──────────────────────────────────────────────
def test_같은_근거로는_두_번_안_열린다(book):
    """폴링이 겹치고, 수정된 메시지가 새것처럼 오고, 다시 훑는다 — 셋 다 실제로
    있고 셋 다 이 한 줄이 막는다(`wiring.md` 근거 키)."""
    a = book.open("#42 본다", source="slack:C01:1756.1")
    b = book.open("#42 보기로 함", source="slack:C01:1756.1")
    assert a.id == b.id
    assert len(book) == 1
    # 처음 연 것이 남는다. 나중 말로 덮어쓰지 않는다
    assert a.text == "#42 본다"


def test_근거가_다르면_다른_고리다(book):
    book.open("같은 말", source="slack:C01:1")
    book.open("같은 말", source="slack:C01:2")
    assert len(book) == 2


# ── 상태 ─────────────────────────────────────────────────────────────
def test_상태는_넷뿐이다():
    """늘릴수록 "지금 뭐지" 를 사람이 판단하게 된다."""
    assert STATES == (OPEN, WAITING, DONE, DROPPED)
    assert LIVE == (OPEN, WAITING)


def test_모르는_상태는_거부한다(book):
    lp = book.open("x", source="s:1")
    with pytest.raises(ValueError, match="보류"):
        book.move(lp.id, "음", state="보류")


def test_닫힌_것을_안_지운다(book):
    """닫힌 고리가 곧 한 일이다. 지우면 성과 보고의 근거가 사라진다."""
    lp = book.open("#42 리뷰", source="s:1")
    book.close(lp.id)
    assert len(book) == 1
    assert book.get(lp.id).state == DONE
    assert book.get(lp.id) not in book.live()


def test_접은_것은_한_일이_아니다(book, frozen):
    """`DROPPED` 를 `DONE` 과 갈라 두는 이유가 이것 하나다."""
    a = book.open("한 것", source="s:1")
    b = book.open("안 하기로 한 것", source="s:2")
    book.close(a.id)
    book.drop(b.id)

    done = book.closed_between(days(-1), days(1))
    assert [x.id for x in done] == [a.id]
    # 둘 다 살아 있지는 않다
    assert book.live() == []


# ── 차례 ─────────────────────────────────────────────────────────────
def test_차례가_넘어가면_owner_가_바뀐다(book):
    """"내가 남에게 건 것" 이 팀장 일의 절반이고 제일 잘 샌다."""
    lp = book.open("리뷰 부탁함", source="s:1")
    assert lp.mine and lp.owner == ME

    book.move(lp.id, "팀원A 에게 넘김", state=WAITING, owner="팀원A")
    got = book.get(lp.id)
    assert got.owner == "팀원A" and got.state == WAITING and not got.mine
    assert got.live  # 기다림도 아직 안 끝난 것이다

    assert book.live(owner="팀원A") == [got]
    assert book.live(owner=ME) == []


# ── 움직임을 적는다 ──────────────────────────────────────────────────
def test_움직임이_자국으로_남는다(book):
    lp = book.open("x", source="s:1", note="본인이 '내가 볼게'")
    book.move(lp.id, "찔렀다")
    book.close(lp.id, "본인이 '다 봤어'")

    got = book.get(lp.id)
    assert [m.note for m in got.moves] == ["본인이 '내가 볼게'", "찔렀다", "본인이 '다 봤어'"]
    assert [m.state for m in got.moves] == [OPEN, OPEN, DONE]


def test_찌른_것도_움직임이라_조용한_날짜가_되감긴다(book, frozen):
    """안 적으면 조용한 날짜만 보고 **매일 같은 것을 다시 찌른다.**
    그게 이 물건이 죽는 가장 흔한 방식이다(`wiring.md` 찌를 순서)."""
    lp = book.open("멈춘 것", source="s:1")

    frozen["now"] = T0 + timedelta(days=4)
    assert book.quiet(3) == [book.get(lp.id)]

    book.move(lp.id, "찔렀다")  # 찌른 자국을 남긴다
    assert book.quiet(3) == []

    frozen["now"] = T0 + timedelta(days=8)
    assert book.quiet(3) == [book.get(lp.id)]  # 다시 조용해지면 다시 나온다


def test_없는_고리를_움직이면_None(book):
    assert book.move("없다", "음") is None
    assert book.close("없다") is None
    assert book.confirm("없다") is None


# ── 확신 ─────────────────────────────────────────────────────────────
def test_추측한_고리는_먼저_말_거는_목록에_안_뜬다(book):
    """틀린 알림 하나가 깎는 신뢰가 놓친 고리 하나보다 비싸다.
    여기가 신뢰의 방파제다."""
    확실 = book.open("#42 는 내가 오늘 볼게", source="s:1", sure=True)
    추측 = book.open("내가 볼게", source="s:2", sure=False)

    assert book.live() == [확실, 추측]          # 훑을 때는 넓게
    assert book.live(sure_only=True) == [확실]   # 먼저 말할 때는 좁게


def test_사람이_맞다고_하면_먼저_말해도_된다(book):
    lp = book.open("내가 볼게", source="s:1", sure=False)
    assert book.live(sure_only=True) == []
    book.confirm(lp.id)
    assert book.live(sure_only=True) == [book.get(lp.id)]


def test_찌를_때도_추측은_뺀다(book, frozen):
    book.open("추측", source="s:1", sure=False)
    확실 = book.open("확실", source="s:2", sure=True)
    frozen["now"] = T0 + timedelta(days=5)
    assert book.quiet(3, sure_only=True) == [book.get(확실.id)]


# ── 꺼내 보기 ────────────────────────────────────────────────────────
def test_오래_조용한_것이_앞이다(book, frozen):
    """그게 먼저 봐야 할 것이다."""
    오래 = book.open("오래된 것", source="s:1")
    frozen["now"] = T0 + timedelta(days=2)
    최근 = book.open("최근 것", source="s:2")
    assert [x.id for x in book.live()] == [오래.id, 최근.id]


def test_기한_없는_고리는_기한_목록에_안_나온다(book):
    """기한이 있는 고리가 대부분이 아니다 — 없는 쪽이 대부분이다."""
    book.open("기한 없음", source="s:1")
    급한 = book.open("내일까지", source="s:2", due="2026-09-01")
    assert [x.id for x in book.due_by("2026-09-01")] == [급한.id]
    assert book.due_by("2026-08-31") == []


def test_닫힌_것도_기한_목록에_안_나온다(book):
    lp = book.open("어제까지였던 것", source="s:1", due="2026-08-30")
    book.close(lp.id)
    assert book.due_by("2026-12-31") == []


# ── 파일로 남는다 ────────────────────────────────────────────────────
def test_다시_열어도_그대로다(tmp_path):
    """단발 실행이 성립하는 이유다 — 상태가 전부 파일이다(`wiring.md` 2절).
    아침·저녁·따라잡기가 각자 다른 프로세스라 이게 안 되면 아무것도 안 된다."""
    a = LoopBook(tmp_path)
    lp = a.open("남아야 한다", source="s:1", owner="팀원A", due="2026-09-01", sure=False)
    a.move(lp.id, "찔렀다", state=WAITING)

    b = LoopBook(tmp_path)
    got = b.get(lp.id)
    assert got.text == "남아야 한다"
    assert got.owner == "팀원A" and got.state == WAITING
    assert got.due == "2026-09-01" and got.sure is False
    assert [m.note for m in got.moves] == ["열림", "찔렀다"]
    # 근거로 두 번 안 여는 것도 파일을 건너서 지켜져야 한다
    assert b.open("다시 왔다", source="s:1").id == lp.id


def test_모르는_필드가_있는_옛_파일도_읽는다(tmp_path):
    """쓰는 쪽과 읽는 쪽이 항상 같은 버전이라는 보장이 없다(`store.from_dict`).
    필드가 하나 늘면 옛 코드가 `TypeError` 로 죽는데, **실제로 그렇게 죽었다.**"""
    a = LoopBook(tmp_path)
    lp = a.open("x", source="s:1")

    import json

    p = tmp_path / "loops.json"
    raw = json.loads(p.read_text(encoding="utf-8"))
    raw["loops"][0]["나중에_생긴_것"] = "값"
    raw["loops"][0]["moves"][0]["이것도"] = "값"
    p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    got = LoopBook(tmp_path).get(lp.id)
    assert got is not None and got.text == "x"
    assert [m.note for m in got.moves] == ["열림"]


# ── 도구로 켠다 ──────────────────────────────────────────────────────
def test_다섯_개가_등록소에_있다():
    names = ["loop_open", "loop_move", "loop_close", "loop_list", "loop_confirm"]
    assert [t["name"] for t in Toolbox(CATALOG, names).specs()] == names


def test_런타임에_원장이_없으면_켤_때_죽는다():
    box = Toolbox(CATALOG, ["loop_open"])

    class 빈것:
        pass

    assert box.check(빈것()) == ["loop_open 은(는) `loops` 가 필요한데 런타임에 없다"]
    with pytest.raises(MissingContext, match="loops"):
        box.bind(빈것())


def test_열고_넘기고_닫는다(ctx):
    box = Toolbox(CATALOG, ["loop_open", "loop_move", "loop_close", "loop_list"])

    got = box.call(ctx, "loop_open", text="#42 리뷰", source="slack:C01:1756.1")
    lid = got["id"]
    assert got["owner"] == ME and got["state"] == OPEN

    box.call(ctx, "loop_move", id=lid, note="팀원A 에게 넘김",
             state=WAITING, owner="팀원A")
    listed = box.call(ctx, "loop_list")["loops"]
    assert [x["owner"] for x in listed] == ["팀원A"]
    assert [x["state"] for x in listed] == [WAITING]

    box.call(ctx, "loop_close", id=lid, note="본인이 '다 봤어'")
    assert box.call(ctx, "loop_list")["loops"] == []


def test_도구도_같은_근거로는_두_번_안_연다(ctx):
    """추출이 매번 같은 말을 다시 보므로 여기가 제일 자주 걸리는 자리다."""
    box = Toolbox(CATALOG, ["loop_open"])
    a = box.call(ctx, "loop_open", text="x", source="slack:C01:1")
    b = box.call(ctx, "loop_open", text="x 다시", source="slack:C01:1")
    assert a["id"] == b["id"]
    assert b["결과"] == "이미 있던 고리다"


def test_접는_것은_move_로_간다(ctx):
    """상태 넷을 안 늘리는 대신 도구도 안 늘린다 — "그건 내 일 아님" 이 여기로 온다."""
    box = Toolbox(CATALOG, ["loop_open", "loop_move", "loop_list"])
    got = box.call(ctx, "loop_open", text="남의 일", source="s:1")
    box.call(ctx, "loop_move", id=got["id"], note="내 일 아님", state=DROPPED)
    assert box.call(ctx, "loop_list")["loops"] == []


def test_없는_고리를_건드리면_예외가_아니라_답이_온다(ctx):
    """막힌 것을 예외로 던지면 판단 루프가 거기서 끊긴다."""
    box = Toolbox(CATALOG, ["loop_move", "loop_close", "loop_confirm"])
    assert box.call(ctx, "loop_move", id="없다", note="음")["결과"] == "그런 고리가 없다"
    assert box.call(ctx, "loop_close", id="없다")["결과"] == "그런 고리가 없다"
    assert box.call(ctx, "loop_confirm", id="없다")["결과"] == "그런 고리가 없다"


def test_모르는_상태를_주면_예외가_아니라_답이_온다(ctx):
    """모델은 상태 이름을 지어낸다. 그 턴을 통째로 날릴 일이 아니다."""
    box = Toolbox(CATALOG, ["loop_open", "loop_move"])
    got = box.call(ctx, "loop_open", text="x", source="s:1")
    out = box.call(ctx, "loop_move", id=got["id"], note="음", state="보류")
    assert "보류" in out["결과"] and OPEN in out["결과"]


def test_목록에_비었을_때와_없을_때를_가른다(ctx):
    """빈 목록만 주면 "아직 아무것도 안 열었다" 와 "다 닫았다" 가 구분이 안 된다."""
    box = Toolbox(CATALOG, ["loop_open", "loop_close", "loop_list"])
    assert box.call(ctx, "loop_list")["전체"] == 0
    got = box.call(ctx, "loop_open", text="x", source="s:1")
    box.call(ctx, "loop_close", id=got["id"])
    out = box.call(ctx, "loop_list")
    assert out["loops"] == [] and out["전체"] == 1


def test_목록을_좁혀_본다(ctx, frozen):
    box = Toolbox(CATALOG, ["loop_open", "loop_list"])
    box.call(ctx, "loop_open", text="추측", source="s:1", sure=False)
    box.call(ctx, "loop_open", text="남의 차례", source="s:2", owner="팀원A")

    assert len(box.call(ctx, "loop_list")["loops"]) == 2
    assert len(box.call(ctx, "loop_list", sure_only=True)["loops"]) == 1
    assert len(box.call(ctx, "loop_list", owner="팀원A")["loops"]) == 1

    frozen["now"] = T0 + timedelta(days=5)
    assert len(box.call(ctx, "loop_list", quiet_days=3)["loops"]) == 2
    assert len(box.call(ctx, "loop_list", quiet_days=9)["loops"]) == 0


def test_목록이_id_와_근거를_같이_준다(ctx):
    """id 가 없으면 모델이 닫을 것을 지목할 수 없고(`wiring.md` 겹 2),
    근거가 없으면 사람이 눌러 볼 수 없다."""
    box = Toolbox(CATALOG, ["loop_open", "loop_list"])
    box.call(ctx, "loop_open", text="x", source="slack:C01:1756.1")
    row = box.call(ctx, "loop_list")["loops"][0]
    assert row["id"] and row["source"] == "slack:C01:1756.1"


def test_추측이라고_말해_준다(ctx):
    """`sure=False` 를 목록이 감추면 저녁에 사람이 무엇을 고쳐야 할지 모른다."""
    box = Toolbox(CATALOG, ["loop_open", "loop_list", "loop_confirm"])
    got = box.call(ctx, "loop_open", text="내가 볼게", source="s:1", sure=False)
    assert box.call(ctx, "loop_list")["loops"][0]["sure"] is False
    box.call(ctx, "loop_confirm", id=got["id"])
    assert box.call(ctx, "loop_list")["loops"][0]["sure"] is True


def test_도구_설명은_그_존재가_갈아_끼운다():
    box = Toolbox(CATALOG, ["loop_open"], describe={"loop_open": "안 끝난 걸 적어 둬."})
    spec = box.specs()[0]
    assert spec["description"] == "안 끝난 걸 적어 둬."
    assert set(spec["input_schema"]["properties"]) == {
        "text", "source", "owner", "due", "sure", "note"
    }
    assert spec["input_schema"]["required"] == ["text", "source"]


def test_근거_없이는_못_연다():
    """모든 고리는 근거를 든다. 근거 없이 "이거 하기로 했잖아" 라고 하면
    사람은 확인할 길이 없고, 한 번 그러면 이 원장 전체를 안 믿게 된다."""
    spec = Toolbox(CATALOG, ["loop_open"]).specs()[0]
    assert "source" in spec["input_schema"]["required"]


def test_안_켜면_없는_것이다(ctx):
    box = Toolbox(CATALOG, ["loop_list"])
    with pytest.raises(UnknownTool):
        box.call(ctx, "loop_open", text="x", source="s:1")
