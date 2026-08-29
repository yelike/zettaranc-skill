"""市场择时权重 MarketTimingWeights 单元测试。

- 6 个综合分权重 + 2 个阈值 + 2 个强弱势 + 2 个涨跌停,所有 magic numbers 集中到一处
- 默认值与原 modules.market_timing 内置常量完全一致(0 行为变更)
- validate() 校验:strong > weak、涨跌停符号、权重和 ≈ 1.0
- compute_market_timing(weights=...) 可覆盖;None 用默认
"""

from __future__ import annotations

import pytest

from modules.dynamic_config import (
    DEFAULT_MARKET_TIMING_WEIGHTS,
    MarketTimingWeights,
)


def test_default_weights_match_original_magic_numbers():
    """默认权重必须与 modules.market_timing 原本的 magic numbers 完全一致。

    0 行为变更(向后兼容):任何调权重 = 显式传 weights=MarketTimingWeights(...)。
    """
    w = DEFAULT_MARKET_TIMING_WEIGHTS
    # 综合分权重
    assert w.weight_trend == 0.25
    assert w.weight_breadth == 0.20
    assert w.weight_moneyflow == 0.15
    assert w.weight_risk == 0.15
    assert w.weight_sentiment == 0.10
    assert w.weight_amv == 0.15
    # 状态分类阈值
    assert w.strong_threshold == 65.0
    assert w.weak_threshold == 40.0
    # 强弱势阈值
    assert w.strong_up_pct == 5.0
    assert w.strong_down_pct == -5.0
    # 涨跌停阈值(A 股硬规则 + 0.5% buffer)
    assert w.limit_up_pct == 9.5
    assert w.limit_down_pct == -9.5


def test_weights_sum_to_one():
    """6 个综合分权重之和应 ≈ 1.0,任何偏离 > 0.01 都是配错。"""
    DEFAULT_MARKET_TIMING_WEIGHTS.validate()
    assert abs(DEFAULT_MARKET_TIMING_WEIGHTS.weights_sum() - 1.0) < 0.01


def test_validate_rejects_inverted_thresholds():
    """strong_threshold 必须 > weak_threshold。"""
    with pytest.raises(ValueError, match="strong_threshold"):
        MarketTimingWeights(strong_threshold=40.0, weak_threshold=65.0).validate()


def test_validate_rejects_nonpositive_strong_up_pct():
    with pytest.raises(ValueError, match="strong_up_pct"):
        MarketTimingWeights(strong_up_pct=0.0).validate()


def test_validate_rejects_positive_strong_down_pct():
    with pytest.raises(ValueError, match="strong_down_pct"):
        MarketTimingWeights(strong_down_pct=5.0).validate()


def test_validate_rejects_invalid_limit_thresholds():
    with pytest.raises(ValueError, match="limit_up_pct"):
        MarketTimingWeights(limit_up_pct=0.0).validate()
    with pytest.raises(ValueError, match="limit_down_pct"):
        MarketTimingWeights(limit_down_pct=0.0).validate()


def test_validate_rejects_weights_sum_deviation():
    """权重之和偏离 1.0 超过 0.01 应拒绝,防误配。"""
    bad = MarketTimingWeights(
        weight_trend=0.5,
        weight_breadth=0.5,
        weight_moneyflow=0.0,  # 总和 1.0 但不均衡
        weight_risk=0.0,
        weight_sentiment=0.0,
        weight_amv=0.0,
    )
    # 总和 1.0 应该通过
    bad.validate()

    really_bad = MarketTimingWeights(
        weight_trend=0.5,
        weight_breadth=0.5,
        weight_moneyflow=0.5,  # 总和 1.5
        weight_risk=0.0,
        weight_sentiment=0.0,
        weight_amv=0.0,
    )
    with pytest.raises(ValueError, match="权重之和应为 1.0"):
        really_bad.validate()


def test_market_timing_classify_regime_uses_weights():
    """_classify_regime 必须读 weights.strong_threshold / weak_threshold。"""
    from modules.market_timing import _classify_regime

    # 默认:65 / 40
    assert _classify_regime(70.0) == "强势"
    assert _classify_regime(50.0) == "震荡"
    assert _classify_regime(30.0) == "弱势"

    # 自定义阈值:80 / 20
    custom = MarketTimingWeights(strong_threshold=80.0, weak_threshold=20.0)
    assert _classify_regime(70.0, weights=custom) == "震荡"  # 70 < 80 → 震荡
    assert _classify_regime(85.0, weights=custom) == "强势"
    assert _classify_regime(15.0, weights=custom) == "弱势"


