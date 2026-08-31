"""깨어남 — 언제 스스로 말을 거나.

골격에 없던 마지막 큰 구멍이다. 지금까지 이 골격의 에이전트는 **불려야만**
말했다(`talk`). 부르지 않았는데 말하는 물건은 다른 종류이고, 다른 위험을 진다.

━━ 판단과 상한을 갈라 둔다 ━━

★ **`Nudge` 는 모델이 말하기로 정한 뒤에도 막는 문이다.**
  말을 걸지 말지의 판단 자체는 `policy.decision_tools = {"speak", "stay_silent"}`
  가 든다. 여기는 그 뒤에 선다.

  모델에게 "하루 3번까지" 를 지키게 하면 **언젠가 안 지킨다.** 지키는 날이
  대부분이라 더 나쁘다 — 어긴 날에야 없다는 걸 알게 된다.

━━ 값으로 올라가야 기록이 된다 ━━

    Nudge(evening="18:00", max_per_day=3)

`if agent == "이름"` 이 안 되는 이유와 같다(`policy.py`). 코드에 흩어 두면
**어느 날 세 개가 겹쳐서 온다.** 값이면 그 자리가 곧 기록이다.

━━ 시각이 목표가 아니라 상한인 자리 ━━

★ 이 골격은 상시 프로세스를 전제하지 않는다. 깨우는 것은 밖(작업 스케줄러·
  cron·systemd timer)이고, 그것은 **놓친다.** PC 가 꺼져 있고, 절전에 들고,
  연휴엔 닷새를 건너뛴다.

  그래서 여기가 드는 물음은 "지금 몇 시인가" 가 아니라 **"무엇이 밀렸나"** 다
  (`pending`). 밀린 것을 다음에 깨어날 때 낸다.

★ **트리거는 *언제 도는가*를 정하고, `Nudge` 는 *언제 말해도 되는가*를 정한다.**
  둘은 다른 물음이고 스케줄러는 뒤쪽을 모른다. 주말에 잠깐 켠 밤 11시에
  밀린 저녁이 날아가면 안 된다 — 그것을 막는 것은 트리거가 아니라 `quiet` 다.

━━ 여기서 안 하는 것 ━━

**무엇을 말할지는 안 정한다.** 그건 그 존재가 누구인지에 딸린 것이라
`prompt.md` 가 든다. *언제* 는 값으로, *무엇* 은 인격으로.

원장도 안 든다. `nudge_quiet_days` 는 값으로만 있고, 그걸로 무엇을 찾을지는
부르는 쪽이 정한다 — 열린 고리일 수도, 답 없는 메일일 수도 있다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from . import clock
from .store import JsonStore, default_root, from_dict

# 깨어나는 자리 넷. 늘리기 전에 물을 것: **이건 사람에게 말하는가?**
MORNING = "아침"    # 오늘 걸린 것. 좁게 — 사람의 주의를 끊는다
EVENING = "저녁"    # 오늘 이렇게 보여 + 빠진 거 있어? 넓게 — 사람이 앉아서 읽는다
NUDGE = "찌름"      # 기한 임박 · 오래 멈춘 것. 드물게
CATCHUP = "따라잡기"  # **말하지 않는다.** 쌓기만 한다

KINDS = (MORNING, EVENING, NUDGE, CATCHUP)

SPEAKS = (MORNING, EVENING, NUDGE)
"""사람에게 말이 나가는 자리. `CATCHUP` 은 여기 없고, 그래서 상한을 안 먹는다."""

LOGON = "로그온"
"""아침을 시각이 아니라 이 말로 걸 수 있다.

★ 상시가 아닌 기계에서 "08:30" 은 아무 뜻이 없다 — 그때 그 기계는 꺼져 있다.
  *사람이 그 앞에 앉은 순간*이 아침이고, 그걸 아는 것은 시계가 아니라 로그온이다.
  그러면 아침의 기한은 **그날 안**이 된다.
