"""붙은 것을 다루는 자리 — 줄이기와 풀기.

여기 있는 시험 전부가 지키는 것은 하나다. **사진 한 장이 사라지지 않는 것.**
줄이다 실패하든 표시가 가짜든 방향 표가 깨졌든, 잃어도 되는 것은 표시뿐이고
사진과 말은 남아야 한다.
"""

from genie_agents import media as M

SOI = bytes.fromhex("ffd8")  # JPEG 시작
APP1 = bytes.fromhex("ffe1")  # EXIF 가 들어가는 조각
SOS = bytes.fromhex("ffda0002")  # 그림 자료 시작 — 여기부터는 태그가 없다
PNG = bytes.fromhex("89504e470d0a1a0a")


class 저장소:
    """`store.get` 만 본다 — `drop_unknown` 이 그것만 쓴다."""

    def __init__(self, *ids):
        self.ids = set(ids)

    def get(self, mid):
        return object() if mid in self.ids else None


# ── 줄이기 ────────────────────────────────────────────────────────────
def test_작은_사진은_안_건드린다():
    """줄이는 값(ffmpeg 한 번)이 아까운 크기다."""
    raw = PNG + bytes(100)
    assert M.shrink(raw, "image/png") == (raw, "image/png")


def test_소리와_영상은_안_줄인다():
    """소리는 이미 작고, 영상은 다시 인코딩하는 값이 크고 잃는 것도 다르다."""
    big = bytes(M.SHRINK_FLOOR + 1)
    assert M.shrink(big, "audio/mpeg") == (big, "audio/mpeg")
    assert M.shrink(big, "video/mp4") == (big, "video/mp4")


def test_줄이다_실패해도_사진은_그대로_돌아온다(monkeypatch):
    """**사진 한 장을 완벽하게 줄이는 것보다 사진이 가는 것이 먼저다.**"""
    big = PNG + bytes(M.SHRINK_FLOOR)
    monkeypatch.setattr("shutil.which", lambda name: None)  # ffmpeg 이 없다
    assert M.shrink(big, "image/png") == (big, "image/png")


def test_형식을_모르면_안_건드린다():
    big = bytes(M.SHRINK_FLOOR + 1)
    assert M.shrink(big, "") == (big, "")


# ── 방향 ──────────────────────────────────────────────────────────────
#
# 폰은 사진을 가로로 저장하고 "돌려서 봐라" 를 EXIF 에 적는다. 다시 인코딩하면
# 그 표시가 날아가므로 픽셀을 실제로 돌려야 한다 — 안 그러면 세로로 찍은
# 사진이 눕는다. 큰 사진을 작게 만들려다 사진을 눕히면 안 된다.


def _jpeg_facing(value: int) -> bytes:
    """EXIF 방향 하나만 든 최소 JPEG. little-endian TIFF."""
    ifd = (
        (1).to_bytes(2, "little")  # 태그 한 개
        + (0x0112).to_bytes(2, "little")  # Orientation
        + (3).to_bytes(2, "little")  # SHORT
        + (1).to_bytes(4, "little")  # 개수 1
        + value.to_bytes(2, "little")
        + bytes(2)
        + bytes(4)  # 다음 IFD 없음
    )
    tiff = b"II" + (42).to_bytes(2, "little") + (8).to_bytes(4, "little") + ifd
    app1 = b"Exif" + bytes(2) + tiff
    return SOI + APP1 + (len(app1) + 2).to_bytes(2, "big") + app1 + SOS


def test_방향_표를_읽는다():
    for want in (1, 3, 6, 8):
        assert M.orientation(_jpeg_facing(want)) == want


def test_표가_없으면_안_돌린다():
    assert M.orientation(SOI + SOS) == 1
    assert M.orientation(PNG) == 1
    assert M.orientation(b"") == 1


def test_깨진_것을_읽어도_안_터진다():
    """못 읽으면 안 돌린다. 읽다 터져서 사진이 사라지면 안 된다."""
    assert M.orientation(SOI + APP1 + b"\xff\xff" + b"Exif" + bytes(2) + b"rubbish") == 1
    assert M.orientation(SOI + APP1) == 1


def test_돌리는_필터가_여덟_가지_다_있다():
    """EXIF 는 1~8 을 쓴다. 하나라도 빠지면 그 사진만 눕는다."""
    assert sorted(M.TURN) == [1, 2, 3, 4, 5, 6, 7, 8]
    assert M.TURN[1] == "", "1 은 안 돌리는 것이다"


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
