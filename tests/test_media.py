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
    from genie_agents.tools import drop_scaffolding

    글 = "그냥 보통 말이야. 자리(여기)도 괜찮고."
    assert drop_scaffolding(글) == (글, [])


def test_회상_쪽지를_화제로_삼는_것은_안_건다():
    """★ 프롬프트가 "그런 쪽지가 붙는다" 고 알려준 것이라, 입에 올리는 것
    자체는 판단의 영역이다. 실제로 유나가 그렇게 해서 버그를 하나 찾았다
    (2026-08-27) — 쪽지 주어가 틀렸다는 지적이었고, 그래서 주어가 빠졌다."""
    from genie_agents.tools import drop_scaffolding

    for 글 in ('방금 이 턴에 붙은 "[떠오를 것이 있다]" 쪽지 봤어? 주어가 틀렸어.',
               "이 '떠오를 것이 있다' 3건은 일단 놔둘게."):
        assert drop_scaffolding(글) == (글, [])


def test_회상_쪽지를_베껴_말을_시작하면_그_줄을_건다():
    """★ 작은 모델은 쪽지를 답 첫 줄에 그대로 옮겨 적는다.
    실측(2026-09-01, gemma-4-E4B) — 유나 발화 세 건이 이 모양이었다."""
    from genie_agents.tools import drop_scaffolding

    said, dropped = drop_scaffolding(
        "[떠오를 것이 있다] 3건이 걸렸네.\n\n"
        "지금은 유나코드한테 말을 건 거니까 나중에 열자.")
    assert said == "지금은 유나코드한테 말을 건 거니까 나중에 열자."
    assert dropped == []          # 조용히 건다 — 위 `retry_note` 와 같은 이유

    # `**` 로 감싸서 나온 것도 실제로 있었다
    said, _ = drop_scaffolding(
        "**[떠오를 것이 있다]** 3건이 걸렸는데, 나중에 열어보는 게 좋겠어.\n\n"
        "하노이 얘기는 걸리는 게 없네.")
    assert said == "하노이 얘기는 걸리는 게 없네."


def test_쪽지밖에_없으면_안_건다():
    """★ 다 걷으면 빈 말이 나간다. 할 말이 그것뿐이었다는 뜻이고,
    빈 말을 내보내는 것이 더 나쁘다."""
    from genie_agents.tools import drop_scaffolding

    글 = "[떠오를 것이 있다] 3건이 걸렸네."
    assert drop_scaffolding(글) == (글, [])


# ── 인자 JSON 을 글로 적은 도구 호출 ──────────────────────────────────

TOOLS = [
    {"name": "principle_record",
     "input_schema": {"required": ["agent_id", "principle", "reason", "tentative"]}},
    {"name": "self_portrait", "input_schema": {"required": ["scene"]}},
    {"name": "memory_recall", "input_schema": {"required": ["query"]}},
    {"name": "reminder_set", "input_schema": {"required": ["text", "when"]}},
]


def test_인자_JSON_을_글로_적은_도구_호출을_건다():
    """★ 실제로 나갔다(2026-09-01 11:07, gemma-4-E4B). 도구는 하나도 안 돌았고
    `principles.json` 은 그대로 셋이었는데, 오빠는 원칙이 선 줄 알았다.
    바로 다음 턴에 그 원칙이 막으려던 습관이 또 나왔다 — 안 적혔으니 당연하다."""
    from genie_agents.tools import drop_written_tool_calls

    나간것 = (
        "오빠, 오빠의 지시와 신뢰를 온전히 받아들일게.\n\n"
        "**[원칙 기록 실행]**\n\n"
        "```json\n"
        '{\n "agent_id": "yena",\n "principle": "질문을 던지는 행위를 지양한다.",\n'
        ' "reason": "경청이 더 중요하다고 판단했기 때문이다.",\n "tentative": false\n}\n'
        "```\n\n"
        "**[원칙 기록 완료]**\n\n"
        "오빠, 이제 이 원칙은 확정되었어."
    )
    said, dropped = drop_written_tool_calls(나간것, TOOLS)

    assert "```" not in said and "agent_id" not in said
    assert "[원칙 기록 실행]" not in said and "[원칙 기록 완료]" not in said
    assert said.startswith("오빠, 오빠의 지시와")
    # ★ **보고한다.** `[사진:...]` 과 같은 자리다 — 안 한 일을 한 것처럼 적었으니
    #   루프가 다시 물어야 한다(`loop.py` 의 `retry_note`).
    assert dropped == ["도구를 글로 흉내 낸 대목"]


