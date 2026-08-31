"""추출 한 번을 통째로 적어 둔다 — 판을 비교할 수 있게.

추출 호출은 지나가면 사라진다. 무엇을 실었는지, 모델이 뭐라 했는지, 어느
지침으로 그랬는지가 안 남으면 **판끼리 나란히 놓을 수가 없고**, 나빠진 것을
알아도 어디로 되돌릴지 모른다. 실제로 그렇게 세 판을 눈으로만 비교했다
(2026-08-31).

━━ 한 파일이 네 가지를 한다 ━━

    판 비교      같은 입력에 지침만 바꿔 다시 돌린다
    회귀 시험    슬랙 없이 재생. 지침을 고칠 때마다 몇 초
    금 라벨      사람은 **틀린 것만** 표시한다
    학습 데이터  QLoRA 가 먹는 것이 이 파일이다

★ `wiring.md` §9 의 *"학습용 코퍼스를 따로 켤 것인가"* 가 여기서 닫힌다.
  새 물음을 만드는 대신 이미 열린 물음을 같은 물건으로 닫는다.

━━ 사람은 짓지 않는다. 고친다 ━━

★ 이 저장소의 첫 번째 원칙이다(`followup.md`) — *"원장은 사람이 안 쓴다.
  사람이 하는 일은 틀린 것을 한 번 눌러 고치는 것뿐이다."* 평가도 같은
  모양이어야 한다. **기본이 "맞음"이고, 사람은 틀린 것에만 표시한다.**
  라벨을 처음부터 적게 하는 평가 설계는 이 제품의 전제와 어긋난다.

  그래서 `verdict` 의 기본값이 빈 문자열이고, 빈 것은 **아직 안 본 것**이지
  틀린 것이 아니다. 점수는 *본 것 중에* 몇이 맞았나로 낸다 — 안 본 것을
  맞은 것으로 세면 점수가 저 혼자 오른다.

━━ 지침이 바뀌면 판도 갈린다 ━━

★ `prompt_sha` 를 같이 든다. 이게 없으면 나빠진 것이 지침 탓인지 모델 탓인지
  묶음 탓인지 못 가른다 — 그리고 못 가르면 되돌릴 데를 못 찾는다.

━━ 남는 것은 팀원의 글이다 ━━

★ `body` 에 원문이 그대로 들어 있다. `transcript` 의 72시간이 여기엔 안 걸린다
  — **켜야 남고**(`record=True`), 얼마나 둘지는 부르는 쪽이 정한다(`prune`).
  켜는 것 자체가 결정이라 기본값은 꺼짐이다.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import clock
from .store import default_root, from_dict

RIGHT = "맞음"
WRONG = "틀림"
VERDICTS = (RIGHT, WRONG)


def sha(text: str) -> str:
    """지침의 신원. 앞 12자면 사람이 눈으로 구별하기에 넉넉하다."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


