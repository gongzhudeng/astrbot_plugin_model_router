"""回退感知测试。"""

from core.watcher import judge, should_skip_scheduled, should_trigger


def test_judge_fallback_detected():
    v = judge("deepseek-v4", "doubao-seed")
    assert v.fallback_detected is True
    assert "deepseek-v4" in v.reason and "doubao-seed" in v.reason


def test_judge_same_model_ok():
    v = judge("deepseek-v4", "deepseek-v4")
    assert v.fallback_detected is False


def test_judge_missing_actual_skips():
    """响应拿不到模型名(流式场景)→ 宁漏勿错。"""
    assert judge(None, "deepseek-v4").fallback_detected is False
    assert judge("", "deepseek-v4").fallback_detected is False


def test_judge_missing_current_skips():
    assert judge("deepseek-v4", None).fallback_detected is False
    assert judge("deepseek-v4", "").fallback_detected is False


def test_judge_whitespace_tolerant():
    assert judge(" deepseek-v4 ", "deepseek-v4").fallback_detected is False


def test_cooldown_first_trigger_passes():
    assert should_trigger(now=1000.0, last_trigger_ts=0.0, cooldown_seconds=300) is True


def test_cooldown_blocks_within_window():
    assert should_trigger(now=1100.0, last_trigger_ts=1000.0, cooldown_seconds=300) is False


def test_cooldown_passes_after_window():
    assert should_trigger(now=1300.0, last_trigger_ts=1000.0, cooldown_seconds=300) is True


def test_cooldown_disabled():
    assert should_trigger(now=1001.0, last_trigger_ts=1000.0, cooldown_seconds=0) is True


def test_skip_scheduled_no_chat_history():
    """从未聊过天 → 不跳过,照常检测。"""
    assert should_skip_scheduled(now=10000.0, last_chat_success_ts=0.0, window_seconds=1800) is False


def test_skip_scheduled_within_window():
    """距上次成功对话 10 分钟 < 30 分钟窗口 → 跳过。"""
    now = 10000.0
    last_chat = now - 10 * 60
    assert should_skip_scheduled(now, last_chat, 30 * 60) is True


def test_skip_scheduled_outside_window():
    """距上次成功对话 45 分钟 > 30 分钟窗口 → 不跳过。"""
    now = 10000.0
    last_chat = now - 45 * 60
    assert should_skip_scheduled(now, last_chat, 30 * 60) is False


def test_skip_scheduled_disabled():
    """窗口为 0(功能关闭)→ 永不跳过。"""
    now = 10000.0
    assert should_skip_scheduled(now, now - 60, 0) is False