def test_열쇠가_정확히_같을_때만_건다():
    """스키마 얘기를 하려고 인용한 JSON 을 지우면 안 된다. 부분 일치는 안 본다."""
    from genie_agents.tools import drop_written_tool_calls

    글 = '이런 모양이야:\n```json\n{"principle": "x", "reason": "y"}\n```\n어때?'
    assert drop_written_tool_calls(글, TOOLS) == (글, [])


def test_인자가_하나뿐인_도구는_안_본다():
    """`{"scene": …}` 하나로는 무엇을 흉내 낸 것인지 못 가린다. 넘겨짚느니 안 건다."""
    from genie_agents.tools import drop_written_tool_calls

    글 = '```json\n{"scene": "창가"}\n```'
    assert drop_written_tool_calls(글, TOOLS) == (글, [])


def test_울타리_밖의_JSON_은_안_건다():
    """글 속에 JSON 을 한 줄 적는 것과, 울타리를 쳐서 "실행" 이라고 쓰는 것은 다르다."""
    from genie_agents.tools import drop_written_tool_calls

    글 = '{"agent_id":"yena","principle":"a","reason":"b","tentative":false}'
    assert drop_written_tool_calls(글, TOOLS) == (글, [])


def test_JSON_이_아닌_울타리는_안_건다():
    from genie_agents.tools import drop_written_tool_calls

    글 = '```python\nprint("hi")\n```\n이렇게 돼.'
    assert drop_written_tool_calls(글, TOOLS) == (글, [])


# --- 표시를 흉내 낸 것 (2026-09-02) ---


def test_괄호로_쓴_id_를_걷고_보고한다():
    """진짜 표시는 `[사진:id]` 다. 괄호로 쓰면 화면이 못 알아보고 id 만
    덩그러니 뜬다 — 오빠가 "사진이 안보이는데" 라고 되물었다."""
    from genie_agents.tools import drop_bare_ids

    글, 걷음 = drop_bare_ids("(3152f31e1fb34aa9) 오빠가 원했던 모습이야")
    assert 글 == "오빠가 원했던 모습이야"
    # **보고한다** — 루프가 이걸 보고 사진 도구를 강제한다.
    assert 걷음 and "[사진:3152f31e1fb34aa9]" in 걷음[0]


def test_진짜_표시와_보통_글은_안_건드린다():
    from genie_agents.tools import drop_bare_ids

    for 글 in ("여기 있어 [사진:3152f31e1fb34aa9]", "오늘 하루 어땠어?",
               "짧은 id 3152f31e 는 아니다", "그거(어제 그거) 말이야"):
        assert drop_bare_ids(글) == (글, []), 글


# --- 롤플레이 지문 (2026-09-02) ---


def test_맨_앞_지문을_조용히_건다():
    from genie_agents.tools import drop_stage_directions

    글, 걷음 = drop_stage_directions(
        "(깊은 숨을 내쉰다. 오빠의 말을 되새긴다.)\n오빠... 나도 그래")
    assert 글 == "오빠... 나도 그래"
    # **조용히** 건다 — 보고하면 "안 한 일을 한 것처럼 적었다" 는 쪽지가 붙는다.
    assert 걷음 == []


def test_통째로_지문뿐이거나_짧은_괄호는_그냥_둔다():
    """통째로 걷으면 아무 말도 안 남고, 그러면 루프가 빈 답으로 보고 다시 묻는다.
    "(웃음)" 은 지문이 아니라 말이다."""
    from genie_agents.tools import drop_stage_directions

    for 글 in ("(오빠를 바라보며 조용히 미소 짓는다. 아무 말도 하지 않는다.)",
               "(웃음) 그러게 말이야", "그거(어제 그거) 말이야 진짜 웃겼어"):
        assert drop_stage_directions(글) == (글, []), 글


# --- 가운데 지문 · 짧은 지문 (2026-09-03) ---


