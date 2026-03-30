"""
비트코인 실시간 데이터 캐시 (mempool.space API)
5분마다 갱신. 프롬프트에 삽입.
"""

import asyncio
import logging
from datetime import datetime

logger = logging.getLogger("BitcoinData")

# 캐시
_cache = {
    "price_usd": None,
    "block_height": None,
    "fee_fast": None,
    "fee_half": None,
    "fee_hour": None,
    "updated": None,
}


async def _fetch():
    """mempool.space API에서 데이터 가져오기"""
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            # 블록 높이
            async with session.get("https://mempool.space/api/blocks/tip/height", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    _cache["block_height"] = int(await resp.text())

            # 추천 수수료
            async with session.get("https://mempool.space/api/v1/fees/recommended", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    fees = await resp.json()
                    _cache["fee_fast"] = fees.get("fastestFee")
                    _cache["fee_half"] = fees.get("halfHourFee")
                    _cache["fee_hour"] = fees.get("hourFee")

            # 가격 (USD)
            async with session.get("https://mempool.space/api/v1/prices", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    prices = await resp.json()
                    _cache["price_usd"] = prices.get("USD")

            _cache["updated"] = datetime.now()
            logger.info(f"비트코인 데이터 갱신: ${_cache['price_usd']} | 블록 {_cache['block_height']} | 수수료 {_cache['fee_fast']}/{_cache['fee_half']}/{_cache['fee_hour']} sat/vB")

    except Exception as e:
        logger.warning(f"비트코인 데이터 갱신 실패: {e}")


async def start_background_update(interval: int = 300):
    """5분마다 갱신하는 백그라운드 태스크"""
    await _fetch()  # 시작 시 즉시 1회
    while True:
        await asyncio.sleep(interval)
        await _fetch()


def get_prompt_block() -> str:
    """프롬프트용 텍스트 반환"""
    if not _cache["updated"]:
        return ""
    parts = []
    if _cache["price_usd"]:
        parts.append(f"가격: ${_cache['price_usd']:,.0f}")
    if _cache["block_height"]:
        parts.append(f"블록: {_cache['block_height']:,}")
        # 다음 반감기 계산 (210,000 블록마다)
        next_halving = (((_cache["block_height"] // 210000) + 1) * 210000)
        remaining = next_halving - _cache["block_height"]
        parts.append(f"다음 반감기까지 {remaining:,}블록")
    if _cache.get("fee_fast"):
        parts.append(f"수수료: 빠름 {_cache['fee_fast']} / 보통 {_cache['fee_half']} / 느림 {_cache['fee_hour']} sat/vB")
    if not parts:
        return ""
    return "## 비트코인 현황\n" + " | ".join(parts)
