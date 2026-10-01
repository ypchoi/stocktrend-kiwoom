import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Response, status

from src.collector import metadata_redis, run_forever
from src.config import SERVICE_NAME, settings

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s KST - %(name)s - %(levelname)s - %(message)s",
)
logging.Formatter.converter = lambda *args: datetime.now(timezone(timedelta(hours=9))).timetuple()


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(run_forever())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await metadata_redis.aclose()


app = FastAPI(title="Stocktrend Kiwoom collector", lifespan=lifespan)


@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/ready", include_in_schema=False)
async def ready():
    try:
        await metadata_redis.ping()
    except Exception:
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    return {"status": "ready"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.main:app", host="0.0.0.0", port=settings.kiwoom_port, log_level=settings.log_level
    )
