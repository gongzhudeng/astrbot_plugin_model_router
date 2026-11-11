"""决策与切换判定测试。"""

from core.prober import ProbeResult, STATUS_ERROR, STATUS_OK, STATUS_SKIPPED, STATUS_SLOW
from core.router import (
    REASON_ALL_FAILED,
    REASON_FASTEST_SLOW,
    REASON_OK,
    build_long_unavailable_notice,
    current_status_of,
    decide,
    extract_number,
    should_switch,
)


def _r(pid, status, latency=100, error=""):
    return ProbeResult(provider_id=pid, status=status, latency_ms=latency, error=error)


def test_decide_ok_takes_highest_priority():
    results = [_r("a", STATUS_SLOW, 400), _r("b", STATUS_OK, 200), _r("c", STATUS_OK, 100)]
    d = decide(results, ["a", "b", "c"])
    assert d.selected_id == "b"          # 优先级高于速度
    assert d.reason == REASON_OK


def test_decide_all_slow_picks_fastest():
    """全超线 → 不看出身,取延迟最低。"""
    results = [_r("a", STATUS_SLOW, 4000), _r("b", STATUS_SLOW, 3500), _r("c", STATUS_SLOW, 5000)]
    d = decide(results, ["a", "b", "c"])
    assert d.selected_id == "b"          # 3500ms 最快,虽然 b 优先级最低
    assert d.reason == REASON_FASTEST_SLOW


def test_decide_all_failed_returns_none():
    results = [_r("a", STATUS_ERROR), _r("b", STATUS_ERROR)]
    d = decide(results, ["a", "b"])
    assert d.selected_id is None
    assert d.reason == REASON_ALL_FAILED


def test_decide_mixed_slow_and_error():
    results = [_r("a", STATUS_ERROR), _r("b", STATUS_SLOW, 4200)]
    d = decide(results, ["a", "b"])
    assert d.selected_id == "b"


def test_decide_skipped_not_selected():
    results = [_r("a", STATUS_SKIPPED), _r("b", STATUS_SKIPPED)]
    d = decide(results, ["a", "b"])
    assert d.selected_id is None


def test_should_switch_force_align():
    d = _r("", STATUS_OK)
    from core.router import Decision
    decision = Decision(selected_id="b", reason=REASON_OK, selected_latency_ms=100)
    assert should_switch(decision, "a", STATUS_OK, force_align=True) is True   # 归位:手动切的也会掰回
    assert should_switch(decision, "b", STATUS_OK, force_align=True) is False  # 已是目标


def test_should_switch_without_force_align():
    from core.router import Decision
    decision = Decision(selected_id="b", reason=REASON_OK, selected_latency_ms=100)
    # 仅当前模型失败才切
    assert should_switch(decision, "a", STATUS_ERROR, force_align=False) is True
    assert should_switch(decision, "a", STATUS_OK, force_align=False) is False
    assert should_switch(decision, "a", STATUS_SLOW, force_align=False) is False
    assert should_switch(decision, "a", None, force_align=False) is False  # 当前不在链里不动
    assert should_switch(decision, "b", STATUS_ERROR, force_align=False) is False  # 已是目标


def test_should_switch_no_decision():
    from core.router import Decision
    decision = Decision(selected_id=None, reason=REASON_ALL_FAILED)
    assert should_switch(decision, "a", STATUS_ERROR, force_align=True) is False


def test_current_status_of():
    results = [_r("a", STATUS_OK), _r("b", STATUS_ERROR)]
    assert current_status_of(results, "a") == STATUS_OK
    assert current_status_of(results, "b") == STATUS_ERROR
    assert current_status_of(results, "c") is None
    assert current_status_of(results, None) is None


def test_notices():
    long_notice = build_long_unavailable_notice("dead_model", 24)
    assert "dead_model" in long_notice and "24" in long_notice
    assert "日志" not in long_notice  # 纯文案,发送与否由调用方决定


def test_extract_number():
    """命令参数容错:各种自然写法都能取出编号。"""
    assert extract_number("3") == 3
    assert extract_number(3) == 3
    assert extract_number("测3") == 3
    assert extract_number("测试 3") == 3
    assert extract_number("切换 3") == 3
    assert extract_number("第3个") == 3
    assert extract_number("测试") is None
    assert extract_number("状态") is None
    assert extract_number(None) is None
    assert extract_number("") is None