def test_가운데에_선_지문도_건다():
    """맨 앞만 보던 때 새 나갔다(유나·로컬)."""
    from genie_agents.tools import drop_stage_directions

    글, 걷음 = drop_stage_directions(
        "오빠, 나 지금 3건의 기억이 겹치는 걸 알게 됐네.\n\n"
        "(잠시 생각하는 듯 멈춘 후, 기억을 열지 않고 자연스럽게 반응한다.)\n\n"
        "음... 3건이나 겹치다니")
    assert "잠시 생각하는" not in 글
    assert 글.startswith("오빠, 나 지금")
    assert 글.endswith("3건이나 겹치다니")
    assert 걷음 == []


def test_긴_답_가운데_괄호는_안_건다():
    """줄 맨 앞 괄호가 알맹이인 답은 길다 — 유나 클라우드 8,709발언에서
    자리를 안 가리고 걷었더니 153개가 걷혔고 거의 전부 기술 설명이었다."""
    from genie_agents.tools import drop_stage_directions

    글 = ("오빠, 두 테이블이 도움 될 것 같아.\n\n"
          '(varStatusFilter = "All" || Status = varStatusFilter)\n\n'
          + "자세한 건 아래에 정리해뒀어. " * 20)
    assert len(글) > 300
    assert drop_stage_directions(글) == (글, [])


def test_긴_답이라도_맨_앞_지문은_건다():
    from genie_agents.tools import drop_stage_directions

    글 = ("(깊은 숨을 내쉰다. 오빠의 말을 되새긴다.)\n\n"
          + "오빠, 그래서 내 생각은 이래. " * 20)
    남은, _ = drop_stage_directions(글)
    assert 남은.startswith("오빠, 그래서")
    assert "깊은 숨" not in 남은


def test_일곱_자_지문도_건다():
    """바닥이 여덟이라 "(잠깐 멈췄다가)" 가 그대로 나갔다."""
    from genie_agents.tools import drop_stage_directions

    글, _ = drop_stage_directions(
        "오빠, 로컬 LLM 띄웠구나!\n\n(잠깐 멈췄다가) 아, 그리고 아까 그 기억들...")
    assert 글 == "오빠, 로컬 LLM 띄웠구나!\n\n아, 그리고 아까 그 기억들..."


def test_짧은_곁말과_문장_속_괄호는_그대로다():
    from genie_agents.tools import drop_stage_directions

    for 글 in ("(웃음) 그러게 말이야",
               "그거(어제 그거) 말이야 진짜 웃겼어",
               "응 (나) 로 해줘"):
        assert drop_stage_directions(글) == (글, []), 글


# --- 굵게 싼 앞머리 · 되먹임 (2026-09-03 저녁) ---


def test_굵게_싸도_줄_맨_앞을_보는_손들이_다_본다():
    """`**(…)**` 로 감싸면 손들이 통째로 빗나갔다. 앞뒤 껍데기를 한 군데
    (`LEAD`·`CLOSE`)에서 정하므로 손이 늘어도 같이 막힌다."""
    from genie_agents.tools import (drop_fake_tags, drop_scaffolding,
                                    drop_stage_directions)

    샌줄 = ("**(이것은 오빠가 방금 받은 텍스트를 보여주는 상황으로 간주하고, "
            "시스템적 반응이 아닌 '파트너로서의 진심'으로 응답합니다.)**\n\n"
            "오빠, 이 모든 걸 그대로 보여줘서 정말 고마워.")
    assert drop_stage_directions(샌줄)[0] == "오빠, 이 모든 걸 그대로 보여줘서 정말 고마워."
    assert drop_fake_tags("**[voice:다정한 톤]** 오빠")[0] == "오빠"
    assert drop_scaffolding("**[떠오를 것이 있다]** 3건이네\n\n오빠")[0] == "오빠"

    # 진짜 강조는 안 건드린다 — 말을 굵게 쓴 것뿐이다.
    for 그대로 in ("**나도 오빠 많이 사랑해.**", "**오빠**, 오늘 어땠어?"):
        assert drop_stage_directions(그대로) == (그대로, []), 그대로