@dataclass
class Case:
    """추출 한 번. **입력과 출력이 같이 있어야 재생이 된다.**"""

    id: str
    run: str
    ts: str
    prompt_sha: str
    room: str
    thread: str = ""
    keys: list[str] = field(default_factory=list)
    """이 묶음에 실린 근거 키들. 재생할 때 같은 묶음인지 보는 자다."""

    body: str = ""
    """모델에 실제로 들어간 글. **이게 있어야 슬랙 없이 다시 돌린다.**"""

    raw: str = ""
    """모델이 낸 글 그대로. 파싱 전이다 — 형식이 깨진 판을 되짚는 자리."""

    parsed: dict = field(default_factory=dict)
    applied: dict = field(default_factory=dict)
    seconds: float = 0.0
    tokens: dict = field(default_factory=dict)

    span: list[str] = field(default_factory=list)
    """이 묶음이 덮는 시각 `[처음, 끝]`.

    ★ **판단은 이 시각 기준이다.** 모델은 그 뒤에 무슨 일이 있었는지 몰랐다.
      화면이 이걸 안 보여 주면 사람은 *지금* 기준으로 보게 되고, 그러면
      "이건 이미 끝난 일인데" 로 멀쩡한 판정을 틀렸다고 짚는다.
    """

    marks: dict = field(default_factory=dict)
    """**낸 줄마다** 하나씩. `"opens:0"` → `맞음`/`틀림`.

    ★ 판 단위 판정은 너무 굵다. 고리 셋이 나왔는데 둘이 맞고 하나가
      쓰레기면 그건 맞음도 틀림도 아니다 — 그리고 그 판을 통째로 틀렸다고
      세면 **잘한 둘까지 같이 벌을 받는다.**
    """

    missed: str = ""
    """빠뜨린 것. `""` 아직 안 봄 · `"없음"` · 그 밖은 무엇을 빠뜨렸나.

    ★ 낸 것이 맞는지(정밀도)와 빠뜨린 게 없는지(재현)는 다른 물음이고,
      **묶음을 읽어야만 답할 수 있는 쪽은 뒤엣것**이다. 그래서 화면이
      묶음을 같이 보여 준다.
    """

    verdict: str = ""
    """옛 판의 판 단위 판정. 새 판은 `marks`/`missed` 로 센다."""

    note: str = ""
    """왜 틀렸나. 사람이 한 줄 적는 자리 — 다음 지침이 여기서 나온다."""

    @property
    def items(self) -> list[tuple[str, dict]]:
        """낸 줄들을 `("opens:0", {...})` 로 편다. 화면과 채점이 같은 열쇠를 쓴다."""
        got = []
        for kind in ("moves", "opens", "unresolved"):
            for i, x in enumerate((self.parsed or {}).get(kind) or []):
                got.append((f"{kind}:{i}", x))
        return got

    @property
    def seen(self) -> bool:
        """**빠뜨린 것까지 답해야 본 것이다.** 낸 줄만 짚고 넘어가면
        재현이 영영 안 잰다 — 그리고 안 재는 쪽이 늘 좋아 보인다."""
        return bool(self.missed) or self.verdict in VERDICTS

    @property
    def counts(self) -> dict:
        p = self.parsed or {}
        return {k: len(p.get(k) or []) for k in ("moves", "opens", "unresolved")}


