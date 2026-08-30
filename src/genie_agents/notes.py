"""메모 — 에이전트가 스스로 남기고 스스로 찾는 것.

**대화 기록이 아니다.** 오간 말을 통째로 쌓는 자리는 그 에이전트가 어떻게
기억하는지에 딸린 것이라 여기 두지 않는다(임베딩을 쓸지, 자리별로 가를지,
무엇을 잊을지는 전부 그 존재의 문제다).

여기 있는 것은 **의도적으로 남긴 한 줄**이다. 남긴 것만 있어서 찾을 때
"왜 이게 나왔지" 가 없다.

━━ 왜 임베딩을 안 쓰나 ━━

찾기가 글자 맞춤이다. 벡터를 넣으면 모델과 차원과 재색인이 따라오고, 그건
골격이 감당할 것이 아니다. **필요한 쪽이 이 자리를 갈아 끼운다** — 도구는
`ctx.notes` 만 보므로 같은 세 메서드를 가진 것이면 무엇이든 들어온다.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .clock import now_iso
from .store import JsonStore, default_root


@dataclass
class Note:
    id: str
    ts: str
    text: str
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> Note:
        return cls(id=d["id"], ts=d["ts"], text=d["text"], tags=list(d.get("tags") or []))


class NoteStore:
    def __init__(self, root: Path | str | None = None) -> None:
        # ★ 여기서 `default_root()` 를 부른다. 기본 인자로 두면 def 를 읽는
        #   순간 한 번 정해져서, 프리픽스가 걸리기 전 값에 굳는다.
        self._store = JsonStore(Path(root if root is not None else default_root()) / "notes.json")
        raw = self._store.load({"notes": []})
        self._items: list[Note] = [Note.from_dict(d) for d in raw.get("notes", [])]

    def __len__(self) -> int:
        return len(self._items)

    def write(self, text: str, tags: list[str] | None = None) -> Note:
        n = Note(id=uuid.uuid4().hex[:8], ts=now_iso(), text=text, tags=list(tags or []))
        self._items.append(n)
        self._flush()
        return n

    def recall(self, query: str = "", limit: int = 5, tag: str = "") -> list[Note]:
        """최근 것부터. 빈 질의는 그냥 최근 것이다 — 찾을 말이 없다고 아무것도
        안 주면 "무엇을 남겨 뒀는지" 를 물을 길이 없다."""
        q = query.strip().lower()
        got = [
            n for n in reversed(self._items)
            if (not q or q in n.text.lower())
            and (not tag or tag in n.tags)
        ]
        return got[:limit]

    def drop(self, nid: str) -> bool:
        before = len(self._items)
        self._items = [n for n in self._items if n.id != nid]
        if len(self._items) != before:
            self._flush()
            return True
        return False

    def _flush(self) -> None:
        self._store.save({"notes": [asdict(n) for n in self._items]})
