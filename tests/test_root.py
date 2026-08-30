"""상태 디렉토리는 섞이지 않는다.

이 저장소의 첫 규칙이고(`yena/CLAUDE.md`: "달란트 원장이 섞이면 두 존재가 아니라
지갑을 같이 쓰는 하나가 된다"), **실제로 한 번 깨졌다.**

골격을 뽑으면서 `DEFAULT_ROOT = Path(".yuna")` 를 그대로 들고
갔고 예나가 그걸 import 했다. 예나의 달란트 원장이 `.yuna/talent.json` 에
앉았는데 **시험은 전부 root 를 손으로 넘겨서 아무도 못 잡았다.**

그래서 여기서는 손으로 안 넘겼을 때를 본다.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from genie_agents import env
from genie_agents.store import default_root
from genie_agents.talent import TalentLedger


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(env.VAR, raising=False)
    for k in ("YUNA_ROOT", "YENA_ROOT", "ROOT"):
        monkeypatch.delenv(k, raising=False)


def test_프리픽스마다_다른_자리에_앉는다(monkeypatch):
    monkeypatch.setenv(env.VAR, "YUNA")
    assert default_root() == Path(".yuna")
    monkeypatch.setenv(env.VAR, "YENA")
    assert default_root() == Path(".yena")


def test_원장이_섞이지_않는다(monkeypatch):
    """root 를 안 넘겼을 때가 위험한 자리다. 넘기면 어차피 안 섞인다."""
    monkeypatch.setenv(env.VAR, "YUNA")
    한쪽 = TalentLedger()._store.path
    monkeypatch.setenv(env.VAR, "YENA")
    다른쪽 = TalentLedger()._store.path

    assert 한쪽 != 다른쪽
    assert ".yuna" in str(한쪽) and ".yena" in str(다른쪽)


def test_환경변수로_옮길_수_있다(monkeypatch, tmp_path):
    monkeypatch.setenv(env.VAR, "YUNA")
    monkeypatch.setenv("YUNA_ROOT", str(tmp_path / "어딘가"))
    assert default_root() == tmp_path / "어딘가"


def test_기본_인자로_굳어_있지_않다():
    """`def f(root=default_root())` 로 쓰면 def 를 읽는 순간 한 번 정해지고,
    그러면 먼저 import 된 쪽의 자리가 양쪽에 박힌다 — 그게 원래 사고였다."""
    from genie_agents.messaging.delivery import Failures
    from genie_agents.mailbox import Mailbox
    from genie_agents.media import MediaStore
    from genie_agents.reminders import ReminderStore
    from genie_agents.world import WorldFeed

    for cls in (TalentLedger, Failures, Mailbox, MediaStore, ReminderStore, WorldFeed):
        default = inspect.signature(cls.__init__).parameters["root"].default
        assert default is None, f"{cls.__name__} 이 기본값을 굳혀 들고 있다: {default}"
