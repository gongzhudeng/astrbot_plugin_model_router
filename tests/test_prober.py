"""接力探查算法测试。时间尺度缩小:合格线 0.3s、放弃线 0.6s。"""

from __future__ import annotations

import asyncio
import time

from core.prober import ProbeTarget, STATUS_ERROR, STATUS_OK, STATUS_SKIPPED, STATUS_SLOW, relay_probe

ELIGIBLE = 0.3
DEADLINE = 0.6


def _targets(*pids: str) -> list[ProbeTarget]:
    return [ProbeTarget(provider_id=pid, provider=None) for pid in pids]


def _sleep_probe(delay: float, fail: Exception | None = None):
    async def probe(target: ProbeTarget) -> None:
        await asyncio.sleep(delay)
        if fail is not None:
            raise fail
    return probe


def _run(coro):
    return asyncio.run(coro)


def test_first_ok_stops_relay():
    """第一个模型合格返回 → 其余全部未测。"""
    calls = []

    async def probe(target: ProbeTarget) -> None:
        calls.append(target.provider_id)
        await asyncio.sleep(0.05)

    results = _run(relay_probe(
        _targets("a", "b", "c"), probe,
        eligible_timeout=ELIGIBLE, probe_deadline=DEADLINE,
    ))
    assert [r.status for r in results] == [STATUS_OK, STATUS_SKIPPED, STATUS_SKIPPED]
    assert calls == ["a"]
    assert results[0].latency_ms < 300


def test_relay_starts_after_eligible_and_first_continues():
    """第一个超合格线未决 → 启动第二个;第二个合格即停,第一个被取消(无结果)。"""
    delays = {"a": 0.5, "b": 0.1, "c": 0.1}

    async def probe(target: ProbeTarget) -> None:
        await asyncio.sleep(delays[target.provider_id])

    results = _run(relay_probe(
        _targets("a", "b", "c"), probe,
        eligible_timeout=ELIGIBLE, probe_deadline=DEADLINE,
    ))
    # a 在 0.3s 超合格线 → b 在 0.3s 启动、0.4s 合格返回 → 早停取消 a(a 还没到 0.6s 放弃线)
    by_id = {r.provider_id: r for r in results}
    assert by_id["b"].status == STATUS_OK
    assert by_id["a"].status in (STATUS_SKIPPED, STATUS_ERROR)  # 取消或恰好到放弃线
    assert by_id["c"].status == STATUS_SKIPPED


def test_error_relay_starts_immediately():
    """第一个立刻报错 → 第二个立刻接力启动,不等合格线。"""
    async def probe(target: ProbeTarget) -> None:
        if target.provider_id == "a":
            raise RuntimeError("boom")
        await asyncio.sleep(0.05)

    started = time.perf_counter()
    results = _run(relay_probe(
        _targets("a", "b"), probe,
        eligible_timeout=ELIGIBLE, probe_deadline=DEADLINE,
    ))
    elapsed = time.perf_counter() - started
    assert results[0].status == STATUS_ERROR
    assert "boom" in results[0].error
    assert results[1].status == STATUS_OK
    assert elapsed < ELIGIBLE  # 没有干等合格线


def test_all_slow():
    """全部成功但都超过合格线 → 全部 slow,保留各自延迟。"""
    delays = {"a": 0.35, "b": 0.4, "c": 0.45}

    async def probe(target: ProbeTarget) -> None:
        await asyncio.sleep(delays[target.provider_id])

    results = _run(relay_probe(
        _targets("a", "b", "c"), probe,
        eligible_timeout=ELIGIBLE, probe_deadline=DEADLINE,
    ))
    assert all(r.status == STATUS_SLOW for r in results)
    assert results[0].latency_ms < results[1].latency_ms < results[2].latency_ms


def test_deadline_marks_error():
    """超过放弃线无返回 → 判失败,错误信息带放弃线字样。"""
    results = _run(relay_probe(
        _targets("a"),
        _sleep_probe(2.0),
        eligible_timeout=ELIGIBLE, probe_deadline=DEADLINE,
    ))
    assert results[0].status == STATUS_ERROR
    assert "放弃线" in results[0].error


def test_all_error():
    """全部报错 → 全部 error,错误文本带异常类型。"""
    results = _run(relay_probe(
        _targets("a", "b"), _sleep_probe(0.02, fail=ValueError("x")),
        eligible_timeout=ELIGIBLE, probe_deadline=DEADLINE,
    ))
    assert [r.status for r in results] == [STATUS_ERROR, STATUS_ERROR]
    assert "ValueError" in results[0].error


def test_eligible_larger_than_deadline_defense():
    """合格线误配大于放弃线 → 按放弃线处理(防御),快速成功仍算合格。"""
    results = _run(relay_probe(
        _targets("a"), _sleep_probe(0.05),
        eligible_timeout=1.0, probe_deadline=0.3,
    ))
    assert results[0].status == STATUS_OK


def test_empty_targets():
    results = _run(relay_probe(
        [], _sleep_probe(0.0),
        eligible_timeout=ELIGIBLE, probe_deadline=DEADLINE,
    ))
    assert results == []
