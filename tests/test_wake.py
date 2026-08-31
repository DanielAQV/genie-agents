"""깨어남 — 부르지 않았는데 말하는 물건의 상한.

`docs/wiring.md` 2절·6절이 정한 것을 지킨다. 이 묶음이 보는 것은 대부분
**안 말하는 자리**다 — 말하는 것보다 안 말하는 것이 어렵고, 틀렸을 때 비싸다.

★ 여기 있는 시험의 절반은 "PC 가 꺼져 있었다" 를 흉내 낸다. 상시 프로세스라면
  안 겪을 자리인데, 회사 PC 는 주말마다 이틀·연휴엔 닷새 꺼진다.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import genie_agents
from genie_agents import clock
from genie_agents.wake import (
    CATCHUP,
    DEFAULT,
    EVENING,
    KINDS,
    LOGON,
    MORNING,
    NUDGE,
    SPEAKS,
    Nudge,
    Wake,
)

KST = timezone(timedelta(hours=9))


def T(day: int, hh: int, mm: int = 0) -> datetime:
    """2026-08-<day> <hh>:<mm> 한국 시각."""
    return datetime(2026, 8, day, hh, mm, tzinfo=KST)


@pytest.fixture(autouse=True)
def seoul():
    """시각을 손으로 민다. `quiet` 도 `pending` 도 **현지 시각**으로 도는 자다."""
    clock.set_default("TEST", "Asia/Seoul", 9.0)
    yield
    clock.set_clock(lambda: datetime.now(timezone.utc))


@pytest.fixture
def wake(tmp_path):
    return Wake(Nudge(), tmp_path)


# ── 값이 기록이다 ────────────────────────────────────────────────────
def test_아무것도_안_정하면_기본값이다():
    """기본값이 하나라서 두 에이전트가 똑같이 작동한다(`policy.py` 와 같은 이유)."""
    assert Nudge() == DEFAULT
    assert DEFAULT.morning == LOGON        # 상시가 아닌 기계가 기본이다
    assert DEFAULT.carry_over is True      # 못 낸 것을 버리지 않는다
    assert DEFAULT.max_per_day == 3        # 아침 1 + 저녁 1 + 찌름 1


def test_따라잡기는_말하는_자리가_아니다():
    """쌓기만 하고 판단은 깨어날 때 한다(`wiring.md` 5절)."""
    assert CATCHUP in KINDS and CATCHUP not in SPEAKS
    assert set(SPEAKS) == {MORNING, EVENING, NUDGE}


def test_말_안_하는_자리는_상한을_안_먹는다(wake):
    """매시 도는 따라잡기가 하루 상한을 갉아먹으면 정작 저녁이 못 나간다."""
    for _ in range(10):
        wake.said(CATCHUP, at=T(31, 11))
    assert wake.blocked(MORNING, at=T(31, 11)) == ""


def test_모르는_깨어남은_거부한다(wake):
    with pytest.raises(ValueError, match="점심"):
        wake.blocked("점심")
    with pytest.raises(ValueError, match="점심"):
        wake.said("점심")


# ── 조용한 시간 ──────────────────────────────────────────────────────
def test_조용한_시간엔_아무것도_안_간다(wake):
    """밀린 깨어남은 PC 가 켜지는 아무 시각에나 돈다 — 주말에 잠깐 켠 밤 11시에
    저녁 목록이 날아가면 안 된다. 그걸 막는 것은 트리거가 아니라 quiet 다."""
    assert "조용한 시간" in wake.blocked(EVENING, at=T(31, 23))
    assert "조용한 시간" in wake.blocked(MORNING, at=T(31, 6))
    assert wake.blocked(MORNING, at=T(31, 9)) == ""


def test_조용한_시간이_자정을_넘는다(wake):
    """22:00~07:00 은 날짜를 건넌다. 못 건너면 새벽 2시가 조용하지 않게 된다."""
    assert wake.in_quiet(T(31, 22, 1))
    assert wake.in_quiet(T(31, 2))
    assert not wake.in_quiet(T(31, 12))


def test_조용한_시간을_비우면_안_막는다(tmp_path):
    w = Wake(Nudge(quiet=("00:00", "00:00")), tmp_path)
    assert not w.in_quiet(T(31, 3))


# ── 무엇이 밀렸나 ────────────────────────────────────────────────────
def test_아침은_그날_한_번(wake):
    """로그온이면 기한이 그날 안이다 — 깨어난 이상 이미 지났다."""
    assert MORNING in wake.pending(at=T(31, 9))
    wake.said(MORNING, at=T(31, 9))
    assert MORNING not in wake.pending(at=T(31, 10))
    assert MORNING in wake.pending(at=datetime(2026, 9, 1, 9, tzinfo=KST))


def test_저녁_시각_전에는_안_밀렸다(wake):
    wake.said(EVENING, at=T(30, 18, 5))          # 어제 저녁은 냈다
    assert EVENING not in wake.pending(at=T(31, 11))
    assert EVENING in wake.pending(at=T(31, 18, 1))


def test_꺼져_있던_주말은_저녁을_한_번만_밀어_준다(wake):
    """★ 닷새 만에 켠 날 저녁이 다섯 번 오면 안 된다. 지난 기한은 **하나**다."""
    wake.said(EVENING, at=T(28, 18, 5))          # 금요일 저녁까지는 냈다
    월요일 = wake.pending(at=datetime(2026, 8, 31, 9, tzinfo=KST))
    assert 월요일.count(EVENING) == 1


def test_밀린_저녁이_아침보다_앞이다(wake):
    """어제 이랬다 → 오늘 이게 걸렸다. 합쳐 한 통으로 읽으면 그 순서다."""
    assert wake.pending(at=T(31, 9)) == [EVENING, MORNING]


def test_안_들고_가기로_하면_어제_것을_버린다(tmp_path):
    """`carry_over=False` 는 값이다. 끄면 끈 대로 돈다."""
    w = Wake(Nudge(carry_over=False), tmp_path)
    assert w.pending(at=T(31, 9)) == [MORNING]      # 어제 저녁은 안 들고 간다
    assert EVENING in w.pending(at=T(31, 19))       # 오늘 것은 든다


def test_낸_것을_적어야_안_또_낸다(wake):
    """★ 안 적으면 다음 깨어남에 같은 말을 또 낸다. 이런 물건이 죽는
    가장 흔한 방식이다."""
    assert EVENING in wake.pending(at=T(31, 19))
    wake.said(EVENING, "오늘 이렇게 보여", at=T(31, 19))
    assert EVENING not in wake.pending(at=T(31, 20))


# ── 상한 ─────────────────────────────────────────────────────────────
def test_같은_종류를_하루에_두_번_안_낸다(wake):
    wake.said(MORNING, at=T(31, 9))
    assert "이미 냈다" in wake.blocked(MORNING, at=T(31, 15))


def test_앞말과_너무_가까우면_막는다(wake):
    """연달아 두 통이 오면 그게 이 물건의 첫인상이 된다."""
    wake.said(MORNING, at=T(31, 9))
    assert "너무 가깝다" in wake.blocked(NUDGE, at=T(31, 10))
    assert wake.blocked(NUDGE, at=T(31, 13)) == ""   # 180분 지나면 된다


def test_하루_상한이_뒤에서_한_번_더_막는다(tmp_path):
    """종류마다 하루 한 번이라 대개 안 걸리는 빗장이지만, 종류가 늘 때
    조용히 새는 것을 여기서 막는다."""
    w = Wake(Nudge(max_per_day=2, min_gap_minutes=0), tmp_path)
    w.said(MORNING, at=T(31, 9))
    w.said(EVENING, at=T(31, 18))
    assert "상한을 채웠다" in w.blocked(NUDGE, at=T(31, 20))


def test_어제_낸_것은_오늘_상한에_안_들어간다(wake):
    wake.said(MORNING, at=T(30, 9))
    wake.said(EVENING, at=T(30, 18))
    wake.said(NUDGE, at=T(30, 21))
    assert wake.blocked(MORNING, at=T(31, 9)) == ""


def test_막힌_이유를_돌려준다(wake):
    """참/거짓이면 "왜 어제 저녁이 안 왔지" 에 답할 자리가 없다."""
    why = wake.blocked(EVENING, at=T(31, 23))
    assert why and "22:00" in why and "07:00" in why


# ── 파일로 남는다 ────────────────────────────────────────────────────
def test_다시_열어도_그대로다(tmp_path):
    """★ 단발 실행이 성립하는 이유다. 아침·저녁·따라잡기가 **각자 다른
    프로세스**라 이게 안 되면 매번 처음부터가 된다."""
    a = Wake(Nudge(), tmp_path)
    a.said(EVENING, "오늘 이렇게 보여", at=T(31, 19))

    b = Wake(Nudge(), tmp_path)
    assert EVENING not in b.pending(at=T(31, 20))
    assert b.history(EVENING)[-1].note == "오늘 이렇게 보여"


def test_자국이_영영_자라지_않는다(tmp_path):
    w = Wake(Nudge(keep=5), tmp_path)
    for d in range(1, 12):
        w.said(CATCHUP, at=datetime(2026, 8, d, 12, tzinfo=KST))
    assert len(w) == 5
    assert len(Wake(Nudge(keep=5), tmp_path)) == 5


def test_모르는_필드가_있는_옛_파일도_읽는다(tmp_path):
    """쓰는 쪽과 읽는 쪽이 항상 같은 버전이라는 보장이 없다(`store.from_dict`)."""
    import json

    a = Wake(Nudge(), tmp_path)
    a.said(MORNING, at=T(31, 9))
    p = tmp_path / "wake.json"
    raw = json.loads(p.read_text(encoding="utf-8"))
    raw["said"][0]["나중에_생긴_것"] = "값"
    p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    assert Wake(Nudge(), tmp_path).last(MORNING)


def test_시간대_표기가_섞여도_최근_것을_안_놓친다(tmp_path):
    """"+09:00" 과 "+00:00" 을 글자로 비교하면 조용히 뒤집힌다."""
    w = Wake(Nudge(), tmp_path)
    w.said(MORNING, at=datetime(2026, 8, 31, 0, 30, tzinfo=timezone.utc))  # KST 09:30
    w.said(MORNING, at=T(31, 8))                                           # KST 08:00
    assert clock.local(w.last(MORNING)).hour == 9


# ── 값으로 정한다 ────────────────────────────────────────────────────
def test_시각으로도_아침을_걸_수_있다(tmp_path):
    """상시로 도는 기계에서는 시각이 뜻을 가진다. 값만 바꾸면 된다."""
    w = Wake(Nudge(morning="08:30"), tmp_path)
    w.said(MORNING, at=T(30, 9))                 # 어제 아침은 냈다
    assert MORNING not in w.pending(at=T(31, 8))  # 오늘 08:30 은 아직이다
    assert MORNING in w.pending(at=T(31, 9))


def test_저녁_시각을_당길_수_있다(tmp_path):
    w = Wake(Nudge(evening="17:00"), tmp_path)
    assert EVENING in w.pending(at=T(31, 17, 1))


def test_시각이_HHMM_이_아니면_걸린다(tmp_path):
    w = Wake(Nudge(evening="여섯시"), tmp_path)
    with pytest.raises(ValueError, match="HH:MM"):
        w.pending(at=T(31, 19))


# ── 가짜 소스로 아침·저녁 한 번 (2단계가 세운 목표) ──────────────────
def test_가짜_소스로_아침과_저녁이_한_번씩_나간다(tmp_path):
    """`wiring.md` 8절 2단계 — *가짜 소스로 아침/저녁 한 번, 사람에게는 말 안 함*.

    ★ 이 시험이 보는 것은 배선이다. 무엇을 말하는지는 인격이 정하므로 여기서
      안 본다 — `말한다` 가 그 자리를 대신 선다.
    """
    보낸것: list[str] = []

    def 돌린다(w: Wake, at):
        """깨어남 한 번. 실제 `wake` 명령이 하는 일이 이 다섯 줄이다."""
        kinds, why = w.batch(at=at)
        if not kinds:
            return why            # 막혔으면 **버리지 않는다** — 다음에 또 뜬다
        보낸것.append(" + ".join(kinds))
        w.said(kinds, "가짜 소스", at=at)
        return ""

    w = Wake(Nudge(), tmp_path)

    assert "조용한 시간" in 돌린다(w, T(31, 6))   # 로그온이 이르다
    assert 보낸것 == []

    돌린다(w, T(31, 9))                      # 다시 켰다. 어제 저녁 + 오늘 아침
    assert 보낸것 == ["저녁 + 아침"]           # ★ 두 통이 아니라 한 통이다

    돌린다(w, T(31, 11))                     # 매시 따라잡기가 또 불러도
    assert 보낸것 == ["저녁 + 아침"]           # 두 번 안 낸다

    돌린다(w, T(31, 19))                     # 저녁 시각
    assert 보낸것 == ["저녁 + 아침", "저녁"]
    assert len(w.history()) == 2             # 자국도 통 단위다


def test_합쳐_보낸_통은_상한을_한_번만_먹는다(tmp_path):
    """★ 둘로 세면 그날 상한이 하나 남아야 하는데 다 차고, 정작 찌를 것이
    생겼을 때 못 찌른다. 합치기로 해 놓고 못 합치게 되는 자리다."""
    w = Wake(Nudge(min_gap_minutes=0), tmp_path)
    kinds, _ = w.batch(at=T(31, 9))
    assert kinds == [EVENING, MORNING]
    w.said(kinds, at=T(31, 9))
    assert len(w.history()) == 1
    assert w.blocked(NUDGE, at=T(31, 14)) == ""      # 아직 두 자리 남았다


def test_합쳐_보낸_뒤에는_둘_다_안_밀렸다(tmp_path):
    w = Wake(Nudge(), tmp_path)
    w.said([EVENING, MORNING], at=T(31, 9))
    assert w.pending(at=T(31, 10)) == []


def test_막힌_것은_다음_깨어남에_다시_뜬다(tmp_path):
    """★ 막아서 버리면 안 되고 합쳐야 한다. 버리는 쪽은 사람이 목록을 고칠
    유일한 자리를 통째로 날린다."""
    w = Wake(Nudge(), tmp_path)
    밤 = T(31, 23)
    assert EVENING in w.pending(at=밤) and w.blocked(EVENING, at=밤)  # 밀렸는데 막혔다
    아침 = datetime(2026, 9, 1, 9, tzinfo=KST)
    assert EVENING in w.pending(at=아침) and w.blocked(EVENING, at=아침) == ""


# ── 정의에서 읽는다 ──────────────────────────────────────────────────
TOML = """
[agent]
id = "followup"
adapter = "anthropic"