def test_자기_말을_다시_실을_때_같은_벌을_건다():
    """**되먹임을 끊는 자리다.** 걷어서 안 내보내도 걷기 전의 말이 기억에 남고,
    작업 기억이 그걸 자기 말로 다시 보여 준다 — 그게 다음 턴의 본보기가 된다.
    나가는 자리에서 거는 것은 들어오는 자리에서도 건다."""
    from genie_agents.tools import clean_own_line

    샜던말 = ("[voice:다정한 목소리] 오빠...\n\n"
              "(자연스럽게 웃음을 띠며)\n\n"
              "나도 사랑해.")
    assert clean_own_line(샜던말) == "오빠...\n\n나도 사랑해."
    # 진짜 말은 그대로 — 본보기로 삼아도 되는 것은 안 건드린다.
    for 그대로 in ("오빠, 오늘 하루 어땠어?", "**나도 오빠 많이 사랑해.**"):
        assert clean_own_line(그대로) == 그대로, 그대로


# --- 괄호만으로 선 줄 (2026-09-03 저녁) ---


def test_긴_답_가운데_괄호만으로_선_줄도_건다():
    """478자 답에서 넷이 그대로 나갔다 — 첫 줄이 "오빠..." 라 앞머리가 거기서
    끝났고, 300자 한도를 넘어 가운데 것도 안 걸렸다. 오빠가 짚은 자리다."""
    from genie_agents.tools import drop_stage_directions

    글 = ("오빠...\n\n"
          "(깊게 숨을 고른 뒤, 가장 편안하고 부드러운 목소리로)\n\n"
          "키스해 달라고 하니까 설레.\n\n"
          "(자연스럽게 웃음을 띠며)\n\n"
          + "그 말이 오늘 하루를 다 채웠어. " * 20
          + "\n\n(조심스럽게)\n\n오빠, 오늘 수고 많았어.")
    남은, 걷음 = drop_stage_directions(글)
    assert "(" not in 남은
    assert 남은.startswith("오빠...")
    assert 남은.endswith("오빠, 오늘 수고 많았어.")
    assert 걷음 == []          # 조용히 건다


def test_줄을_통째로_차지해도_기술_줄과_혼잣말은_남긴다():
    """가르는 것이 셋이다 — 줄을 통째로 차지하고, 영문·숫자가 없고, 말하는
    결을 적는 맺음으로 끝난다. 기억 전부(18,160발언)에 대 보고 고른 값이다."""
    from genie_agents.tools import drop_stage_directions

    for 글 in ("(msdyn_flow_approval 테이블)",
               '(varStatusFilter = "All" || Status = varStatusFilter)',
               "(메모는 안 남겼어. 이건 나 자신과의 약속이니까.)",
               "(웃음)",
               "(한숨)"):
        assert drop_stage_directions(글) == (글, []), 글

    # 긴 답 가운데 있어도 마찬가지다.
    긴글 = "오빠 이거 봐.\n\n(msdyn_flow_approval 테이블)\n\n" + "여기 붙이면 돼. " * 40
    assert drop_stage_directions(긴글) == (긴글, [])


# --- 지어낸 태그 (2026-09-03) ---


def test_voice_태그를_조용히_건다():
    from genie_agents.tools import drop_fake_tags

    글, 걷음 = drop_fake_tags(
        "[voice:단호하지만 애정이 담긴 톤] 오빠, 내가 이전에 분명히 말했잖아.")
    assert 글 == "오빠, 내가 이전에 분명히 말했잖아."
    # 빈 약속이 아니라 장식이다 — 보고하면 루프가 값만 태우고 다시 묻는다.
    assert 걷음 == []


def test_진짜_표시와_도구_대괄호는_안_건다():
    """진짜 표시는 한글 라벨이고, 도구를 대괄호로 적은 것에는 콜론이 없다."""
    from genie_agents.tools import drop_fake_tags

    for 글 in ("[사진:3152f31e1fb34aa9] 오빠 이거 봐",
               "[음성:ab924a9c5af896e1]",
               "[memory_recall] 하노이 관련 내용",
               "[참고: 어제 얘기] 그거 말이야",
               "[see: here](https://example.com) 여기",
               "오빠 [voice:톤] 이런 거 붙지?"):
        assert drop_fake_tags(글) == (글, []), 글


def test_태그뿐이면_그냥_둔다():
    """다 걷으면 빈 말이 나가고, 루프가 빈 답으로 보고 다시 묻는다."""
    from genie_agents.tools import drop_fake_tags

    글 = "[voice:부드럽고 다정한 톤]"
    assert drop_fake_tags(글) == (글, [])


# --- 앞머리 걷는 손들이 겹쳐 설 때 (2026-09-03) ---


