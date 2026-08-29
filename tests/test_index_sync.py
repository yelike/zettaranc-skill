"""指数同步到 DuckDB 的单元测试。"""

import os

import pytest

pytest.importorskip("duckdb")

import duckdb  # noqa: E402
import pandas as pd  # noqa: E402

from modules.index_sync import sync_indices_to_duckdb  # noqa: E402


class FakeHithink:
    """假的 hithink 数据源，返回少量指数日线。"""

    def get_index_daily(self, ts_code, start_date=None, end_date=None):
        return pd.DataFrame(
            [
                {
                    "ts_code": ts_code,
                    "trade_date": "20260101",
                    "open": 100.0,
                    "high": 102.0,
                    "low": 99.0,
                    "close": 101.0,
                    "vol": 1000000.0,
                    "amount": 101000000.0,
                },
                {
                    "ts_code": ts_code,
                    "trade_date": "20260102",
                    "open": 101.0,
                    "high": 103.0,
                    "low": 100.0,
                    "close": 102.0,
                    "vol": 1100000.0,
                    "amount": 112200000.0,
                },
            ]
        )


def test_sync_indices_to_duckdb(tmp_path, monkeypatch):
    db_path = str(tmp_path / "market.duckdb")
    con = duckdb.connect(db_path)
    con.execute(
        """
        CREATE TABLE raw_kline_daily (
            thscode VARCHAR, date DATE, open DOUBLE, high DOUBLE, low DOUBLE,
            "close" DOUBLE, volume DOUBLE, turnover DOUBLE, currency VARCHAR,
            "interval" VARCHAR, adjusted VARCHAR, source_batch_id VARCHAR,
            PRIMARY KEY(thscode, date)
        )
        """
    )
    con.execute(
        """
        CREATE TABLE _import_batches (
            batch_id VARCHAR PRIMARY KEY, "source" VARCHAR, kind VARCHAR,
            started_at TIMESTAMP, finished_at TIMESTAMP, row_count BIGINT, notes VARCHAR
        )
        """
    )
    con.close()

    result = sync_indices_to_duckdb(
        duckdb_path=db_path,
        index_codes=["000001.SH"],
        datasource=FakeHithink(),
    )
    assert result["total_rows"] == 2
    assert result["details"]["000001.SH"]["rows"] == 2

    con = duckdb.connect(db_path, read_only=True)
    rows = con.execute("SELECT thscode, COUNT(*) FROM raw_kline_daily GROUP BY thscode").fetchall()
    assert rows == [("000001.SH", 2)]
    con.close()