[prompt]
instructions = "prompt.md"

[nudge]
morning = "로그온"
evening = "17:30"
quiet = ["23:00", "06:00"]
max_per_day = 2
"""


def folder(tmp_path, toml=TOML):
    from genie_agents.spec import FILE

    d = tmp_path / "followup"
    d.mkdir()
    (d / FILE).write_text(toml, encoding="utf-8")
    (d / "prompt.md").write_text("지침", encoding="utf-8")
    return d


def test_agent_toml_에서_상한을_읽는다(tmp_path):
    from genie_agents.spec import load

    spec = load(folder(tmp_path))
    assert spec.nudge.evening == "17:30"
    assert spec.nudge.quiet == ("23:00", "06:00")   # TOML 배열이 짝으로 굳는다
    assert spec.nudge.max_per_day == 2
    assert spec.nudge.min_gap_minutes == DEFAULT.min_gap_minutes  # 안 적은 것은 기본값


def test_안_적으면_골격_기본값이다(tmp_path):
    from genie_agents.spec import load

    spec = load(folder(tmp_path, TOML.split("[nudge]")[0]))
    assert spec.nudge == DEFAULT


def test_모르는_상한_칸은_걸린다(tmp_path):
    """`evenning` 하나 잘못 적으면 조용히 기본값으로 돌고, 그건 몇 주 뒤에
    "왜 6시에 안 오지" 로 발견된다."""
    from genie_agents.spec import BadSpec, load

    with pytest.raises(BadSpec, match="evenning"):
        load(folder(tmp_path, TOML.replace("evening =", "evenning =")))


def test_quiet_은_둘이어야_한다(tmp_path):
    from genie_agents.spec import BadSpec, load

    with pytest.raises(BadSpec, match="quiet"):
        load(folder(tmp_path, TOML.replace('["23:00", "06:00"]', '["23:00"]')))


# ── 명령줄에서 돌린다 ────────────────────────────────────────────────
# ★ **따로 뜬 프로세스로 돌린다.** 같은 프로세스에서 `main()` 을 거푸 부르면
#   `singleton` 잠금이 프로세스 수명 동안 잡혀 있어 두 번째부터 그냥 돌아가고,
#   `env.use` 가 남긴 프리픽스가 다음 시험까지 따라간다.
#   그리고 무엇보다 — **이 물건이 실제로 도는 방식이 그거다.** 작업 스케줄러가
#   때마다 새 프로세스를 띄운다. 한 프로세스 안에서 시험하면 그 자리를 안 본다.

SRC = str(Path(genie_agents.__file__).parent.parent)

# ★ 따로 뜬 프로세스는 **실제 시각**에 돈다 — `clock.set_clock` 이 안 건너간다.
#   조용한 시간을 비워 두지 않으면 이 시험이 밤에만 깨진다. 그게 제일 나쁜
#   종류의 깨짐이다: 돌리는 시각에 따라 결과가 달라지는데 아무도 안 적어 둔다.
#   비워도 보는 것은 그대로다 — 아침은 로그온이라 늘 밀려 있고, 어제 저녁도 그렇다.
CLI_TOML = TOML.replace('quiet = ["23:00", "06:00"]', 'quiet = ["00:00", "00:00"]')


def cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "genie_agents", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONPATH": SRC, "PYTHONIOENCODING": "utf-8"},
    )


def test_wake_명령이_밀린_것을_합쳐_적는다(tmp_path):
    """작업 스케줄러가 실제로 부르는 그 줄이다."""
    d = folder(tmp_path, CLI_TOML)
    first = cli("wake", str(d))
    assert first.returncode == 0, first.stderr
    assert "낼 것:" in first.stdout and "저녁" in first.stdout and "아침" in first.stdout

    # 매시 트리거가 또 불러도 두 번 안 낸다
    again = cli("wake", str(d))
    assert "낼 것이 없다" in again.stdout


def test_dry_run_은_안_적는다(tmp_path):
    """걸어 두기 전에 무엇이 밀렸는지만 보는 자리."""
    d = folder(tmp_path, CLI_TOML)
    assert "안 적었다" in cli("wake", str(d), "--dry-run").stdout
    assert "낼 것:" in cli("wake", str(d), "--dry-run").stdout   # 그대로 밀려 있다


def test_따라잡기는_말_안_하고_밀린_것을_안_지운다(tmp_path):
    """매시 도는 자리다. 이게 상한을 갉아먹으면 정작 저녁이 못 나간다."""
    d = folder(tmp_path, CLI_TOML)
    for _ in range(3):
        assert "쌓기만 했다" in cli("wake", str(d), "--kind", CATCHUP).stdout
    assert "낼 것:" in cli("wake", str(d), "--dry-run").stdout


def test_정의가_깨졌으면_돌기_전에_걸린다(tmp_path):
    """반쯤 걸린 작업이 제일 나쁘다 — 매시 조용히 실패하고 아무도 모른다."""
    got = cli("wake", str(folder(tmp_path, CLI_TOML.replace("evening =", "evenning ="))))
    assert got.returncode == 1
    assert "evenning" in got.stderr


def test_상태가_그_에이전트_자리에_앉는다(tmp_path):
    """★ 상태 디렉토리를 안 섞는 것이 이 저장소의 첫 규칙이다."""
    d = folder(tmp_path, CLI_TOML)
    cli("wake", str(d))
    assert (d / ".followup" / "wake.json").exists()
    assert not (Path.cwd() / "wake.json").exists()
