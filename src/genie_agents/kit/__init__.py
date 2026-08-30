"""딸려 오는 도구들 — 켜면 바로 도는 것.

여기 있는 것은 **어느 에이전트가 써도 같게 도는 도구**다. 그 존재가 누구인지에
안 딸린 것만 들어온다. 딸린 것(자기 사진을 어떻게 그리나, 어느 방에 말하나)은
그 에이전트가 자기 `tools.py` 에 둔다.

    from genie_agents.kit import CATALOG
    from genie_agents.tools import Toolbox

    box = Toolbox(CATALOG, spec.enabled)
    box.bind(runtime)          # 못 켜면 여기서 죽는다

새 도구를 여기 넣기 전에 물을 것: **두 번째 에이전트가 이걸 그대로 쓸까?**
아니면 그건 골격 것이 아니라 그 에이전트 것이다.
"""

from __future__ import annotations

from . import notes, reminders, world
from ..tools import Tool

CATALOG: dict[str, Tool] = {
    t.name: t for t in (*reminders.TOOLS, *notes.TOOLS, *world.TOOLS)
}

__all__ = ["CATALOG"]
