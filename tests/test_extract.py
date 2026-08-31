"""추출 세 겹 — 규칙으로 후보를 파고, 모델 한 번, 원장과 대조.

★ 여기 시험은 **모델을 안 부른다.** 겹 1 과 겹 3 은 규칙이고, 겹 2 는 답을
  읽는 부분만 본다. 모델이 무엇을 낼지는 시험할 수 없지만 **못 낸 것을 어떻게
  받아내는지**는 시험할 수 있고, 그쪽이 실제로 깨지는 자리다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from genie_agents import clock
from genie_agents.extract import (
    Extraction,
    Open,
    apply,
    ask,
    certain,
    parse,
    plan,
    serialize,
    worth_asking,
)
from genie_agents.loops import DONE, ME, OPEN, LoopBook
from genie_agents.transcript import Book, Bundle, Line

T0 = datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc)
ME_ID = "U0ME"


@pytest.fixture
def book(tmp_path):
    return Book(tmp_path)


@pytest.fixture
def loops(tmp_path):
    return LoopBook(tmp_path)


@pytest.fixture
def frozen():
    at = {"now": T0}
    clock.set_clock(lambda: at["now"])
    yield at
    clock.set_clock(lambda: datetime.now(timezone.utc))


def at(minutes: float = 0) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat()


def line(n: float, text: str, *, who="U7", room="C01", thread="") -> Line:
    return Line(key=f"slack:{room}:{1756000000 + n * 60:.6f}", room=room, who=who,
                text=text, ts=at(n), thread=thread)


def bundle(*lines, room="C01", thread="") -> Bundle:
    return Bundle(room=room, thread=thread, lines=list(lines))


# ── 겹 1 — 규칙으로 후보를 판다 ─────────────────────────────────────
def test_내가_친_말이_있으면_태운다(frozen):
    """짧은 말이 상태를 바꾸는 순간이다. 내 말은 전부 후보다."""
    assert worth_asking(bundle(line(0, "내가 볼게", who=ME))) == "내가 친 말이 있다"


def test_나를_부르면_태운다(frozen):
    assert "부른" in worth_asking(bundle(line(0, "<@U0ME> 이거 봐줄래?")), ME_ID)


def test_남이_묻고_내가_안_답했으면_태운다(frozen):
    """마지막 말이 남의 물음이면 내 차례가 열려 있다."""
    # 내 말을 안 넣는다 — 넣으면 규칙 1이 먼저 걸려서 이 규칙을 안 본다
    got = worth_asking(bundle(line(0, "배포 나갔어요"), line(1, "이거 가능할까요?")))
    assert "물었고" in got


def test_내가_마지막인데_조용하면_태운다(frozen):
    """답이 끊긴 자리. 내가 남에게 건 것이 제일 잘 샌다."""
    b = bundle(line(0, "확인 부탁해요", who=ME))
    assert worth_asking(b, ME_ID) == "내가 친 말이 있다"      # 내 말이라 먼저 걸린다
    frozen["now"] = T0 + timedelta(days=3)
    assert worth_asking(b, ME_ID)                            # 여전히 걸린다


def test_남들끼리_한_잡담은_안_태운다(frozen):
    """소음이 가장 큰 위험이다. 규칙으로 자르고 모델로 판단한다 —
    순서가 반대면 비싸다."""
    b = bundle(line(0, "점심 뭐 먹지"), line(1, "국밥"))
    assert worth_asking(b, ME_ID) == ""


def test_빈_묶음은_안_태운다():
    assert worth_asking(bundle()) == ""


def test_후보를_늘리는_쪽으로_틀린다(frozen):
    """★ 잘못 태우면 토큰 한 묶음이고, 잘못 안 태우면 그 고리는 영영 안
    잡힌다. 값이 대칭이 아니다 — 물음표 표가 헐거운 것은 그래서다."""
    assert worth_asking(bundle(line(0, "이거 괜찮나")), ME_ID)


def test_방마다_스레드마다_묶음이_따로다(book, frozen):
    """묶음 단위 = 방 하나 × 시간창 하나."""
    book.put_many([
        line(0, "PR 올렸어요", thread="t1"),
        line(1, "내가 볼게", who=ME, thread="t1"),
        line(2, "딴 방 얘기", room="D02"),
        line(3, "확인해봐", who=ME, room="D02"),
    ])
    got = plan(book, me_id=ME_ID)
    assert {(b.room, b.thread) for b in got} == {("C01", "t1"), ("D02", "")}


def test_스레드에만_있는_방은_창을_두_번_안_태운다(book, frozen):
    """같은 말을 스레드 묶음과 창 묶음에 두 번 실으면 값이 두 배고,
    모델이 같은 고리를 두 번 연다."""
    book.put_many([line(0, "원글", thread="t1"), line(1, "내가 볼게", who=ME, thread="t1")])
    assert len(plan(book, me_id=ME_ID)) == 1


# ── 겹 2 — 무엇을 싣나 ──────────────────────────────────────────────
def test_열린_고리를_id와_함께_싣는다(book, loops, frozen):
    """★ **닫을 것을 지목하게 하려고** 넣는다. 없으면 모델은 열기만 하고,
    목록은 자라기만 한다."""
    lp = loops.open("#42 본다", source="slack:C01:1756000000.000000")
    got = serialize(bundle(line(5, "다 봤어", who=ME)), loops=loops.live())
    assert f"[{lp.id}]" in got and "#42 본다" in got


def test_남의_방_고리는_안_싣는다(book, loops, frozen):
    """방을 섞으면 모델이 딴 방 고리를 이 방 말로 닫는다."""
    loops.open("딴 방 것", source="slack:D99:1756000000.000000")
    got = serialize(bundle(line(0, "다 봤어", who=ME)), loops=loops.live())
    assert "딴 방 것" not in got


def test_이름_표는_쓰인_것만_싣는다(frozen):
    """워크스페이스 사람이 백 명이면 표가 묶음보다 커진다."""
    got = serialize(bundle(line(0, "<@U7> 봐줄래?")), names={"U7": "팀원A", "U8": "팀원B"})
    assert "팀원A" in got and "팀원B" not in got


def test_줄마다_근거_키가_붙는다(frozen):
    """모델이 `source` 로 되돌려 줄 값이다. 없으면 근거 없는 고리가 된다."""
    x = line(0, "내가 볼게", who=ME)
    assert x.key in serialize(bundle(x))


# ── 겹 2 — 답을 읽는다 ─────────────────────────────────────────────
def test_울타리를_벗긴다():
    """★ 4B 급은 물론이고 큰 모델도 ```json 을 두른다. 형식 하나 때문에
    그 턴을 통째로 버리는 것이 제일 비싼 실패다."""
    got = parse('여기 있습니다:\n```json\n{"opens": [{"text": "본다", "source": "s"}]}\n```')
    assert len(got.opens) == 1 and got.opens[0].text == "본다"


def test_울타리가_없어도_읽는다():
    got = parse('말이 앞에 붙고 {"moves": [{"id": "a1"}]} 뒤에도 붙는다')
    assert len(got.moves) == 1


def test_못_읽어도_예외를_안_던진다():
    got = parse("죄송합니다 JSON 을 못 만들겠어요")
    assert len(got) == 0 and got.dropped


def test_한_줄이_이상해도_나머지는_산다():
    """★ 셋 중 하나가 이상해도 나머지 둘은 성하다. 예외면 그 턴이 통째로
    날아가고 같이 온 성한 판단까지 버려진다."""
    got = parse('{"moves": ["문자열이네"], "opens": [{"text": "본다", "source": "s"}]}')
    assert len(got.opens) == 1
    assert got.dropped


def test_모르는_칸은_버리고_읽는다():
    """모델은 없는 칸을 지어낸다. 그것 때문에 죽으면 안 된다."""
    got = parse('{"opens": [{"text": "본다", "source": "s", "확신도": 0.9}]}')
    assert len(got.opens) == 1


# ── sure 는 규칙이 상한을 건다 ──────────────────────────────────────
def test_문장_안에_대상이_있으면_그대로(book, frozen):
    x = line(0, "#42 는 내가 오늘 볼게", who=ME)
    book.put(x)
    assert certain(Open(text="#42 본다", source=x.key, sure=True), book) is True


def test_자리로_추론한_것은_내린다(book, frozen):
    """★ 모델은 자기 추론을 확신한다 — 추론해서 찾아냈다는 사실 자체가
    확신의 근거가 되지 않는다. 여기가 신뢰의 방파제다."""
    x = line(0, "내가 볼게", who=ME)
    book.put(x)
    assert certain(Open(text="#42 본다", source=x.key, sure=True), book) is False


def test_구조가_명시한_것은_그대로(book, frozen):
    x = line(0, "<@U7> 이거 봐줄래?", who=ME)
    book.put(x)
    assert certain(Open(text="U7 에게 부탁", source=x.key, sure=True), book) is True


def test_근거를_못_찾으면_확신하지_않는다(book, frozen):
    """지어낸 근거일 수도 있다."""
    assert certain(Open(text="뭔가", source="slack:C01:없는것", sure=True), book) is False


def test_모델이_아니라고_하면_올리지_않는다(book, frozen):
    """규칙은 **누르기만** 한다. 올리지는 않는다."""
    x = line(0, "#42 는 내가 볼게", who=ME)
    book.put(x)
    assert certain(Open(text="#42", source=x.key, sure=False), book) is False


# ── 겹 3 — 원장과 대조 ─────────────────────────────────────────────
def test_닫는_것이_여는_것보다_먼저다(book, loops, frozen):
    """★ 못 닫으면 목록이 자라고, 자란 목록은 안 읽힌다."""
    lp = loops.open("#42 본다", source="slack:C01:1756000000.000000")
    x = line(5, "#42 다 봤어", who=ME)
    book.put(x)
    from genie_agents.extract import Move

    got = Extraction(
        moves=[Move(id=lp.id, state=DONE, note="본인이 '다 봤어'", source=x.key)],
        opens=[Open(text="아주 다른 새 일거리", source=x.key, sure=True)],
    )
    셈 = apply(got, loops, book)
    assert 셈 == {"움직임": 1, "열림": 1, "못 씀": 0, "겹침": 0}
    assert loops.get(lp.id).state == DONE


def test_없는_id_를_불러도_그_턴이_안_죽는다(book, loops, frozen):
    """★ 모델은 지워진 id 를 부른다. 예외면 같이 온 성한 판단까지 날아간다."""
    from genie_agents.extract import Move

    x = line(0, "내가 볼게", who=ME)
    book.put(x)
    got = Extraction(moves=[Move(id="없는것", state=DONE)],
                     opens=[Open(text="성한 것", source=x.key)])
    셈 = apply(got, loops, book)
    assert 셈["못 씀"] == 1 and 셈["열림"] == 1
    assert any("없는 고리 id" in d for d in got.dropped)


def test_없는_상태를_지어내도_안_죽는다(book, loops, frozen):
    from genie_agents.extract import Move

    lp = loops.open("본다", source="slack:C01:1756000000.000000")
    got = Extraction(moves=[Move(id=lp.id, state="완료함")])
    assert apply(got, loops, book)["못 씀"] == 1
    assert loops.get(lp.id).state == OPEN          # 안 건드렸다


def test_근거_없는_고리는_안_연다(book, loops, frozen):
    """★ 사람이 확인할 길이 없으면 한 번 틀렸을 때 원장 전체를 안 믿게 된다."""
    got = Extraction(opens=[Open(text="이거 하기로 했잖아", source="")])
    assert apply(got, loops, book)["못 씀"] == 1
    assert len(loops) == 0


def test_같은_근거로는_두_번_안_열린다(book, loops, frozen):
    """리허설을 두 번 돌려도 원장이 두 배가 되지 않는다."""
    x = line(0, "내가 볼게", who=ME)
    book.put(x)
    got = Extraction(opens=[Open(text="본다", source=x.key)])
    apply(got, loops, book)
    apply(Extraction(opens=[Open(text="본다", source=x.key)]), loops, book)
    assert len(loops) == 1


def test_규칙이_원장에_들어가는_순간에_눌린다(book, loops, frozen):
    """모델이 sure=True 라고 해도, 문장 안에 대상이 없으면 내려서 넣는다."""
    x = line(0, "내가 볼게", who=ME)
    book.put(x)
    apply(Extraction(opens=[Open(text="#42 본다", source=x.key, sure=True)]), loops, book)
    assert loops.live()[0].sure is False


# ── 도구를 안 넘긴다 ───────────────────────────────────────────────
def test_추출은_도구를_안_넘긴다():
    """★ 도구를 넘기면 모델이 도구를 부르려 들고, 그 턴들이 전부 값이다.
    여기가 원하는 것은 JSON 하나뿐이다."""
    본것 = {}

    class Fake:
        class messages:
            @staticmethod
            def create(**kw):
                본것.update(kw)

                class R:
                    content = [type("T", (), {"text": '{"opens": []}'})()]

                return R()

    out = ask(Fake(), "m", "지침", "묶음")
    assert out == '{"opens": []}'
    assert "tools" not in 본것
    assert 본것["system"] == "지침"


# ── 근거 없이는 안 닫는다 ──────────────────────────────────────────
def test_근거_없이_닫으려_하면_안_닫는다(book, loops, frozen):
    """★ 실측에서 모델이 이렇게 닫았다 — *"본인이 '다 확인했어'를 말하지
    않았으나, 전반적으로 완료된 것으로 판단됨."* 닫는 말이 없는데 닫은 것이다.

    잘못 닫으면 그 고리는 목록에서 **조용히 사라진다.** 안 닫힌 고리는 목록에
    남아서 사람이 지울 수 있지만, 잘못 닫힌 고리는 사람이 볼 기회 자체가 없다.
    두 실패의 값이 대칭이 아니다."""
    from genie_agents.extract import Move

    lp = loops.open("본다", source="slack:C01:1756000000.000000")
    셈 = apply(Extraction(moves=[Move(id=lp.id, state=DONE, note="느낌상 끝났다")]),
             loops, book)
    assert 셈["못 씀"] == 1
    assert loops.get(lp.id).state == OPEN


def test_묶음에_없는_근거로는_못_닫는다(book, loops, frozen):
    """모델은 그 묶음만 봤다. 거기 없는 말을 근거로 댔으면 지어낸 것이다."""
    from genie_agents.extract import Move

    lp = loops.open("본다", source="slack:C01:1756000000.000000")
    x = line(0, "딴 얘기", who=ME)
    book.put(x)
    셈 = apply(Extraction(moves=[Move(id=lp.id, state=DONE, source="slack:C01:없는말")]),
             loops, book, bundle=bundle(x))
    assert 셈["못 씀"] == 1 and loops.get(lp.id).state == OPEN


def test_근거가_묶음_안에_있으면_닫는다(book, loops, frozen):
    from genie_agents.extract import Move

    lp = loops.open("본다", source="slack:C01:1756000000.000000")
    x = line(5, "다 봤어", who=ME)
    book.put(x)
    셈 = apply(Extraction(moves=[Move(id=lp.id, state=DONE, source=x.key)]),
             loops, book, bundle=bundle(x))
    assert 셈["움직임"] == 1 and loops.get(lp.id).state == DONE


def test_그냥_움직이는_것은_근거를_안_묻는다(book, loops, frozen):
    """상태를 안 닫는 움직임은 되돌릴 수 있다. 문턱을 같게 둘 이유가 없다."""
    from genie_agents.extract import Move

    lp = loops.open("본다", source="slack:C01:1756000000.000000")
    assert apply(Extraction(moves=[Move(id=lp.id, note="찔렀다")]), loops, book)["움직임"] == 1


# ── 같은 일을 두 번 안 연다 ────────────────────────────────────────
def test_같은_일이_다른_줄에서_또_나와도_한_고리다(book, loops, frozen):
    """★ `open()` 은 근거가 같을 때만 막는다. 그런데 실측에서 한 고리가
    **세 번** 열렸다 — 근거가 다 달라서 막을 수가 없었다."""
    a, b = line(0, "말1", who=ME), line(9, "말2", who=ME)
    book.put_many([a, b])
    apply(Extraction(opens=[Open(text="파트 1: UI 수정 및 사용자 안내 추가", source=a.key)]),
          loops, book)
    셈 = apply(Extraction(opens=[Open(text="파트 1: UI 수정 및 사용자 안내 추가", source=b.key)]),
             loops, book)
    assert 셈["겹침"] == 1 and 셈["열림"] == 0
    assert len(loops.live()) == 1


def test_겹친_것을_버리지_않고_움직임으로_적는다(book, loops, frozen):
    """다시 나왔다는 것 자체가 그 고리가 아직 살아 있다는 정보다.
    안 적으면 조용한 날짜만 보고 찌르게 된다."""
    a, b = line(0, "말1", who=ME), line(9, "말2", who=ME)
    book.put_many([a, b])
    apply(Extraction(opens=[Open(text="figma 리뷰한다", source=a.key)]), loops, book)
    apply(Extraction(opens=[Open(text="mr Khôi figma 리뷰한다", source=b.key)]), loops, book)
    lp = loops.live()[0]
    assert len(lp.moves) == 2 and "또 나왔다" in lp.moves[-1].note


def test_다른_일은_안_접는다(book, loops, frozen):
    """실측 간격: 진짜 중복 0.57~1.00, 남남 0.00~0.07. 사이가 비어 있다."""
    a, b = line(0, "말1", who=ME), line(9, "말2", who=ME)
    book.put_many([a, b])
    apply(Extraction(opens=[Open(text="BE 리뷰 요청", source=a.key)]), loops, book)
    apply(Extraction(opens=[Open(text="subdomain 블랙리스트 적용", source=b.key)]), loops, book)
    assert len(loops.live()) == 2


def test_닫힌_고리와는_안_겹친다(book, loops, frozen):
    """`similar` 는 살아 있는 것만 본다 — 닫힌 일이 다시 열리는 것은
    같은 일이 아니라 **다시 생긴 일**이다."""
    a, b = line(0, "말1", who=ME), line(9, "말2", who=ME)
    book.put_many([a, b])
    apply(Extraction(opens=[Open(text="figma 리뷰한다", source=a.key)]), loops, book)
    loops.close(loops.live()[0].id)
    셈 = apply(Extraction(opens=[Open(text="figma 리뷰한다", source=b.key)]), loops, book)
    assert 셈["열림"] == 1


# ── 내 이름이 남으로 안 잡힌다 ─────────────────────────────────────
def test_남들이_부르는_내_이름도_나다(book, loops, frozen):
    """★ 실측에서 이게 없어서 본인 일이 남 일로 잡혔다. 팀원들이 본인을
    "mr Khôi" 라고 부르는데 이름 표에는 id 밖에 없었다. 내 일을 남 일로 세면
    원장이 **조용히** 틀린다 — 목록에 줄은 그대로 있어서 눈치채기 어렵다."""
    from genie_agents.extract import me_names

    names = {"U0ME": "Daniel (Khôi)", "U7": "Nghia Tran Quang"}
    별칭 = me_names(names, "U0ME")
    x = line(0, "본다", who=ME)
    book.put(x)
    apply(Extraction(opens=[Open(text="figma 본다", source=x.key, owner="mr Khôi")]),
          loops, book, names=names, mine=별칭)
    assert loops.live()[0].owner == ME


def test_날_id_는_이름으로_바뀐다(book, loops, frozen):
    names = {"U0ME": "Daniel (Khôi)", "U7": "Nghia Tran Quang"}
    x = line(0, "본다", who=ME)
    book.put(x)
    apply(Extraction(opens=[Open(text="BE 본다", source=x.key, owner="U7")]),
          loops, book, names=names, mine=["U0ME", "Daniel", "Khôi"])
    assert loops.live()[0].owner == "Nghia Tran Quang"


def test_주인이_비면_나다(book, loops, frozen):
    x = line(0, "본다", who=ME)
    book.put(x)
    apply(Extraction(opens=[Open(text="본다", source=x.key, owner="")]), loops, book)
    assert loops.live()[0].owner == ME