def test_market_timing_load_snapshot_sqlite_uses_weights(tmp_path, monkeypatch):
    """SQLite snapshot 必须用 weights.limit_up_pct / strong_up_pct 等阈值。"""
    import sqlite3

    from modules import market_timing
    from modules import database

    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    database.init_database()

    trade_date = "20260821"
    rows = [
        ("600000.SH", trade_date, 10, 11, 9, 11, 1e7, 1.1e8, 10.0, 1, 0),  # +10% (default 9.5 limit)
        ("600001.SH", trade_date, 10, 10.4, 9.6, 10.3, 1e7, 1.03e8, 3.0, 0, 0),  # +3% 普通涨
        ("600002.SH", trade_date, 10, 9.5, 9, 9.4, 1e7, 9.4e7, -6.0, 0, 0),  # -6% 普通跌
    ]
    con = sqlite3.connect(str(tmp_path / "test.db"))
    con.executemany(
        "INSERT INTO daily_kline (ts_code, trade_date, open, high, low, close, vol, amount, pct_chg, is_limit_up, is_limit_down) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    con.commit()
    con.close()

    # 默认 9.5% 阈值:+10% 算 limit_up,同时也 >= 5% 算 strong_up(两个独立计数器)
    snap_default = market_timing._load_market_snapshot_sqlite(trade_date)
    assert snap_default["limit_up"] == 1
    assert snap_default["strong_up"] == 1  # +10% 同样 >= 5% strong 阈值

    # 自定义 limit_up=11%:+10% 不再算 limit_up;strong_up 仍按 5% 算(与 limit 阈值独立)
    custom = MarketTimingWeights(limit_up_pct=11.0)
    snap_custom = market_timing._load_market_snapshot_sqlite(trade_date, weights=custom)
    assert snap_custom["limit_up"] == 0
    # strong_up 仍按 5% 阈值算:1(因为 +10% >= 5%)
    assert snap_custom["strong_up"] == 1
    # 6% 跌幅 <= -5% strong 阈值,所以 strong_down=1
    assert snap_custom["strong_down"] == 1


def test_market_timing_no_magic_numbers_left():
    """market_timing.py 中综合分公式 + 状态阈值 + 涨跌停不能再有硬编码 magic numbers。"""
    from pathlib import Path

    src = Path("modules/market_timing.py").read_text(encoding="utf-8")

    # 综合分公式应是 weights.weight_x 不是 0.25/0.20/0.15
    assert "trend_score * 0.25" not in src, "综合分 trend 权重应走 weights.weight_trend"
    assert "b_score * 0.20" not in src, "综合分 breadth 权重应走 weights.weight_breadth"
    assert "m_score * 0.15" not in src, "综合分 moneyflow 权重应走 weights.weight_moneyflow"
    assert "s_score * 0.10" not in src, "综合分 sentiment 权重应走 weights.weight_sentiment"

    # 状态阈值应是 weights.strong_threshold / weak_threshold
    assert "composite >= 65.0" not in src, "strong 阈值应走 weights.strong_threshold"
    assert "composite <= 40.0" not in src, "weak 阈值应走 weights.weak_threshold"

    # 涨跌停 SQL 不应再硬编码 9.5
    assert "pct_chg >= 9.5" not in src, "limit_up SQL 阈值应走 weights.limit_up_pct"
    assert "pct_chg <= -9.5" not in src, "limit_down SQL 阈值应走 weights.limit_down_pct"
    assert ">= 9.5 THEN" not in src, "DuckDB limit_up SQL 阈值应走 weights.limit_up_pct"
    assert "<= -9.5 THEN" not in src, "DuckDB limit_down SQL 阈值应走 weights.limit_down_pct"

    # 强弱势 SQL 也不应硬编码 5
    assert "pct_chg >= 5 THEN" not in src, "SQLite strong_up 应走 weights.strong_up_pct"
    assert "pct_chg <= -5 THEN" not in src, "SQLite strong_down 应走 weights.strong_down_pct"
    assert ">= 5 THEN" not in src or "strong_threshold" in src, "强弱势阈值应走 weights"


def test_weights_is_frozen_dataclass():
    """MarketTimingWeights 必须是 frozen,避免运行时意外修改。"""
    from dataclasses import FrozenInstanceError

    w = MarketTimingWeights()
    with pytest.raises(FrozenInstanceError):
        w.weight_trend = 0.5  # type: ignore[misc]
