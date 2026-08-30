"""에이전트 골격 — 인격은 여기 없다.

여기 있는 것은 **에이전트가 누구인지와 무관한 부품**이다: 파일에 쓰는 법,
시간을 읽는 법, 잔고를 세는 법, 도구 계약을 지키는 법.

무엇을 말할지, 언제 말을 걸지, 무엇을 원칙으로 삼을지는 여기서 안 정한다.
그건 그 에이전트가 누구인지에 대한 것이라 만드는 쪽이 정한다.

    genie_agents/            런타임
      store clock config env gate singleton talent
      usage world sources reminders media mailbox toolcontract
      messaging/             선택 — 여럿이 서로 말할 때만
        peers delivery
      adapters/              모델을 갈아끼우는 자리

에이전트마다 다른 것 하나는 **환경변수 프리픽스**다(`env.py`). 한 호스트에
여럿이 살면서 서로의 설정과 상태 디렉토리를 안 밟게 하는 자리다.
"""

__all__ = [
    "clock", "config", "env", "gate", "mailbox", "media", "reminders",
    "singleton", "sources", "store", "talent", "toolcontract", "usage", "world",
]
