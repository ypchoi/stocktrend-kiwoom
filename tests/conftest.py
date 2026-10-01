"""테스트가 운영 인스턴스를 건드리지 못하게 막는다 (yfinance conftest와 같은 규칙).

unittest.sh만 STOCKTREND_UNITTEST를 세운다. 없으면 일회용 인스턴스 주소로 되돌린다.
"""
import os

if os.getenv("STOCKTREND_UNITTEST") != "1":
    os.environ["_POSTGRES_URL"] = os.getenv(
        "UNITTEST_POSTGRES_URL", "postgresql://stocktrend:unittest@localhost:5433/stocktrend"
    )
    for _var in ("METADATA_REDIS_URL", "EVENT_REDIS_URL"):
        os.environ[_var] = os.getenv("UNITTEST_REDIS_URL", "redis://localhost:6377")
