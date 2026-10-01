from collections import Counter

from stocktrend_core.constants import BACKFILL_START_DATE

from src import collector


def _row(day, c, a=None):
    return {"date": day, "o": c, "h": c, "l": c, "c": c, "v": 10.0, "a": a}


def test_target_markets_domestic_price_only_and_requested():
    markets = {
        "KOSPI": {"is_price_support": True},
        "KOSDAQ": {"is_price_support": True},
        "KONEX": {"is_price_support": False},
        "IDX_DOMESTIC": {"is_price_support": True, "is_index": True},
        "NASDAQ": {"is_price_support": True},
    }
    requested = {"KOSPI", "KONEX", "IDX_DOMESTIC", "NASDAQ"}
    assert collector.target_markets(markets, requested) == ["KOSPI"]


def test_compare_rows_counts_each_outcome():
    stats = Counter()
    kiwoom = [_row("20250102", 100.0, a=5_000_000.0), _row("20250103", 100.0),
              _row("20250106", 100.0)]
    db = [
        {"date": "20250102", "o": 100, "h": 100, "l": 100, "c": 100, "v": 10, "a": 5_123_456},
        {"date": "20250103", "o": 100, "h": 100, "l": 100, "c": 101, "v": 10},
        {"date": "20250107", "c": 100},
    ]
    mismatched = collector.compare_rows(kiwoom, db, stats)
    assert mismatched == ["20250103"]
    assert stats == Counter(rows=3, matched=1, mismatched=1, missing_in_db=1,
                            missing_in_kiwoom=1)


def test_compare_rows_amount_beyond_million_won_is_mismatch():
    stats = Counter()
    kiwoom = [_row("20250102", 100.0, a=5_000_000.0)]
    db = [{"date": "20250102", "o": 100, "h": 100, "l": 100, "c": 100, "v": 10, "a": 6_000_001}]
    assert collector.compare_rows(kiwoom, db, stats) == ["20250102"]


def test_compare_rows_ignores_null_fields():
    stats = Counter()
    kiwoom = [{**_row("20250102", 100.0), "v": None}]
    db = [{"date": "20250102", "o": 100, "h": 100, "l": 100, "c": 100, "v": 99}]
    assert collector.compare_rows(kiwoom, db, stats) == []
    assert stats["matched"] == 1


class FakeClient:
    def __init__(self, rows=None, error=None):
        self.rows, self.error, self.calls = rows or [], error, []

    async def fetch_daily(self, ticker, base_date):
        self.calls.append((ticker, base_date))
        if self.error:
            raise self.error
        return self.rows


async def test_compare_chunk_clips_to_chunk_and_skips_today(monkeypatch):
    loaded = []

    async def load(market, ticker, *, start, end):
        loaded.append((market, ticker, start, end))
        return [{"date": "20250105", "o": 1, "h": 1, "l": 1, "c": 1, "v": 10},
                {"date": "20250110", "c": 999}]

    monkeypatch.setattr(collector.price_store, "load_daily_prices_adj", load)
    client = FakeClient([_row("20241231", 1.0), _row("20250105", 1.0), _row("20250110", 2.0)])
    chunk = {"chunk_number": 91, "start_date": "20250101", "end_date": "20250410"}
    stats, samples = Counter(), []

    await collector.compare_chunk(client, "KOSPI", "005930", chunk, "20250110",
                                  stats, samples)

    # 청크 끝이 오늘보다 뒤면 오늘을 기준일로 부른다
    assert client.calls == [("005930", "20250110")]
    assert loaded == [("KOSPI", "005930", "20250101", "20250110")]
    assert stats == Counter(calls_ok=1, rows=1, matched=1)
    assert samples == []


async def test_compare_chunk_counts_failure_without_db_read(monkeypatch):
    async def load(*args, **kwargs):
        raise AssertionError("DB must not be read after a failed call")

    monkeypatch.setattr(collector.price_store, "load_daily_prices_adj", load)
    stats = Counter()
    chunk = {"chunk_number": 91, "start_date": "20250101", "end_date": "20250410"}
    await collector.compare_chunk(FakeClient(error=RuntimeError("boom")), "KOSPI", "005930",
                                  chunk, "20250501", stats, [])
    assert stats == Counter(calls_failed=1)


async def test_run_cycle_walks_chunks_latest_first_down_to_backfill_start(monkeypatch):
    async def fetch_markets(_redis):
        return {"KOSPI": {"is_price_support": True}, "NASDAQ": {"is_price_support": True}}

    async def tickers(_redis, market):
        return {"005930": {}, "000660": {}}

    seen = []

    async def compare_chunk(client, market, ticker, chunk, today, *args):
        seen.append((market, ticker, chunk["chunk_number"], chunk["start_date"]))

    monkeypatch.setattr(collector.collector_metadata, "fetch_markets", fetch_markets)
    monkeypatch.setattr(collector.collector_metadata, "get_tickers_for_market", tickers)
    monkeypatch.setattr(collector, "compare_chunk", compare_chunk)

    await collector.run_cycle(FakeClient(), {"KOSPI", "NASDAQ"})

    assert {m for m, *_ in seen} == {"KOSPI"}
    numbers = [n for _, _, n, _ in seen]
    assert numbers == sorted(numbers, reverse=True)
    assert seen[-1][3] == BACKFILL_START_DATE
    assert len(seen) == 2 * len(set(numbers))


def test_price_store_is_read_only():
    import pytest

    with pytest.raises(PermissionError):
        collector.price_store._require_writer()
