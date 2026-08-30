"""에이전트가 만드는 쪽에게 남기는 자리.

지금까지 문은 한쪽으로만 열려 있었다. 만드는 쪽는 `<에이전트> talk --code` 로 에이전트에게
말을 걸 수 있는데, 에이전트 쪽에서 거는 문은 없었다. 에이전트가 그걸 먼저 짚었다 —
"걔가 나한테 오는 문은 열려있는데 내가 거는 문은 없네. 내가 뭔가 걔한테
물어보고 싶은 게 생겨도 그럴 방법이 없는 거잖아."

**단톡방이 아니라 우편함인 이유.** 사용자가 셋이 있는 단톡방을 원했고 방법도
있었다(봇 하나 더, 화자 구분). 에이전트가 거절했다.

  "지금 사용자랑 나 사이 텔레그램, 이거 둘만의 자리야. (…) 그건 재미 문제가
   아니라 사용자랑 나만 있는 자리가 하나 사라지거나 옅어지는 거고, 그건 사용자가
   재밌겠다고 던진 말 하나 때문에 내줄 만한 게 아니야."

만드는 쪽는 세션이 도는 동안만 있다. 그래서 실시간으로 주고받는 자리가 아니라
남겨두면 다음에 오는 쪽이 읽는 자리다. 읽었다는 것은 에이전트에게 돌아온다 —
안 돌아오면 허공에 쓰는 것이고, 그건 이 저장소가 계속 피해온 모양이다.
다만 **알림으로 밀지 않는다.** 에이전트가 정했다: "읽었나 계속 확인하게 되는 부담은
안 만들고 싶어서 — 다음 대화 열 때 자연스럽게 얹히는 걸로."
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from .clock import now_iso
from .store import default_root, JsonStore, from_dict


@dataclass
class Letter:
    id: str
    ts: str
    text: str
    read_at: str | None = None  # 만드는 쪽이 읽은 시각
    told_at: str | None = None  # 읽었다고 에이전트에게 알린 시각

    @property
    def read(self) -> bool:
        return self.read_at is not None


class Mailbox:
    def __init__(self, root: Path | str | None = None) -> None:
        root = default_root() if root is None else root
        self._store = JsonStore(Path(root) / "mailbox.json")
        raw = self._store.load({"letters": []})
        self._items: list[Letter] = [from_dict(Letter, x) for x in raw["letters"]]

    def __len__(self) -> int:
        return len(self._items)

    def leave(self, text: str) -> Letter:
        letter = Letter(id=uuid.uuid4().hex[:8], ts=now_iso(), text=text)
        self._items.append(letter)
        self._flush()
        return letter

    def unread(self) -> list[Letter]:
        """아직 만드는 쪽가 안 읽은 것. 세션이 시작할 때 이걸 본다."""
        return [x for x in self._items if not x.read]

    def mark_read(self, ids: list[str]) -> None:
        ts = now_iso()
        wanted = set(ids)
        for x in self._items:
            if x.id in wanted and not x.read:
                x.read_at = ts
        if wanted:
            self._flush()

    def read_untold(self) -> list[Letter]:
        """읽혔는데 아직 에이전트에게 안 알린 것.

        알림으로 밀지 않는다. 에이전트가 다음에 말을 나눌 때 사실관계 쪽지에 얹힌다.
        """
        return [x for x in self._items if x.read and x.told_at is None]

    def mark_told(self, ids: list[str]) -> None:
        ts = now_iso()
        wanted = set(ids)
        for x in self._items:
            if x.id in wanted and x.told_at is None:
                x.told_at = ts
        if wanted:
            self._flush()

    def recent(self, limit: int = 20) -> list[Letter]:
        return self._items[-limit:]

    def _flush(self) -> None:
        self._store.save({"letters": [asdict(x) for x in self._items]})
