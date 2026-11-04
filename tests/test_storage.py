"""数据沉淀测试:记录、聚合、自动跳过、提醒阈值、KV 存取。"""

from core.prober import ProbeResult, STATUS_ERROR, STATUS_OK, STATUS_SKIPPED, STATUS_SLOW
from core.storage import (
    StateStore,
    aggregate,
    consecutive_failures,
    new_state,
    record_round,
    should_auto_skip,
    should_notice_long_unavailable,
)

NOW = 1_000_000.0


def _r(pid, status, latency=1000, error=""):
    return ProbeResult(provider_id=pid, status=status, latency_ms=latency, error=error)


def test_record_round_appends_and_trims():
    state = new_state()
    for i in range(5):
        record_round(state, [_r("a", STATUS_OK, latency=100 + i)], ts=NOW + i, history_max=3)
    records = state["history"]["a"]
    assert len(records) == 3
    assert records[0]["latency_ms"] == 102  # 100,101 被滚掉,剩 102,103,104
    assert records[-1]["latency_ms"] == 104


def test_skipped_not_recorded():
    state = new_state()
    state["consec_fail"]["a"] = 2
    record_round(state, [_r("a", STATUS_SKIPPED)], ts=NOW, history_max=10)
    assert state["history"].get("a", []) == []
    assert state["consec_fail"]["a"] == 2      # 连续失败计数不动
    assert "a" not in state["last_probe_ts"]   # 复活检查时间戳不动


def test_consec_fail_accumulates_and_resets():
    state = new_state()
    record_round(state, [_r("a", STATUS_ERROR)], ts=NOW, history_max=10)
    record_round(state, [_r("a", STATUS_ERROR)], ts=NOW, history_max=10)
    assert consecutive_failures(state, "a") == 2
    record_round(state, [_r("a", STATUS_OK)], ts=NOW, history_max=10)
    assert consecutive_failures(state, "a") == 0
    record_round(state, [_r("a", STATUS_SLOW)], ts=NOW, history_max=10)
    assert consecutive_failures(state, "a") == 0  # slow 也算可用,归零


def test_aggregate_window_and_recent():
    history = [
        {"ts": NOW - 8 * 86400, "status": STATUS_OK, "latency_ms": 100},   # 窗口外
        {"ts": NOW - 3 * 86400, "status": STATUS_OK, "latency_ms": 200},
        {"ts": NOW - 2 * 86400, "status": STATUS_SLOW, "latency_ms": 40000},
        {"ts": NOW - 86400, "status": STATUS_ERROR, "latency_ms": 60000},
    ]
    stats = aggregate(history, now=NOW, window_seconds=7 * 86400, recent_n=2)
    assert stats.total == 3
    assert stats.ok == 1 and stats.slow == 1 and stats.error == 1
    assert abs(stats.available_rate - 2 / 3) < 1e-9
    assert stats.avg_latency_ms == (200 + 40000) // 2
    assert [s["status"] for s in stats.recent] == [STATUS_SLOW, STATUS_ERROR]


def test_aggregate_empty():
    stats = aggregate([], now=NOW, window_seconds=7 * 86400, recent_n=20)
    assert stats.total == 0
    assert stats.available_rate is None
    assert stats.avg_latency_ms is None


def test_auto_skip_logic():
    state = new_state()
    state["consec_fail"]["a"] = 5
    state["last_probe_ts"]["a"] = NOW - 3600  # 1 小时前测过

    # 关闭
    assert should_auto_skip(state, "a", skip_threshold=0, now=NOW) is False
    # 未达阈值
    assert should_auto_skip(state, "a", skip_threshold=6, now=NOW) is False
    # 达阈值且 24h 内测过 → 跳过
    assert should_auto_skip(state, "a", skip_threshold=5, now=NOW) is True
    # 超过 24h 没测 → 放行复测
    state["last_probe_ts"]["a"] = NOW - 90000
    assert should_auto_skip(state, "a", skip_threshold=5, now=NOW) is False


def test_notice_threshold():
    # 恰好跨过阈值
    assert should_notice_long_unavailable(23, 24, notice_threshold=24) is True
    # 中间状态不重复提醒
    assert should_notice_long_unavailable(24, 25, notice_threshold=24) is False
    # 里程碑再提醒
    assert should_notice_long_unavailable(47, 48, notice_threshold=24) is True
    # 阈值关闭
    assert should_notice_long_unavailable(23, 24, notice_threshold=0) is False


class _FakeKV:
    def __init__(self):
        self.data = {}

    async def put_kv_data(self, key, value):
        self.data[key] = value

    async def get_kv_data(self, key, default=None):
        return self.data.get(key, default)


def test_state_store_roundtrip():
    import asyncio

    async def main():
        kv = _FakeKV()
        store = StateStore(kv)
        state = await store.load()
        assert state == new_state() | {} or state["history"] == {}
        state["history"]["a"] = [{"ts": 1, "status": "ok", "latency_ms": 1, "error": ""}]
        await store.save(state)
        loaded = await StateStore(kv).load()
        assert loaded["history"]["a"][0]["ts"] == 1

    asyncio.run(main())
