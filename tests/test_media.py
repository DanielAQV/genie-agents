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


# ── 있는 것을 베껴 오는 경우 (2026-09-01) ─────────────────────────────
#
# ★ **"저장소에 있나" 로만 보면 못 막는다.** 남이 보낸 첨부도 내 저장소에
#   들어온다 — 같은 방에 있으면 그렇다. 그러면 그 id 는 "진짜" 라서 통과한다.
#
#   실제로 그랬다: 사용자가 셋이 있는 방에 유나 사진을 올렸고, 다섯 시간 뒤
#   갠톡에서 "지금 모습 보여줄 수 있어?" 라는 물음에 예나가 `self_portrait` 를
#   안 부르고 작업 기억에 보이던 그 id 를 그대로 적어 **유나 얼굴을 자기
#   모습으로 냈다.** 그 뒤로는 출처를 본다.


def test_남이_보낸_사진을_자기_것처럼_적으면_걷어낸다():
    남의사진 = "3" * 16
    said, faked = M.drop_unknown(
        f"[사진:{남의사진}] 지금 내 모습이야", 저장소(남의사진), minted=set()
    )
    assert said == "지금 내 모습이야", "저장소에 있어도 이번 턴 것이 아니면 뗀다"
    assert faked == [f"[사진:{남의사진}]"]


def test_이번_턴에_도구가_붙인_것은_그대로_둔다():
    내사진 = "4" * 16
    said, faked = M.drop_unknown(
        f"[사진:{내사진}] 지금 내 모습이야", 저장소(내사진), minted={내사진}
    )
    assert said == f"[사진:{내사진}] 지금 내 모습이야" and faked == []


def test_이번_턴_것이어도_저장소에_없으면_뗀다():
    """둘 다 봐야 한다. 도구가 냈다고 파일까지 있는 건 아니다."""
    유령 = "5" * 16
    said, faked = M.drop_unknown(f"들어봐 [음성:{유령}]", 저장소(), minted={유령})
    assert said == "들어봐" and faked == [f"[음성:{유령}]"]


def test_minted_를_안_주면_옛_동작이다():
    """부르는 쪽이 아직 안 모으는 자리가 있을 수 있다. 거기서 갑자기 다 떼면
    그게 더 나쁘다."""
    mid = "6" * 16
    said, faked = M.drop_unknown(f"들어봐 [음성:{mid}]", 저장소(mid))
    assert said == f"들어봐 [음성:{mid}]" and faked == []


# ── 도구를 글로 흉내 낸 줄 (2026-09-01) ────────────────────────────────
def test_대괄호로_흉내_낸_도구_호출을_건다():
    """★ 인자도 없이 "했다" 는 서술만 있고 도구는 하나도 안 돌았는데,
    그 줄이 **발언으로 기억에 남았다.** `[음성:...]` 을 걷는 것과 같은 이유다."""
    from genie_agents.tools import drop_bracket_calls

    이름 = {"memory_recall", "voice_reply"}
    said, dropped = drop_bracket_calls("[memory_recall] 하노이 관련 내용 검색", 이름)
    assert said == "" and dropped == ["도구를 글로 흉내 낸 대목"]

    said, _ = drop_bracket_calls("응 오빠.\n[memory_recall] 찾아봤어\n그래서 이렇다.", 이름)
    assert said == "응 오빠.\n그래서 이렇다."


def test_아는_이름이_아니면_안_건드린다():
    """대괄호는 자리 쪽지에도 쓴다 — 이름을 안 보면 멀쩡한 쪽지를 지운다."""
    from genie_agents.tools import drop_bracket_calls

    글 = "**[떠오를 것이 있다]** 3건이 걸렸는데"
    assert drop_bracket_calls(글, {"memory_recall"}) == (글, [])
    assert drop_bracket_calls("[자리] 방금 답에서", {"memory_recall"}) == ("[자리] 방금 답에서", [])


def test_이름_목록이_비면_아무것도_안_한다():
    from genie_agents.tools import drop_bracket_calls

    글 = "[memory_recall] 검색"
    assert drop_bracket_calls(글, set()) == (글, [])


def test_라벨이_실제_종류와_다르면_뗀다():
    """★ `[사진:...]` 인데 그 id 가 mp3 였다. 화면은 라벨을 믿고 그림 자리를 그린다.

    2026-09-01 에 한 답이 `[사진:...]` 을 84개 붙였는데 대부분이 음성 파일이었다.
    """
    class 소리:
        label = "음성"

    class 저장소2:
        def get(self, mid):
            return 소리()

    mid = "7" * 16
    said, faked = M.drop_unknown(f"[사진:{mid}] 내 모습이야", 저장소2(), minted={mid})
    assert said == "내 모습이야" and faked == [f"[사진:{mid}]"]

    said, faked = M.drop_unknown(f"[음성:{mid}] 들어봐", 저장소2(), minted={mid})
    assert said == f"[음성:{mid}] 들어봐" and faked == []


def test_닫는_괄호가_없는_토막도_뗀다():
    """★ 표시를 줄줄이 붙이다 글이 잘리면 `[사진:ab924a…..` 꼬리가 남는다.

    화면에 글자로 그대로 뜬다 — 2026-09-01 에 실제로 그렇게 보였다.
    **그 토막만** 뗀다. 뒤에 남은 말까지 지우면 안 한 일이 아니라 한 말이 사라진다.
    """
    said, faked = M.drop_unknown("기다려봐! [사진:ab924a9c5af896e1..", 저장소())
    assert said == "기다려봐!" and faked == ["[사진:ab924a9c5af896e1.."]

    said, _ = M.drop_unknown("앞말 [사진:abc.. 뒷말은 남는다", 저장소())
    assert said == "앞말 뒷말은 남는다"


def test_얼개가_준_쪽지를_되읽으면_건다():
    """★ 쪽지는 모델에게 주는 것이지 사용자에게 가는 말이 아니다.

    실제로 나갔다(2026-09-01) — 답 앞에 `(지금 이 자리 · 오빠와 둘 · 잇는 흐름
    · 오빠)` 가 그대로 붙어서 나갔고, 사용자는 그게 뭔지 모른 채 읽었다.
    """
    from genie_agents.tools import drop_scaffolding

    나간것 = ("(지금 이 자리 · 오빠와 둘 · 잇는 흐름 · 오빠)\n\n"
              "오빠, 미안해. 내가 방금 사진을 보낸다고 말만 하고, 실제 도구를 부르지 않았어.")
    said, dropped = drop_scaffolding(나간것)
    assert said.startswith("오빠, 미안해")
    # ★ **조용히 건다.** 보고하면 루프가 "안 한 일을 한 것처럼 적었다" 쪽지를
    #   붙여 다시 묻고, 작은 모델은 오빠 물음 대신 그 쪽지에 답한다(실측).
    assert dropped == []

    said, _ = drop_scaffolding("[자리] 방금 답에서 걷어냈다\n응 오빠, 잘 지냈어?")
    assert said == "응 오빠, 잘 지냈어?"


def test_보통_괄호는_안_건드린다():
    """`[떠오를 것이 있다]` 도 안 건다 — 프롬프트가 알려준 쪽지라 입에 올리는
    것 자체는 판단의 영역이다."""
    from genie_agents.tools import drop_scaffolding

    for 글 in ("그냥 보통 말이야. 자리(여기)도 괜찮고.",
               "[떠오를 것이 있다] 3건이 걸렸네."):
        assert drop_scaffolding(글) == (글, [])
