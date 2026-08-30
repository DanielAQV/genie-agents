"""배역은 골격이 안 정한다 — 쓰는 쪽이 선언한다.

이 시험은 **일부러 낯선 배역**을 쓴다. 골격이 특정 등장인물을 알고 있으면
여기서 깨진다. 만든 계기가 그거였다 — 골격이 처음 나온 저장소의 에이전트
이름과 방 이름이 코드에 박혀 있었고, 그 상태로는 남에게 못 준다.
"""

from __future__ import annotations

import pytest

from genie_agents import env
from genie_agents.messaging import peers

CAST = peers.Cast(
    agents=("scribe", "warden"),
    humans=("chief",),
    rooms={
        "scribe-warden": ("scribe", "warden"),
        "scribe-chief": ("scribe", "chief"),
        "warden-chief": ("warden", "chief"),
        "all-three": ("scribe", "warden", "chief"),
    },
    speakers={"scribe": "서기", "warden": "관리", "chief": "대표"},
)


@pytest.fixture(autouse=True)
def _stage(monkeypatch):
    monkeypatch.setenv(env.VAR, "SCRIBE")
    peers.identify("SCRIBE", "scribe", CAST)
    yield


def test_방_종류는_사람_수가_정한다():
    """따로 적게 하면 적는 사람이 언젠가 안 맞게 적는다."""
    assert CAST.room_type("scribe-chief") == "dm"
    assert CAST.room_type("all-three") == "group"


def test_모르는_사람이_방에_있으면_안_만들어진다():
    with pytest.raises(ValueError, match="모르는 사람"):
        peers.Cast(agents=("a",), humans=("b",), rooms={"r": ("a", "ghost")})


def test_혼자_있는_방은_없다():
    with pytest.raises(ValueError, match="둘은 있어야"):
        peers.Cast(agents=("a",), humans=("b",), rooms={"r": ("a",)})


def test_안_끼어_있는_방에는_못_보낸다():
    """`warden-chief` 에 서기는 없다."""
    with pytest.raises(peers.InvalidMessage):
        peers.compose("몰래", room_id="warden-chief", sender="scribe")


def test_peer_only_가_아무에게도_안_가면_거절한다():
    """사람과 단둘인 방에서는 뜻이 없다 — 가릴 상대가 곧 유일한 수신자다."""
    with pytest.raises(peers.InvalidMessage, match="아무에게도 안 간다"):
        peers.compose("x", room_id="scribe-chief", sender="scribe",
                      visibility=peers.PEER_ONLY)


def test_peer_only_는_섞인_방에서는_뜻이_있다():
    """에이전트도 사람도 있는 방에서는 에이전트만 본다. 그건 막지 않는다."""
    m = peers.compose("둘만 아는 얘기", room_id="all-three", sender="scribe",
                      visibility=peers.PEER_ONLY)
    assert CAST.recipients(m) == frozenset({"warden"})


def test_배달은_뺄셈_하나다():
    """방 사람들 − 보낸 사람 − (peer_only 면 사람들)."""
    m = peers.compose("다들 보자", room_id="all-three", sender="scribe")
    assert CAST.recipients(m) == frozenset({"warden", "chief"})

    은밀 = peers.compose("둘만", room_id="all-three", sender="scribe",
                         visibility=peers.PEER_ONLY)
    assert CAST.recipients(은밀) == frozenset({"warden"})


def test_보는_쪽에_따라_내_말인지_갈린다():
    내_말 = peers.compose("내가 한 말", room_id="scribe-chief", sender="scribe")
    남의_말 = peers.compose("대표가 한 말", room_id="scribe-chief", sender="chief")

    assert 내_말.mine and not 내_말.from_human
    assert 남의_말.from_human and not 남의_말.mine
    assert 남의_말.speaker == "대표"


def test_배역을_안_정하면_분명하게_죽는다():
    """조용히 빈 배역으로 도는 것보다 낫다 — 그러면 아무 방에도 못 보내는데
    이유를 모른다."""
    peers._who.clear()
    with pytest.raises(RuntimeError, match="배역이 없다"):
        peers.stage()
