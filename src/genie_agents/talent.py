"""달란트 잔고 — 설계 문서 3.2 (A).

- 소모형 배터리가 아니라 **누적형 잔고(달란트 통장)**.
- 좋은 판단에는 +, 충동적/근거 없는 원칙 수정 시도에는 -.
- 원장은 append-only. 설계 원칙 3(되돌릴 수 없음)을 구조로 표현한다 —
  기록을 지워서 잔고를 되돌리는 경로를 두지 않는다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from .clock import now_iso
from .store import default_root, JsonStore, from_dict

# 요율. 원칙에 손대는 행위는 선불이고, 검증되면 그 이상으로 돌아온다.
#
# **게이트가 걸리는 자리는 에이전트마다 다르다**(계약 문서 2절) — 에이전트는
# 원칙을 세우는 것부터, 다른 에이전트는 바꾸는 것에만. 값은 같아서 이름 둘이 같은 수를
# 가리킨다. 이름을 하나로 합치지 않는 것은 **각자 무엇에 값을 매기는지**가
# 다르기 때문이고, 그 차이가 이름에 남아 있어야 한다.
COST_PROPOSE = -1  # 임시 원칙을 세운다
COST_REVISE = COST_PROPOSE  # 이미 선 원칙을 고친다
REWARD_CONFIRM = 3  # 반복/결과로 확정됨 (좋은 판단)
COST_RETRACT = -2  # 근거 없었음이 드러나 폐기됨

INITIAL_BALANCE = 5


@dataclass(frozen=True)
class TalentEntry:
    ts: str
    delta: int
    reason: str
    ref: str | None = None  # 관련 원칙 id


class TalentLedger:
    def __init__(self, root: Path | str | None = None) -> None:
        root = default_root() if root is None else root
        self._store = JsonStore(Path(root) / "talent.json")
        raw = self._store.load(None)
        if raw is None:
            self._entries: list[TalentEntry] = [
                TalentEntry(ts=now_iso(), delta=INITIAL_BALANCE, reason="초기 잔고")
            ]
            self._flush()
        else:
            self._entries = [from_dict(TalentEntry, e) for e in raw["entries"]]

    # --- 조회 ---

    def balance(self) -> int:
        return sum(e.delta for e in self._entries)

    def entries(self) -> list[TalentEntry]:
        return list(self._entries)

    # --- 기록 (append-only) ---

    def record(self, delta: int, reason: str, ref: str | None = None) -> TalentEntry:
        entry = TalentEntry(ts=now_iso(), delta=delta, reason=reason, ref=ref)
        self._entries.append(entry)
        self._flush()
        return entry

    def _flush(self) -> None:
        self._store.save({"entries": [asdict(e) for e in self._entries]})
