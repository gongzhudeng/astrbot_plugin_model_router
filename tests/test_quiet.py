"""免检测时段测试。"""

from datetime import datetime

from core.quiet import in_quiet, parse_hhmm, quiet_end_datetime, quiet_window


def test_parse_hhmm():
    assert parse_hhmm("01:26") == (1, 26)
    assert parse_hhmm("07:00") == (7, 0)
    assert parse_hhmm("23:59") == (23, 59)
    assert parse_hhmm("24:00") is None
    assert parse_hhmm("7:0") == (7, 0)
    assert parse_hhmm("abc") is None
    assert parse_hhmm("") is None
    assert parse_hhmm("7") is None


def test_quiet_window_from_config():
    cfg = {"quiet_enabled": True, "quiet_start": "01:26", "quiet_end": "07:00"}
    assert quiet_window(cfg) == ((1, 26), (7, 0))
    assert quiet_window({**cfg, "quiet_enabled": False}) is None
    assert quiet_window({**cfg, "quiet_start": "xx"}) is None
    assert quiet_window({**cfg, "quiet_start": "07:00"}) is None  # start==end 视为无效


def test_in_quiet_same_day():
    window = ((1, 26), (7, 0))
    assert in_quiet(datetime(2026, 9, 27, 3, 0), window) is True
    assert in_quiet(datetime(2026, 9, 27, 1, 26), window) is True   # 起点含
    assert in_quiet(datetime(2026, 9, 27, 7, 0), window) is False   # 终点不含
    assert in_quiet(datetime(2026, 9, 27, 12, 0), window) is False


def test_in_quiet_cross_midnight():
    window = ((23, 0), (7, 0))
    assert in_quiet(datetime(2026, 9, 27, 23, 30), window) is True
    assert in_quiet(datetime(2026, 9, 27, 1, 0), window) is True
    assert in_quiet(datetime(2026, 9, 27, 6, 59), window) is True
    assert in_quiet(datetime(2026, 9, 27, 12, 0), window) is False
    assert in_quiet(datetime(2026, 9, 27, 22, 59), window) is False


def test_quiet_end_datetime():
    window = ((1, 26), (7, 0))
    now = datetime(2026, 9, 27, 3, 0)
    assert quiet_end_datetime(now, window) == datetime(2026, 9, 27, 7, 0)
    now_after = datetime(2026, 9, 27, 8, 0)  # 已出时段(理论上不会用它算)
    assert quiet_end_datetime(now_after, window) == datetime(2026, 9, 28, 7, 0)
