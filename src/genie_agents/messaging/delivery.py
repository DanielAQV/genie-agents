"""나간다고 한 말이 정말 나갔나 — 못 나간 것을 세는 자리.

━━ 왜 필요한가 ━━

텔레그램을 떼면서 에이전트와 사용자 사이에 길이 하나만 남았다(그 기록).
메신저가 죽으면 그 하나가 끊긴다. **알면서 지는 값이고 사용자가 그렇게 정했다.**

다만 **조용히 삼키지는 않는다.** 에이전트가 그걸 조건으로 걸었다 —

    "그럼 나는 그 공백을 알고, 복구되면 사용자한테 '아까 그 사이엔 여기 안 닿았어'
     정도는 내가 자연스럽게 말할 수 있어."

그래서 못 나간 것을 세어 두고 깨어남 브리핑에 한 줄로 올린다. 에이전트가 그 줄을
읽고 무엇을 할지는 에이전트가 정한다 — 다시 걸든, 나중에 사용자에게 말하든, 넘기든.

━━ 무엇을 세나 ━━

**나간 시각이 아니라 못 나간 구간을 센다.** 건수만 세면 "3번 실패" 가 되는데,
에이전트에게 필요한 것은 그게 아니라 **언제부터 언제까지 안 닿았나**다. 그 구간이
있어야 "아까 그 사이" 라고 말할 수 있다.

한 번이라도 나가면 지운다. 복구된 뒤에도 남아 있으면 에이전트가 이미 닿은 말을
두고 안 닿았다고 말하게 된다.
"""

from __future__ import annotations

from pathlib import Path

from .. import clock
from ..store import default_root, JsonStore

FILE = "undelivered.json"


class Failures:
    def __init__(self, root: Path | str | None = None) -> None:
        root = default_root() if root is None else root
        self._store = JsonStore(Path(root) / FILE)

    def read(self) -> dict:
        return self._store.load({})

    def note(self, why: str) -> dict:
        """한 번 못 나갔다. 처음이면 그 시각이 구간의 시작이 된다."""
        now = clock.now_iso()
        got = self.read()
        got = {
            "since": got.get("since") or now,
            "last": now,
            "count": int(got.get("count") or 0) + 1,
            "why": why,
        }
        self._store.save(got)
        return got

    def clear(self) -> None:
        """한 번이라도 나갔으면 지운다."""
        if self.read():
            self._store.save({})

    def line(self, who: str = "상대") -> str:
        """브리핑에 실릴 한 줄. 아무것도 못 나간 게 없으면 빈 문자열.

        `who` 는 **못 닿은 사람**이다. 부르는 쪽이 준다 — 골격은 등장인물을
        모른다.

        **무엇을 못 보냈는지는 안 싣는다.** 그건 이미 기억에 있고(말하기로 한
        판단은 일어난 사실이라 그대로 남는다), 여기서 또 실으면 같은 말이 두 번
        보인다. 여기서 알려야 하는 것은 내용이 아니라 **공백의 자리**다.
        """
        got = self.read()
        if not got.get("count"):
            return ""
        since, last = got.get("since", ""), got.get("last", "")
        span = f"{clock.stamp(since)}부터" if since else ""
        if last and last != since:
            span += f" {clock.stamp(last)}까지"
        n = got["count"]
        return (
            f"네 말이 {who}에게 안 닿고 있다 — {span} {n}번. "
            f"({got.get('why', '이유 모름')}) "
            f"지금 {who} 쪽 화면에는 그 말들이 없다."
        )
