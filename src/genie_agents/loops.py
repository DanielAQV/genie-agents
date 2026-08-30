"""열린 고리 — 아직 안 끝난 것들.

★★ **초안이다.** 시험이 없고 아무 데도 안 붙어 있다. 설계를 코드 모양으로
   적어 둔 것에 가깝다 — 쓰기 전에 시험부터 붙여라.
   무엇을 만들려는 것인지는 `docs/followup.md`.

리마인더도 메모도 아니다. 셋은 다른 물건이다.

    reminders  **시점**이 있다. 그날이 오면 뜬다
    notes      **알아 둔 것**. 찾을 때 나온다
    loops      **상태**가 있다. 열렸고, 누군가에게 걸려 있고, 언젠가 닫힌다

━━ 이 원장은 사람이 안 쓴다 ━━

★ **쓰는 쪽은 에이전트다.** 사람에게 "적어 두세요" 를 시키는 순간 이 물건은
  안 쓰인다 — 적을 사람이면 애초에 이게 필요 없었다. 고리는 사람이 이미
  남기는 흔적(주고받은 말, 저장소 활동)에서 에이전트가 만들어 낸다.
  사람이 하는 일은 **틀린 것을 한 번 눌러 고치는 것**뿐이다.

━━ 확신을 같이 든다 ━━

★ 흔적에서 만든 고리는 **틀릴 수 있다.** "다 확인했어" 가 무엇을 가리키는지
  못 찾는 일이 실제로 있다. 그래서 `sure` 를 같이 든다.

    sure=True   명시적이다. 먼저 말을 걸어도 된다
    sure=False  추측이다. **먼저 말 걸지 않는다.** 하루 한 번 훑는 목록에만 넣고
                거기서 한 글자로 고치게 한다

  틀린 알림 하나가 깎는 신뢰가, 놓친 고리 하나보다 비싸다. 목록에서 줄 하나
  지우는 값은 싸다. 그래서 **먼저 말할 때는 좁게, 훑을 때는 넓게** 간다.

━━ 닫힌 것을 안 지운다 ━━

★ 닫힌 고리가 곧 **한 일**이다. 지우면 "이번 주에 무엇이 움직였나" 를 물을
  근거가 사라진다. 상태만 바꾸고 자리에 둔다.

━━ 근거 없이는 안 연다 ━━

★ 모든 고리는 `source` 를 든다 — 어느 말, 어느 PR 에서 나왔는지. 근거 없이
  "이거 하기로 했잖아" 라고 하면 사람은 확인할 길이 없고, 한 번 그러면
  이 원장 전체를 안 믿게 된다.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .clock import elapsed_minutes, now_iso
from .store import JsonStore, default_root

# 상태 넷. 더 늘리지 않는다 — 늘릴수록 "지금 뭐지" 를 사람이 판단하게 된다.
OPEN = "열림"      # 내가 할 차례다
WAITING = "기다림"  # 남이 할 차례다. 내가 할 건 없고 안 움직이면 찔러야 한다
DONE = "닫힘"      # 끝났다. **안 지운다** — 이게 한 일이다
DROPPED = "접음"    # 안 하기로 했다. 닫힘과 갈라 둔다 — 성과가 아니다

STATES = (OPEN, WAITING, DONE, DROPPED)
LIVE = (OPEN, WAITING)

ME = "나"


@dataclass
class Move:
    """고리가 움직인 자국. 언제 무엇이 있었나."""

    ts: str
    note: str
    state: str = ""


@dataclass
class Loop:
    id: str
    text: str
    owner: str            # 누구 차례인가. `ME` 아니면 사람 이름
    state: str
    source: str           # 어디서 나왔나. 근거다
    opened_at: str
    moved_at: str
    due: str = ""         # 있으면 그때까지. 없는 고리가 대부분이다
    sure: bool = True
    moves: list[Move] = field(default_factory=list)

    @property
    def live(self) -> bool:
        return self.state in LIVE

    @property
    def mine(self) -> bool:
        return self.owner == ME

    def quiet_days(self, at: str | None = None) -> float:
        """마지막으로 움직인 뒤 며칠. 멈춘 것을 찾는 자다."""
        return elapsed_minutes(self.moved_at, at) / 1440

    @classmethod
    def from_dict(cls, d: dict) -> Loop:
        return cls(
            id=d["id"], text=d["text"], owner=d["owner"], state=d["state"],
            source=d["source"], opened_at=d["opened_at"], moved_at=d["moved_at"],
            due=d.get("due", ""), sure=bool(d.get("sure", True)),
            moves=[Move(**m) for m in d.get("moves", [])],
        )


class LoopBook:
    def __init__(self, root: Path | str | None = None) -> None:
        # 기본 인자로 두면 def 를 읽는 순간 한 번 정해져서, 프리픽스가 걸리기
        # 전 값에 굳는다. 부를 때 정한다.
        self._store = JsonStore(Path(root if root is not None else default_root()) / "loops.json")
        raw = self._store.load({"loops": []})
        self._items: list[Loop] = [Loop.from_dict(d) for d in raw.get("loops", [])]

    def __len__(self) -> int:
        return len(self._items)

    # --- 열고 닫기 ---

    def open(
        self,
        text: str,
        *,
        source: str,
        owner: str = ME,
        due: str = "",
        sure: bool = True,
        note: str = "",
    ) -> Loop:
        """고리를 연다. 같은 근거로는 **두 번 안 열린다.**

        같은 말이 두 번 들어오는 일은 늘 있다 — 폴링이 겹치고, 수정된 메시지가
        새것처럼 오고, 다시 훑는다. 근거가 같으면 같은 고리로 본다.
        """
        found = self.by_source(source)
        if found is not None:
            return found
        ts = now_iso()
        loop = Loop(
            id=uuid.uuid4().hex[:8], text=text, owner=owner, state=OPEN,
            source=source, opened_at=ts, moved_at=ts, due=due, sure=sure,
            moves=[Move(ts=ts, note=note or "열림", state=OPEN)],
        )
        self._items.append(loop)
        self._flush()
        return loop

    def move(self, lid: str, note: str, *, state: str = "", owner: str = "") -> Loop | None:
        """움직였다고 적는다. 상태나 차례가 바뀌면 같이 준다.

        **찌른 것도 움직임이다.** 안 적으면 조용한 날짜만 보고 매일 같은 것을
        다시 찌른다.
        """
        loop = self.get(lid)
        if loop is None:
            return None
        if state:
            if state not in STATES:
                raise ValueError(f"모르는 상태다: {state!r} ({', '.join(STATES)})")
            loop.state = state
        if owner:
            loop.owner = owner
        loop.moved_at = now_iso()
        loop.moves.append(Move(ts=loop.moved_at, note=note, state=loop.state))
        self._flush()
        return loop

    def close(self, lid: str, note: str = "닫힘") -> Loop | None:
        return self.move(lid, note, state=DONE)

    def drop(self, lid: str, note: str = "안 하기로 함") -> Loop | None:
        return self.move(lid, note, state=DROPPED)

    def confirm(self, lid: str) -> Loop | None:
        """사람이 "맞다" 고 했다. 추측이던 것이 명시가 된다 —
        그때부터는 먼저 말을 걸어도 된다."""
        loop = self.get(lid)
        if loop is None:
            return None
        loop.sure = True
        self._flush()
        return loop

    # --- 꺼내 보기 ---

    def get(self, lid: str) -> Loop | None:
        return next((x for x in self._items if x.id == lid), None)

    def by_source(self, source: str) -> Loop | None:
        return next((x for x in self._items if x.source == source), None)

    def live(self, *, owner: str = "", sure_only: bool = False) -> list[Loop]:
        """아직 안 끝난 것. 오래 조용한 것이 앞이다 — 그게 먼저 봐야 할 것이다."""
        got = [x for x in self._items if x.live]
        if owner:
            got = [x for x in got if x.owner == owner]
        if sure_only:
            got = [x for x in got if x.sure]
        return sorted(got, key=lambda x: x.moved_at)

    def quiet(self, days: float = 3, *, at: str | None = None, sure_only: bool = False) -> list[Loop]:
        """이만큼 조용한 고리. 멈춘 것을 찾는다."""
        return [x for x in self.live(sure_only=sure_only) if x.quiet_days(at) >= days]

    def due_by(self, when: str) -> list[Loop]:
        """그때까지인 것. 기한 없는 고리는 안 나온다."""
        return [x for x in self.live() if x.due and x.due <= when]

    def closed_between(self, start: str, end: str) -> list[Loop]:
        """그 사이에 닫힌 것 — **이게 한 일이다.**

        접은 것(`DROPPED`)은 뺀다. 안 하기로 한 것은 성과가 아니다.
        """
        return sorted(
            (x for x in self._items if x.state == DONE and start <= x.moved_at <= end),
            key=lambda x: x.moved_at,
        )

    def _flush(self) -> None:
        self._store.save({"loops": [asdict(x) for x in self._items]})