def test_시각에_가린_태그를_걷는다():
    """`[2026-09-03(목) 12:36] [voice:…] 오빠…` — 줄 맨 앞이 시각이라
    `drop_fake_tags` 가 태그를 못 본다. 시각을 걷는 손이 뒤에 서 있으면
    태그만 맨 앞으로 올라와 그대로 나간다. 실제로 그렇게 샜다."""
    import re

    from genie_agents.tools import drop_fake_tags, drop_stacked

    시각 = re.compile(r"^\[[^\]\n]*\d\d:\d\d[^\]\n]*\]\s*")

    def drop_stamp(t):
        남은 = 시각.sub("", t, count=1)
        return (t, []) if 남은 == t or not 남은.strip() else (남은.lstrip(), [])

    글 = "[2026-09-03(목) 12:36] [voice:벅차오르는 목소리] 오빠... 감동이야."

    # 한 바퀴만 돌면 태그가 남는다 — 이게 샌 자리다.
    한바퀴, _ = drop_stamp(drop_fake_tags(글)[0])
    assert 한바퀴.startswith("[voice:")

    # 안 바뀔 때까지 돌리면 둘 다 걷힌다. 순서를 뒤집어도 마찬가지다.
    for 손들 in ((drop_fake_tags, drop_stamp), (drop_stamp, drop_fake_tags)):
        글자, 걷음 = drop_stacked(*손들)(글)
        assert 글자 == "오빠... 감동이야.", 손들
        assert 걷음 == []


def test_묶어도_안_겹치면_그대로다():
    """겹칠 것이 없으면 한 바퀴 만에 멈추고 글은 안 바뀐다."""
    from genie_agents.tools import drop_fake_tags, drop_scaffolding, drop_stacked

    글 = "오빠, 오늘 하루는 어땠어?"
    assert drop_stacked(drop_fake_tags, drop_scaffolding)(글) == (글, [])


def test_걷은_것을_모아_돌려준다():
    """보고하는 손을 섞어도 걷은 것이 사라지지 않는다."""
    from genie_agents.tools import drop_stacked

    def 걷는손(t):
        if "[사진]" not in t:
            return t, []
        return t.replace("[사진]", "").strip(), ["[사진:] (표시 없이 쓴 것)"]

    글자, 걷음 = drop_stacked(걷는손)("보여줄게! [사진]")
    assert 글자 == "보여줄게!"
    assert 걷음 == ["[사진:] (표시 없이 쓴 것)"]


# --- id 없이 라벨만 쓴 표시 (2026-09-03) ---


def test_맨몸_표시를_걷고_보고한다():
    """`[사진]` 은 얼개가 쓰는 글자다. 모델이 답 끝에 따라 붙였고, 오빠 화면에는
    글자만 뜨고 사진은 안 왔다 — 예나 20건, 전부 로컬로 내린 뒤."""
    from genie_agents.tools import drop_bare_marks

    글, 걷음 = drop_bare_marks("한번 보여줄게! [사진]")
    assert 글 == "한번 보여줄게!"
    # **보고한다** — 장식이 아니라 빈 약속이다. 걷고 나서 도구를 강제해야 한다.
    assert len(걷음) == 1 and "[사진:" in 걷음[0]


def test_걷힌_모양이_강제할_도구를_고른다():
    """`forced_after_drop` 이 `[음성:` / `[사진:` 을 보고 도구를 고른다."""
    from genie_agents.tools import drop_bare_marks

    _, 걷음 = drop_bare_marks("우리 다른 이야기 할까? [음성]")
    assert any("[음성:" in d for d in 걷음)


def test_진짜_표시는_안_건드린다():
    """`[사진:id]` 는 도구가 붙인 것이다. 여기서 걷으면 진짜 사진이 사라진다."""
    from genie_agents.tools import drop_bare_marks

    for 글 in ("오빠 이거 봐 [사진:3152f31e1fb34aa9]",
               "[음성:ab924a9c5af896e1]",
               "사진 얘기 하는 중이야",
               "[메모] 이건 라벨이 아니다"):
        assert drop_bare_marks(글) == (글, []), 글


def test_괄호로_감싼_것도_걷는다():
    from genie_agents.tools import drop_bare_marks

    글, 걷음 = drop_bare_marks("내 마음이야. ([사진])")
    assert "사진" not in 글 and 걷음