"""


@dataclass(frozen=True)
class Nudge:
    """부르지 않았는데 말하는 것의 상한. 전부 기본값이 있다."""

    morning: str = LOGON
    """`"로그온"` 이면 그날 안에 한 번. `"08:30"` 이면 그 시각 뒤에 한 번."""

    evening: str = "18:00"
    """이 시각이 지나면 저녁이 밀린 것이 된다."""

    quiet: tuple[str, str] = ("22:00", "07:00")
    """이 사이엔 아무것도 안 간다. 자정을 넘어도 된다.

    ★ **밀린 것을 낼 때 특히 이게 있어야 한다.** 밀린 깨어남은 PC 가 켜지는
      아무 시각에나 돌 수 있다 — 트리거는 그 시각을 모른다.
    """

    max_per_day: int = 3
    """아침 1 + 저녁 1 + 찌름 1. 종류마다 하루 한 번이라 대개 안 걸리는
    빗장이지만, 종류가 늘 때 조용히 새는 것을 여기서 막는다."""

    min_gap_minutes: int = 180
    """앞말과 이만큼은 떨어뜨린다. 연달아 두 통이 오면 그게 첫인상이 된다."""

    nudge_quiet_days: float = 3
    """이만큼 멈춘 것만 찌른다. **무엇이 멈췄는지는 여기서 안 본다.**"""

    carry_over: bool = True
    """못 낸 것을 버리지 않고 다음 깨어남에 낸다.

    ★ **막아서 버리면 안 되고 합쳐야 한다.** 저녁을 버리는 쪽은 사람이 목록을
      고칠 유일한 자리를 통째로 날린다 — 그러면 원장이 틀린 채로 자란다.
    """

    keep: int = 90
    """자국을 이만큼만 들고 있는다. 파일이 영영 자라지 않게."""


DEFAULT = Nudge()
"""아무것도 안 정했을 때."""


@dataclass
class Said:
    """**한 통**이 나간 자국. 언제, 무엇을 덮어서.

    ★ 자국의 단위가 종류가 아니라 **통**이다. 밀린 저녁과 오늘 아침을 합쳐
      한 통으로 보내 놓고 둘로 세면, 그날 상한이 하나 남아야 하는데 다 차고
      `min_gap` 도 자기 자신에게 걸린다 — 합치기로 해 놓고 못 합치게 된다.
    """

    kinds: list[str] = field(default_factory=list)
    ts: str = ""
    note: str = ""

    def covers(self, kind: str) -> bool:
        return kind in self.kinds


def _hhmm(s: str) -> tuple[int, int]:
    try:
        h, m = s.split(":")
        return int(h), int(m)
    except (ValueError, AttributeError):
        raise ValueError(f"시각은 HH:MM 이다: {s!r}") from None


def _at_local(day: datetime, hhmm: str) -> datetime:
    h, m = _hhmm(hhmm)
    return day.replace(hour=h, minute=m, second=0, microsecond=0)


class Wake:
    """언제 말해도 되는지만 안다. 무엇을 말할지는 안 든다.

        wake = Wake(nudge, root)
        for kind in wake.pending():        # 무엇이 밀렸나
            why = wake.blocked(kind)
            if why:
                continue                   # 막혔다. **버리지 않는다** — 다음에 또 뜬다
            ...                            # 여기서 말한다 (인격)
            wake.said(kind, "무엇을 냈나")
    """

    def __init__(self, nudge: Nudge | None = None, root: Path | str | None = None) -> None:
        self.nudge = nudge or DEFAULT
        # 기본 인자로 두면 def 를 읽는 순간 한 번 정해져서, 프리픽스가 걸리기
        # 전 값에 굳는다(`store.default_root`). 부를 때 정한다.
        self._store = JsonStore(
            Path(root if root is not None else default_root()) / "wake.json"
        )
        raw = self._store.load({"said": []})
        self._said: list[Said] = [from_dict(Said, d) for d in raw.get("said", [])]

    # --- 무엇이 밀렸나 ---

    def last(self, kind: str) -> str:
        """그 종류가 마지막으로 나간 시각. 없으면 빈 문자열."""
        got = [s.ts for s in self._said if s.covers(kind)]
        # ★ 문자열로 최대를 잡으면 안 된다. 시간대 표기가 섞이면
        #   "+09:00" 과 "+00:00" 이 글자 순서로 비교돼 조용히 뒤집힌다.
        return max(got, key=clock.parse) if got else ""

    def due_since(self, kind: str, at: str | datetime | None = None) -> datetime | None:
        """그 종류의 **마지막으로 지난 기한.** 없으면 `None`.

        "지금이 몇 시인가" 가 아니라 이걸 묻는다. 며칠 꺼져 있었어도 지난
        기한은 **하나**라서, 닷새 만에 켠 날 저녁이 다섯 번 오지 않는다.
        """
        now = clock.local(at)
        if kind == MORNING:
            # 로그온이면 그날 안이 기한이다 — 깨어난 이상 이미 지났다.
            when = _at_local(now, "00:00") if self.nudge.morning == LOGON \
                else _at_local(now, self.nudge.morning)
        elif kind == EVENING:
            when = _at_local(now, self.nudge.evening)
        else:
            return None  # 찌름·따라잡기는 시각이 없다. 부르는 쪽이 정한다
        if when > now:
            when -= timedelta(days=1)  # 오늘 것은 아직이다. 어제 것을 본다
        return when

    def covered(self, kind: str, at: str | datetime | None = None) -> bool:
        """그 종류의 **마지막 기한 것을 이미 냈나.**

        ★ 날짜로 보면 안 된다. 아침에 *어제치* 저녁을 밀어 보낸 날, 날짜로는
          "오늘 저녁 냈음" 이 되어 **그날 진짜 저녁이 중복으로 막힌다.**
          냈다는 것은 "오늘 한 번" 이 아니라 "그 기한 것을" 이다.
        """
        last = self.last(kind)
        if not last:
            return False
        when = self.due_since(kind, at)
        if when is None:
            # 찌름은 기한이 없다. 여기만 날짜로 본다 — 하루 1건.
            return clock.local(last).date() == clock.local(at).date()
        return clock.local(last) >= when

    def pending(self, at: str | datetime | None = None) -> list[str]:
        """지금 냈어야 하는데 아직 안 낸 것. **순서가 곧 읽는 순서다.**

        ★ 저녁이 아침보다 앞이다. 밀린 저녁은 *어제 이랬다*이고 아침은
          *오늘 이게 걸렸다*라, 합쳐서 한 통으로 읽으면 그 순서여야 한다.
        """
        out = []
        for kind in (EVENING, MORNING):
            when = self.due_since(kind, at)
            if when is None:
                continue
            if self.covered(kind, at):
                continue  # 이 기한 것은 이미 냈다
            if not self.nudge.carry_over and when.date() != clock.local(at).date():
                continue  # 어제 것을 안 들고 간다
            out.append(kind)
        return out

    def batch(self, at: str | datetime | None = None) -> tuple[list[str], str]:
        """**지금 한 통으로 낼 것들**과, 못 내면 왜인지.

        ★ 밀린 저녁과 오늘 아침은 **합쳐서 한 통**이다. 따로 두 번 보내면
          이 물건이 처음 주는 인상이 "알림 두 개" 가 되고, `min_gap` 이 뒷것을
          막아 절반만 나간다. 상한은 통 하나에 한 번 건다.

            kinds, why = wake.batch()
            if kinds:
                ...                       # 한 통을 만든다 (인격)
                wake.said(kinds, "무엇을 냈나")
        """
        kinds = self.pending(at)
        if not kinds:
            return [], ""
        why = self.blocked(kinds, at)
        return ([], why) if why else (kinds, "")

    # --- 말해도 되나 ---

    def blocked(self, kind: str | Sequence[str],
                at: str | datetime | None = None) -> str:
        """막혔으면 **왜인지** 돌려준다. 빈 문자열이면 나가도 된다.

        `kind` 가 여럿이면 **한 통으로 나갈 것들**이라 상한을 한 번만 먹인다.

        ★ 참/거짓이 아니라 이유다. 안 나간 말을 나중에 설명할 수 있어야 한다 —
          "왜 어제 저녁 목록이 안 왔지" 에 답할 자리가 여기 말고 없다.
        """
        kinds = self._kinds(kind)
        말할것 = [k for k in kinds if k in SPEAKS]
        if not 말할것:
            return ""  # 말이 안 나가는 자리는 상한을 안 먹는다

        now = clock.local(at)
        if self.in_quiet(at):
            a, b = self.nudge.quiet
            return f"조용한 시간이다 ({a}~{b})"

        today = [s for s in self._said
                 if any(k in SPEAKS for k in s.kinds)
                 and clock.local(s.ts).date() == now.date()]
        겹침 = [k for k in 말할것 if self.covered(k, at)]
        if 겹침:
            return f"오늘 {', '.join(겹침)} 은(는) 이미 냈다"
        if len(today) >= self.nudge.max_per_day:
            return f"오늘 상한을 채웠다 ({self.nudge.max_per_day}번)"

        if today or self._said:
            spoken = [s.ts for s in self._said if any(k in SPEAKS for k in s.kinds)]
            if spoken:
                gap = clock.elapsed_minutes(max(spoken, key=clock.parse), at)
                if gap < self.nudge.min_gap_minutes:
                    return (f"앞말과 너무 가깝다 "
                            f"({gap:.0f}분 · {self.nudge.min_gap_minutes}분은 떨어뜨린다)")
        return ""

    def in_quiet(self, at: str | datetime | None = None) -> bool:
        a, b = self.nudge.quiet
        if a == b:
            return False
        now = clock.local(at).strftime("%H:%M")
        return a <= now < b if a < b else (now >= a or now < b)

    # --- 나갔다고 적는다 ---

    def said(self, kind: str | Sequence[str], note: str = "",
             at: str | datetime | None = None) -> Said:
        """★ **낸 것을 반드시 적는다.** 안 적으면 `pending` 이 그대로라 다음
        깨어남에 같은 말을 또 낸다. 그게 이런 물건이 죽는 가장 흔한 방식이다.

        여럿을 주면 **한 통으로 나간 것**이다 — 상한도 한 번만 먹는다.
        """
        kinds = self._kinds(kind)
        if at is None:
            ts = clock.now_iso()
        else:
            ts = (clock.parse(at) if isinstance(at, str) else at).isoformat()
        s = Said(kinds=kinds, ts=ts, note=note)
        self._said.append(s)
        # 오래된 자국은 버린다. `pending` 은 종류마다 마지막 하나만 본다.
        self._said = sorted(self._said, key=lambda x: clock.parse(x.ts))[-self.nudge.keep:]
        self._store.save({"said": [asdict(x) for x in self._said]})
        return s

    def history(self, kind: str = "") -> list[Said]:
        got = [s for s in self._said if not kind or s.covers(kind)]
        return sorted(got, key=lambda x: clock.parse(x.ts))

    @staticmethod
    def _kinds(kind: str | Sequence[str]) -> list[str]:
        kinds = [kind] if isinstance(kind, str) else list(kind)
        모르는 = [k for k in kinds if k not in KINDS]
        if 모르는:
            raise ValueError(f"모르는 깨어남이다: {모르는[0]!r} ({', '.join(KINDS)})")
        return kinds

    def __len__(self) -> int:
        return len(self._said)
