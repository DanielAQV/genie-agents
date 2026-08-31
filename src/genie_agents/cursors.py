"""커서 — 어디까지 읽었나.

    .followup/cursors.json    {"slack:C0123": "1756...", "gmail": "<historyId>"}

상시 프로세스가 없으니 60초짜리 폴링도 없다. 꺼져 있던 시간만큼 창이 벌어질
뿐이고, **`oldest=커서` 가 그걸 그대로 메운다**(`docs/wiring.md` 2절).

━━ 커서는 쌓은 뒤에 옮긴다 ━━

★ **순서가 규칙이다.** 긁고 → 쌓고 → 그 다음에 옮긴다. 먼저 옮기면 그 사이에
  프로세스가 죽었을 때 그 창이 **영영** 안 온다. 다시 긁는 것은 값이 싸고
  (`transcript.Book.put` 이 같은 키를 두 번 안 넣는다), 안 긁는 것은 값이 없다.

  그래서 이 모듈은 "긁으면서 옮기는" 편한 함수를 안 낸다. 편하게 만들면
  부르는 쪽이 쌓기 전에 옮긴다.

━━ 값을 해석하지 않는다 ━━

★ 소스마다 커서의 종류가 다르다 — Slack 은 epoch 문자열, Gmail 은 `historyId`,
  Graph 는 `deltaLink` URL 통째. 여기서 크고 작음을 따지려 들면 소스마다 다른
  비교가 이 파일에 쌓인다. **문자열을 그대로 들고 있는 자리**로만 둔다.
"""

from __future__ import annotations

from pathlib import Path

from .store import JsonStore, default_root


class Cursors:
    def __init__(self, root: Path | str | None = None) -> None:
        # 기본 인자로 두면 프리픽스가 걸리기 전 값에 굳는다(`store.default_root`).
        self._store = JsonStore(
            Path(root if root is not None else default_root()) / "cursors.json"
        )
        self._at: dict[str, str] = dict(self._store.load({"at": {}}).get("at", {}))

    def get(self, key: str, default: str = "") -> str:
        """없으면 빈 문자열. **처음 도는 날은 커서가 없다** — 부르는 쪽이
        그때 얼마나 거슬러 올라갈지 정한다. 여기서 정하면 소스마다 다른
        기본값이 이 파일에 쌓인다."""
        return self._at.get(key, default)

    def set(self, key: str, value: str) -> None:
        """**쌓은 뒤에 부른다.** 위 주석이 이 함수 하나를 위해 있다."""
        if not value or self._at.get(key) == value:
            return
        self._at[key] = value
        self._store.save({"at": self._at})

    def all(self) -> dict[str, str]:
        return dict(self._at)

    def drop(self, key: str) -> bool:
        """지운다. 다음에 그 자리를 처음부터 다시 읽게 된다."""
        if key not in self._at:
            return False
        del self._at[key]
        self._store.save({"at": self._at})
        return True

    def __len__(self) -> int:
        return len(self._at)
