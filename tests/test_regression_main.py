"""源码级防回归测试:锁死定时循环历史上发生过的死循环写法。

背景(2026-10-15 根因):jitter 曾实现为"到点后再随机推后"(循环内 continue),
导致每次到点都被再次推后、永远跑不到执行——定时检测静默失效3天。
正确写法:抖动在"设定下一轮时间"时一次性加入(_next_delay_seconds)。
"""

from pathlib import Path

MAIN_SRC = Path(__file__).resolve().parent.parent / "main.py"


def test_jitter_not_postponed_in_loop():
    """循环内不允许再出现"到点后随机推后 next_run_at"的写法。"""
    src = MAIN_SRC.read_text(encoding="utf-8")
    # 旧的死循环写法(严禁回归)
    assert "random.randint(1, jitter * 60)" not in src


def test_next_delay_helper_exists():
    """修复写法必须存在:设定下一轮时间时一次性附加抖动。"""
    src = MAIN_SRC.read_text(encoding="utf-8")
    assert "def _next_delay_seconds(" in src
    assert "random.randint(0, jitter * 60)" in src


def test_loop_progresses_to_run():
    """_auto_loop 里 run_probe_round 必须可达:检查到点后的主路径存在。"""
    src = MAIN_SRC.read_text(encoding="utf-8")
    assert 'run_probe_round(trigger="auto")' in src


def test_numeric_switch_shortcut_exists():
    """回归:/模型路由 <编号> 数字直切必须存在(与 /provider <n> 同款手感)。"""
    src = MAIN_SRC.read_text(encoding="utf-8")
    assert "sub_norm.isdigit()" in src
    assert "_switch_by_index(event, sub_norm)" in src


def test_skip_requires_chain_head():
    """回归:定时检测跳过必须以"当前模型就是链首"为前提。

    当前不是链首时,聊天只验证了当前模型,首位恢复情况未知,必须照常检测。
    """
    src = MAIN_SRC.read_text(encoding="utf-8")
    assert "current_id == order_ids[0]" in src


def test_command_arg_accepts_int():
    """回归:AstrBot 会把纯数字参数解析成 int,命令处理必须先转字符串。

    历史 bug:`/模型路由 16` 传入 int 16,`(sub or "").strip()` 抛
    AttributeError: 'int' object has no attribute 'strip'。
    """
    src = MAIN_SRC.read_text(encoding="utf-8")
    assert "str(sub if sub is not None else \"\")" in src
    assert "sub: str | int | None" in src


def test_single_probe_command_exists():
    """回归:/模型路由 测 <编号> 单独检测必须存在,且不参与切换决策。"""
    src = MAIN_SRC.read_text(encoding="utf-8")
    assert "async def _probe_single_by_index(" in src
    assert 'sub_norm.startswith("测")' in src
    assert "_probe_single_by_index(event, number)" in src


def test_loose_number_parsing():
    """回归:"测试 3" 这类自然写法必须被识别为单测,而不是整链检测。"""
    src = MAIN_SRC.read_text(encoding="utf-8")
    assert "router_core.extract_number(" in src
    assert "if number is not None:" in src
    assert "async def _run_full_round(" in src


def test_standalone_commands_exist():
    """回归:独立命令必须存在(/模型测试 3、/模型列表、/模型状态、/模型切换 3)。"""
    src = MAIN_SRC.read_text(encoding="utf-8")
    for name in ("模型测试", "模型列表", "模型状态", "模型切换"):
        assert f'@filter.command("{name}"' in src


def test_no_chat_notifications():
    """回归:路由全程对聊天无感——不得调用 send_message,只写日志。"""
    src = MAIN_SRC.read_text(encoding="utf-8")
    assert "send_message(" not in src
    assert "notify_mode" not in src
    assert "_send_text(" not in src
