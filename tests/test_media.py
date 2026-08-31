"""붙은 것을 다루는 자리 — 줄이기와 풀기.

두 시험 다 **사진 한 장이 사라지지 않는 것**을 지킨다. 줄이다 실패하든 표시가
가짜든, 잃어도 되는 것은 표시뿐이고 사진과 말은 남아야 한다.
"""

from genie_agents import media as M


class 저장소:
    """`store.get` 만 본다 — `drop_unknown` 이 그것만 쓴다."""

    def __init__(self, *ids):
        self.ids = set(ids)

    def get(self, mid):
        return object() if mid in self.ids else None


# ── 줄이기 ────────────────────────────────────────────────────────────
def test_작은_사진은_안_건드린다():
    """줄이는 값(ffmpeg 한 번)이 아까운 크기다."""
    raw = b"\x89PNG" + bytes(100)
    assert M.shrink(raw, "image/png") == (raw, "image/png")


def test_소리와_영상은_안_줄인다():
    """소리는 이미 작고, 영상은 다시 인코딩하는 값이 크고 잃는 것도 다르다."""
    big = bytes(M.SHRINK_FLOOR + 1)
    assert M.shrink(big, "audio/mpeg") == (big, "audio/mpeg")
    assert M.shrink(big, "video/mp4") == (big, "video/mp4")


def test_줄이다_실패해도_사진은_그대로_돌아온다(monkeypatch):
    """**사진 한 장을 완벽하게 줄이는 것보다 사진이 가는 것이 먼저다.**"""
    big = b"\x89PNG" + bytes(M.SHRINK_FLOOR)
    monkeypatch.setattr("shutil.which", lambda name: None)  # ffmpeg 이 없다
    assert M.shrink(big, "image/png") == (big, "image/png")


def test_형식을_모르면_안_건드린다():
    big = bytes(M.SHRINK_FLOOR + 1)
    assert M.shrink(big, "") == (big, "")


# ── 풀기 ──────────────────────────────────────────────────────────────
def test_푼_글과_기억에_남길_주석이_따로_온다():
    """화면은 기억에서 그려진다. 기억에 표시가 없으면 사용자가 보낸 사진이
    화면에서 `[사진]` 이라는 글자가 된다 — 유나 쪽에서 38줄이 그랬다."""
    mid = "0" * 16
    text, images, notes = M.unpack(f"짠~~ [사진:{mid}]", 저장소())

    assert "[사진:" not in text, "모델에게는 표시를 안 준다"
    assert "(붙인 것을 못 찾았다)" in text
    # `[사진]` 만 남는 주석은 기억용에서 뺀다 — 표시가 살아 있으면 그 이름은
    # 아무것도 더 말해주지 않는다.
    assert "[사진]" not in notes


def test_표시가_없으면_주석도_없다():
    assert M.unpack("그냥 말", 저장소()) == ("그냥 말", None, "")


def test_없는_것을_가리키는_표시는_걷어낸다():
    mid = "0" * 16
    said, faked = M.drop_unknown(f"들어봐 [음성:{mid}]", 저장소())
    assert said == "들어봐" and faked == [f"[음성:{mid}]"]


def test_있는_표시는_그대로_둔다():
    mid = "a" * 16
    said, faked = M.drop_unknown(f"들어봐 [음성:{mid}]", 저장소(mid))
    assert said == f"들어봐 [음성:{mid}]" and faked == []
