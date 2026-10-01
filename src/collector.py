"""키움 국내 일봉(OHLCVA) 수집 사이클.

청크 격자는 다른 수집기와 같다 (`_DATA_EPOCH` 원점, `BACKFILL_START_DATE`부터, 최신 청크 먼저).
아직 DB에 쓰지 않는다. 받은 값을 KIS가 쓴 `daily_prices_adj`와 대조해 로그로만 남긴다.
쓰지 않으므로 완료 비트도 남기지 않고, 매 사이클 전 구간을 다시 받는다.
"""
import asyncio
import logging
import math
import time
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

import redis.asyncio as redis
from stocktrend_core import RateLimitedApiQueue
from stocktrend_core.constants import BACKFILL_CHUNK_DAYS, BACKFILL_START_DATE
from stocktrend_core.events import EventBus
from stocktrend_core.services import collector_metadata
from stocktrend_core.services.market_sync import MarketSync
from stocktrend_core.services.price_store import DOMESTIC_MARKETS, PriceStore

from src.config import settings
from src.kiwoom_client import KiwoomClient

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
COMPARE_FIELDS = ("o", "h", "l", "c", "v")
MISMATCH_LOG_LIMIT = 20
EVENT_LISTENER_RETRY_SECS = 5

metadata_redis = redis.from_url(settings.metadata_redis_url)
# source 없이 만들면 쓰기가 PermissionError로 막힌다. 대조 전용이다.
price_store = PriceStore()
api_queue = RateLimitedApiQueue(settings.kiwoom_interval_secs, workers=settings.kiwoom_api_workers)
market_sync = MarketSync()


def target_markets(markets: Dict[str, Dict[str, Any]], requested: set[str]) -> List[str]:
    return sorted(
        m for m, conf in markets.items()
        if m in DOMESTIC_MARKETS and m in requested
        and conf.get("is_price_support") and not conf.get("is_index")
    )


def compare_rows(
    kiwoom_rows: List[Dict[str, Any]], db_rows: List[Dict[str, Any]], stats: Counter,
    amount_scale: Counter,
) -> List[str]:
    """한 종목·청크의 대조 결과를 stats에 더하고 불일치 날짜를 돌려준다.

    거래대금은 단위가 확인되지 않아 불일치로 세지 않는다. DB/키움 비율의 자릿수만 모은다.
    """
    db_by_date = {r["date"]: r for r in db_rows}
    kiwoom_dates = {r["date"] for r in kiwoom_rows}
    stats["missing_in_kiwoom"] += len(db_by_date.keys() - kiwoom_dates)
    mismatched = []
    for row in kiwoom_rows:
        stats["rows"] += 1
        stored = db_by_date.get(row["date"])
        if stored is None:
            stats["missing_in_db"] += 1
            continue
        if any(
            row[f] is not None and stored.get(f) is not None
            and not math.isclose(float(stored[f]), row[f], rel_tol=1e-9)
            for f in COMPARE_FIELDS
        ):
            stats["mismatched"] += 1
            mismatched.append(row["date"])
        else:
            stats["matched"] += 1
        if row["a"] and stored.get("a"):
            amount_scale[round(math.log10(float(stored["a"]) / row["a"]))] += 1
    return mismatched


