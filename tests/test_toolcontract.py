"""도구 계약 — 같은 이름이면 같은 인자여야 한다."""

from __future__ import annotations

from genie_agents import toolcontract as TC


def spec(name, *args):
    return {
        "name": name,
        "description": "",
        "input_schema": {"type": "object", "properties": {a: {} for a in args}},
    }


def test_이름이_겹치면_인자도_같아야_한다():
    갈린_것 = TC.check_shared(
        ("alpha", [spec("note", "text")]),
        ("beta", [spec("note", "note")]),
    )
    assert [d.tool for d in 갈린_것] == ["note"]
    assert TC.undeclared(갈린_것), "안 적혔으면 걸려야 한다"


def test_이유를_적으면_통과한다(monkeypatch):
    """다른 것 자체를 막지 않는다 — 적지 않고 다른 것을 막는다."""
    monkeypatch.setitem(TC.DIVERGENT, "note", "하는 일이 다르다")
    갈린_것 = TC.check_shared(
        ("alpha", [spec("note", "text")]),
        ("beta", [spec("note", "note")]),
    )
    assert not TC.undeclared(갈린_것)


def test_이름만_겹치고_인자가_같으면_안_걸린다():
    assert not TC.check_shared(
        ("alpha", [spec("note", "note")]),
        ("beta", [spec("note", "note")]),
    )


def test_맞춰_놓고_안_지운_표시를_잡는다(monkeypatch):
    """없는 것을 가리키는 표시는 지운다 — 안 그러면 다음 사람이 다시 벌린다."""
    monkeypatch.setitem(TC.DIVERGENT, "note", "옛날에는 달랐다")
    갈린_것 = TC.check_shared(
        ("alpha", [spec("note", "note")]),
        ("beta", [spec("note", "note")]),
    )
    assert TC.stale_declarations(갈린_것) == ["note"]


def test_못박은_시그니처를_어기면_걸린다(monkeypatch):
    monkeypatch.setitem(TC.CANON, "principle_record",
                        TC.PRINCIPLE_TOOLS["principle_record"])
    어긴_것 = TC.check_canon([spec("principle_record", "text", "reason")])
    assert [d.tool for d in 어긴_것] == ["principle_record"]
    assert not TC.check_canon(
        [spec("principle_record", "agent_id", "principle", "tentative")]
    )


def test_안_가진_도구는_안_본다(monkeypatch):
    """골격이 도구를 강요하지 않는다. 가진 것만 검사한다."""
    monkeypatch.setitem(TC.CANON, "principle_record",
                        TC.PRINCIPLE_TOOLS["principle_record"])
    assert not TC.check_canon([spec("전혀_다른_도구", "x")])
