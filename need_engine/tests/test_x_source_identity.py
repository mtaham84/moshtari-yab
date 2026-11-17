from datetime import datetime, timezone
from types import SimpleNamespace

from need_engine.config import EngineConfig
from need_engine.engine import NeedEngine
from need_engine.schemas import NeedCard


def test_x_opportunity_keeps_x_platform_when_evidence_is_unavailable(monkeypatch):
    monkeypatch.setattr('need_engine.engine.evidence_messages', lambda *_: [])
    engine = NeedEngine.__new__(NeedEngine)
    engine.cfg, engine.store = EngineConfig(), SimpleNamespace()
    now = datetime.now(timezone.utc)
    need = NeedCard(need_id='n1', chat_id='x:p:60', author_id='x_buyer', label='explicit_need',
                    is_opportunity=True, strength='strong', status='open', created_at=now, updated_at=now)
    assert engine._opportunity(need, []).source.platform == 'x'
