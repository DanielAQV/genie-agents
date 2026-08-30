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
