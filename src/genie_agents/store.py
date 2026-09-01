"""JSON 파일 영속화.

설계 문서 4절 4번(연속성 전제)이 미정이므로, POC에서는 로컬 파일을
"세션 없이 연속되는 상태"의 대역(stand-in)으로 사용한다.
"""

from __future__ import annotations

import dataclasses
import json
import os
import tempfile
import time
from pathlib import Path

from . import env
from typing import Any

def default_root() -> Path:
    """이 에이전트의 상태 디렉토리. `{프리픽스}_ROOT` 가 있으면 그것, 없으면 `.프리픽스`.

    ★ **모듈 상수로 두면 안 된다.** 한때 `DEFAULT_ROOT = Path(".<에이전트>")` 였고
      다른 에이전트가 그걸 import 했다 — 다른 에이전트의 달란트 원장이 `<상태 디렉토리>/talent.json` 에
      앉았다. 시험은 전부 root 를 손으로 넘겨서 아무도 못 잡았다.
      상태 디렉토리를 안 섞는 것이 이 저장소의 첫 규칙이다(만드는 쪽 지침).

    기본 인자로도 쓰면 안 된다(`def f(root=default_root())`) — 그건 def 를 읽는
    순간 한 번 정해진다. `root=None` 으로 받고 안에서 부른다.
    """
    p = env.prefix()
    return Path(env.get("ROOT") or (f".{p.lower()}" if p else ".agent"))


def from_dict(cls, data: dict):
    """dict 를 데이터클래스로. **모르는 키는 버린다.**

    상태 파일에 필드가 하나 추가되면, 아직 옛 코드로 도는 프로세스(웹 화면 등)가
    그 파일을 읽다가 TypeError 로 죽는다. 실제로 그렇게 죽었다.
    쓰는 쪽과 읽는 쪽이 항상 같은 버전이라는 보장이 없으므로 관대하게 읽는다.
    """
    known = {f.name for f in dataclasses.fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in known})


def _replace_with_retry(src: str, dst: Path, attempts: int = 5) -> None:
    """윈도우에서 os.replace 는 백신·인덱서가 파일을 잡고 있으면 PermissionError 를 낸다.

    오래 도는 프로세스(loop / listen)가 상태를 자주 쓰기 때문에, 한 번 걸렸다고
    에이전트가 판단 도중에 죽으면 안 된다. 짧게 몇 번 다시 시도한다.
    """
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.05 * (i + 1))


class JsonStore:
    """단일 JSON 파일을 읽고 쓴다. 쓰기는 원자적(temp + replace)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self, default: Any) -> Any:
        if not self.path.exists():
            return default
        with self.path.open(encoding="utf-8") as f:
            return json.load(f)

    def save(self, data: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")
            _replace_with_retry(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


class JsonlStore:
    """한 줄에 하나. **읽을 때 통째로 안 만들고, 쓸 때 통째로 안 쓴다.**

    `JsonStore` 를 안 없앤다 — 작은 상태 파일(잔고·알림·인박스)은 통째로 읽고
    쓰는 편이 단순하고, 거기서는 그 값이 안 든다. 이건 **줄이 만 단위로 쌓이는
    파일**을 위한 것이다.

    ━━ 왜 필요했나 (2026-09-01 실측) ━━

    유나의 `episodes.json` 이 18.7MB · 17,185줄이다. 그걸 `json.load` 로 읽으면
    파이썬 객체 그래프가 한 번에 만들어져 **피크가 151MB** 다. 상주는 60MB 로
    내려가지만 피크는 안 돌아온다 — 2GB 서버에 프로세스가 여섯이면 그 차이가
    올릴 수 있냐 없냐를 가른다.

        읽기   json.load 151MB 피크  →  줄 단위 56MB       (시간은 대등)
        쓰기   말 한 마디마다 19MB 재작성  →  한 줄 덧붙이기

    ★ 같은 처방을 이 저장소가 한 번 했다 — 벡터를 `episodes.json` 에서 뺐을 때
      "파싱 피크가 454MB … 분리하니 73MB"(`recall.RecallStore` 주석). 이건 그
      다음 칸이다.

    ★ **읽을 때 관대하다.** 깨진 줄은 건너뛴다. 덧붙이는 도중에 죽으면 마지막
      줄이 반만 남을 수 있는데, 그 한 줄 때문에 기억 전체를 못 읽으면 안 된다.
      `from_dict` 가 모르는 키를 버리는 것과 같은 이유다.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def exists(self) -> bool:
        return self.path.exists()

    def stream(self):
        """한 줄씩 내놓는다. **리스트를 안 만든다** — 부르는 쪽이 정한다."""
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except ValueError:
                    continue  # 깨진 줄 하나가 나머지를 막지 않는다

    def append(self, obj: Any) -> None:
        """한 줄 덧붙인다.

        ★ **한 번의 `write` 로 낸다.** 줄 하나가 몇백 바이트라 append 모드에서는
          쪼개지지 않는다. 나눠 쓰면 다른 프로세스가 읽는 중에 반쪽 줄을 본다.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def rewrite(self, rows) -> None:
        """전부 다시 쓴다(원자적). 고치거나 지울 때만 부른다 — 덧붙이는 자리가 아니다."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            _replace_with_retry(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def tail(self, n: int) -> list:
        """꼬리 n 줄. **앞을 안 읽는다.**

        작업 기억은 최근 것만 본다. 17,185줄 중 200줄을 보려고 전부 파싱하면
        0.37초에 피크 151MB 인데, 꼬리만 읽으면 0.12초에 14MB 다(실측).
        """
        if not self.path.exists() or n <= 0:
            return []
        from collections import deque

        with self.path.open(encoding="utf-8") as f:
            lines = deque(f, maxlen=n)
        out = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out