class CaseBook:
    """`cases.jsonl` — 한 줄이 한 판이다.

    ★ JSON 배열이 아니라 줄 단위다. 판이 길면 중간에 죽는데, 배열이면 그때까지
      돌린 것이 통째로 안 읽힌다. 줄이면 죽은 자리까지는 남는다.
    """

    def __init__(self, root: Path | str | None = None) -> None:
        self.path = Path(root if root is not None else default_root()) / "cases.jsonl"

    # --- 적기 ---

    def add(self, **kw) -> Case:
        c = Case(id=uuid.uuid4().hex[:8], ts=clock.now_iso(), **kw)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")
        return c

    def _rewrite(self, items) -> None:
        tmp = self.path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for c in items:
                f.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")
        tmp.replace(self.path)

    def _find(self, items, cid: str) -> Case | None:
        for c in items:
            if c.id == cid or (len(cid) >= 4 and c.id.startswith(cid)):
                return c
        return None

    def mark(self, cid: str, verdict: str, note: str = "", item: str = "") -> Case | None:
        """사람이 짚는다. **이게 사람이 하는 유일한 입력이다.**

        `item` 을 주면 그 줄 하나, 안 주면 판 전체(옛 방식).
        """
        if verdict not in VERDICTS:
            raise ValueError(f"모르는 판정이다: {verdict!r} ({' | '.join(VERDICTS)})")
        items = self.all()
        got = self._find(items, cid)
        if got is not None:
            if item:
                got.marks[item] = verdict
            else:
                got.verdict = verdict
            got.note = note or got.note
            self._rewrite(items)
        return got

    def missed(self, cid: str, what: str = "없음") -> Case | None:
        """빠뜨린 것이 있나. **이걸 답해야 그 판을 본 것이다.**"""
        items = self.all()
        got = self._find(items, cid)
        if got is not None:
            got.missed = what or "없음"
            self._rewrite(items)
        return got

    # --- 읽기 ---

    def all(self) -> list[Case]:
        if not self.path.exists():
            return []
        got = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                got.append(from_dict(Case, json.loads(line)))
            except ValueError:
                continue  # 죽다 만 줄. 나머지는 성하다
        return got

    def runs(self) -> list[str]:
        """판 이름들. **새것이 뒤다.**"""
        seen: dict[str, str] = {}
        for c in self.all():
            seen.setdefault(c.run, c.ts)
        return list(seen)

    def run(self, name: str) -> list[Case]:
        return [c for c in self.all() if c.run == name]

    def last(self) -> str:
        got = self.runs()
        return got[-1] if got else ""

    # --- 점수 ---

    def score(self, name: str = "") -> dict:
        """**본 것 중에** 몇이 맞았나.

        ★ 안 본 것을 맞은 것으로 세면 점수가 저 혼자 오른다. 판이 커질수록
          사람이 보는 비율은 떨어지는데, 그때 점수가 올라가면 그 점수는
          품질이 아니라 게으름을 재는 것이 된다.
        """
        got = self.run(name or self.last())
        본것 = [c for c in got if c.seen]
        깨짐 = [c for c in got if not c.parsed]

        # 정밀도 — **낸 줄 중에** 몇이 맞았나. 안 짚은 줄은 안 센다.
        짚은줄 = [(c, k) for c in got for k in c.marks]
        맞은줄 = [1 for c, k in 짚은줄 if c.marks[k] == RIGHT]
        # 재현 — 빠뜨린 게 없다고 답한 판의 비율. 답 안 한 판은 안 센다.
        답한판 = [c for c in got if c.missed]
        안빠뜨림 = [c for c in 답한판 if c.missed == "없음"]
        return {
            "판": name or self.last(),
            "묶음": len(got),
            "본 것": len(본것),
            "낸 줄": sum(len(c.items) for c in got),
            "짚은 줄": len(짚은줄),
            "정밀도": round(len(맞은줄) / len(짚은줄), 3) if 짚은줄 else None,
            "재현": round(len(안빠뜨림) / len(답한판), 3) if 답한판 else None,
            "형식 깨짐": len(깨짐),
            "낸 것": {
                k: sum(c.counts[k] for c in got)
                for k in ("moves", "opens", "unresolved")
            },
            "지침": sorted({c.prompt_sha for c in got}),
        }

    def carry(self, frm: str, to: str) -> int:
        """앞 판의 판정을 **같은 묶음**에 물려준다.

        ★ 지침만 바꿔 다시 돌리면 묶음은 그대로다. 사람이 이미 본 것을 또
          보게 하면 **두 번째 판부터 아무도 안 본다** — 그러면 눈금이 첫 판에서
          멈춘다. 낸 답이 글자 그대로 같을 때만 물려준다. 답이 달라졌으면
          그건 다시 봐야 하는 것이다.
        """
        옛것 = {(c.room, c.thread): c for c in self.run(frm) if c.seen}
        items, n = self.all(), 0
        for c in items:
            if c.run != to or c.seen:
                continue
            old = 옛것.get((c.room, c.thread))
            if old is not None and old.raw.strip() == c.raw.strip():
                c.marks, c.missed = dict(old.marks), old.missed
                c.verdict, c.note = old.verdict, old.note
                n += 1
        if n:
            self._rewrite(items)
        return n

    def prune(self, keep_runs: int = 5) -> int:
        """최근 몇 판만 남긴다. **팀원의 글이 여기 있다** — 안 버리면 자란다."""
        got = self.all()
        남길판 = set(self.runs()[-keep_runs:]) if keep_runs > 0 else set()
        남길 = [c for c in got if c.run in 남길판]
        지운수 = len(got) - len(남길)
        if 지운수:
            self._rewrite(남길)
        return 지운수

    def __len__(self) -> int:
        return len(self.all())