async def compare_chunk(
    client: KiwoomClient, market: str, ticker: str, chunk: Dict[str, Any], today: str,
    stats: Counter, amount_scale: Counter, samples: List[str],
) -> None:
    # 오늘 봉은 장중 값이라 DB와 어긋나는 게 정상이다. 마감된 날만 대조한다.
    end = min(chunk["end_date"], today)
    try:
        rows = await api_queue.submit(client.fetch_daily, ticker, end)
        stats["calls_ok"] += 1
    except Exception as e:
        stats["calls_failed"] += 1
        logger.warning(f"[{market}] {ticker} chunk #{chunk['chunk_number']} failed: {e}")
        return
    rows = [r for r in rows if chunk["start_date"] <= r["date"] <= end and r["date"] < today]
    db_rows = await price_store.load_daily_prices_adj(
        market, ticker, start=chunk["start_date"], end=end
    )
    db_rows = [r for r in db_rows if r["date"] < today]
    for day in compare_rows(rows, db_rows, stats, amount_scale):
        if len(samples) < MISMATCH_LOG_LIMIT:
            kiwoom = next(r for r in rows if r["date"] == day)
            stored = next(r for r in db_rows if r["date"] == day)
            samples.append(
                f"{ticker} {day} kiwoom={[kiwoom[f] for f in COMPARE_FIELDS]} "
                f"db={[stored.get(f) for f in COMPARE_FIELDS]}"
            )


async def run_cycle(client: KiwoomClient, requested: set[str]) -> None:
    markets = target_markets(await collector_metadata.fetch_markets(metadata_redis), requested)
    if not markets:
        logger.info(f"No Kiwoom target markets in {sorted(requested)}")
        return
    today = datetime.now(KST).strftime("%Y%m%d")
    chunk_ranges = collector_metadata.calculate_chunk_ranges(
        BACKFILL_START_DATE, today, BACKFILL_CHUNK_DAYS
    )
    tickers = {m: sorted(await collector_metadata.get_tickers_for_market(metadata_redis, m))
               for m in markets}
    logger.info(
        f"Kiwoom cycle: {markets} ({sum(map(len, tickers.values()))} tickers), "
        f"{len(chunk_ranges)} chunks {BACKFILL_START_DATE}~{today}"
    )

    started = time.monotonic()
    stats: Dict[str, Counter] = defaultdict(Counter)
    amount_scale: Dict[str, Counter] = defaultdict(Counter)
    samples: Dict[str, List[str]] = defaultdict(list)
    for chunk in chunk_ranges:
        await asyncio.gather(*(
            compare_chunk(client, m, t, chunk, today, stats[m], amount_scale[m], samples[m])
            for m in markets for t in tickers[m]
        ))
        logger.info(f"Chunk #{chunk['chunk_number']} {chunk['start_date']}~{chunk['end_date']} "
                    f"done: {dict(stats)}")

    for m in markets:
        # 비율 자릿수 6이면 키움 거래대금은 백만원 단위다.
        logger.info(f"[{m}] compare result: {dict(stats[m])} "
                    f"amount db/kiwoom log10: {dict(amount_scale[m])}")
        for line in samples[m]:
            logger.info(f"[{m}] mismatch {line}")
    logger.info(f"Kiwoom cycle finished in {time.monotonic() - started:.0f}s")


async def event_listener() -> None:
    bus = EventBus()
    # 구독이 끊기면 트리거를 영영 못 받는다. 재구독한다.
    while True:
        try:
            async for msg in bus.subscribe():
                if msg.sender == "stocktrend-manager" and msg.event_type == "trigger_kiwoom":
                    logger.info(f"[Event] trigger_kiwoom: {msg.payload.get('markets')}")
                    market_sync.trigger(msg.payload.get("markets"))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[Event] listener error: {e}")
        await asyncio.sleep(EVENT_LISTENER_RETRY_SECS)


async def run_forever() -> None:
    client = KiwoomClient()
    listener = asyncio.create_task(event_listener())
    api_queue.start()
    try:
        # 재시작 전 목록 갱신은 list의 ready 키로 복구한다.
        await market_sync.restore(metadata_redis)
        while True:
            try:
                await run_cycle(client, market_sync.take())
            except Exception as e:
                logger.error(f"Kiwoom cycle failed: {e}", exc_info=True)
                await asyncio.sleep(settings.sync_retry_delay_mins * 60)
                continue
            await market_sync.event.wait()
    finally:
        listener.cancel()
        api_queue.stop()
        await client.close()
