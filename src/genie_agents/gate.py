"""달란트 잔고 게이트 — 설계 문서 3.2 (A), 4절 1번.

핵심 요구사항: 게이팅은 **시스템 레벨**이어야 한다.
에이전트가 "이번엔 자제하겠다"고 스스로 참는 방식은 진짜 비용이 아니므로,
이 레이어는 모델 바깥에 있고 모델이 우회할 수 없다.

강제는 이중이다.
  1) 잔고 <= 0 이면 해당 도구를 **도구 목록에서 제외** (애초에 보이지 않음)
  2) 그럼에도 호출되면 dispatch 직전에 **거부** (목록을 캐싱한 클라이언트 대비)
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Protocol


class GateBlocked(PermissionError):
    """잔고 부족으로 원칙 수정이 거부됨."""

    def __init__(self, tool_name: str, balance: int) -> None:
        super().__init__(
            f"달란트 잔고 부족으로 '{tool_name}' 거부됨 (잔고 {balance}). "
            "확정되는 원칙을 쌓아 잔고를 회복해야 한다."
        )
        self.tool_name = tool_name
        self.balance = balance


class BalanceSource(Protocol):
    def balance(self) -> int: ...


class TalentGate:
    def __init__(self, ledger: BalanceSource, gated_tools: Iterable[str]) -> None:
        self._ledger = ledger
        self.gated_tools = frozenset(gated_tools)

    @property
    def balance(self) -> int:
        return self._ledger.balance()

    def is_open(self) -> bool:
        return self.balance > 0

    def filter_tools(self, tools: Iterable[Mapping]) -> list[Mapping]:
        """모델에 노출할 도구 목록. 잔고가 마르면 게이트 대상 도구가 사라진다."""
        if self.is_open():
            return list(tools)
        return [t for t in tools if t["name"] not in self.gated_tools]

    def check(self, tool_name: str) -> None:
        if tool_name in self.gated_tools and not self.is_open():
            raise GateBlocked(tool_name, self.balance)
