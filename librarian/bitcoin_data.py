"""
비트코인 실시간 데이터 캐시 (mempool.space API)
5분마다 갱신. 프롬프트에 삽입.
"""

import asyncio
import logging
from datetime import datetime

logger = logging.getLogger("BitcoinData")

_cache = {
    "price_usd": None,
    "block_height": None,
    "fee_fast": None,
    "fee_half": None,
    "fee_hour": None,
    "hashrate": None,
    "difficulty": None,
    "updated": None,
}

# 반감기 스케줄 (블록 보상)
HALVING_SCHEDULE = {
    0: 50,        # 0 ~ 209,999
    210000: 25,   # 210,000 ~ 419,999
    420000: 12.5, # 420,000 ~ 629,999
    630000: 6.25, # 630,000 ~ 839,999
    840000: 3.125,# 840,000 ~ 1,049,999
}


def _get_current_reward(height):
    """현재 블록 보상"""
    era = height // 210000
    return 50 / (2 ** era)


def _get_supply(height):
    """현재까지 채굴된 총 비트코인"""
    supply = 0
    remaining = height
    reward = 50
    while remaining > 0:
        blocks = min(remaining, 210000)
        supply += blocks * reward
        remaining -= blocks
        reward /= 2
    return supply


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

            # 해시레이트 + 난이도
            async with session.get("https://mempool.space/api/v1/mining/hashrate/1d", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("hashrates"):
                        _cache["hashrate"] = data["hashrates"][-1].get("avgHashrate")
                    if data.get("difficulty"):
                        _cache["difficulty"] = data["difficulty"][-1].get("difficulty")

            _cache["updated"] = datetime.now()
            logger.info(f"비트코인 데이터 갱신: ${_cache['price_usd']} | 블록 {_cache['block_height']}")

    except Exception as e:
        logger.warning(f"비트코인 데이터 갱신 실패: {e}")


async def start_background_update(interval: int = 300):
    """5분마다 갱신하는 백그라운드 태스크"""
    await _fetch()
    while True:
        await asyncio.sleep(interval)
        await _fetch()


def get_prompt_block() -> str:
    """프롬프트용 텍스트 반환"""
    if not _cache["updated"]:
        return ""

    height = _cache["block_height"]
    lines = []

    # 가격
    if _cache["price_usd"]:
        lines.append(f"가격: ${_cache['price_usd']:,.0f}")

    # 블록
    if height:
        lines.append(f"현재 블록: {height:,}")

        # 현재 보상
        reward = _get_current_reward(height)
        lines.append(f"블록 보상: {reward} BTC")

        # 채굴 현황
        supply = _get_supply(height)
        lines.append(f"채굴된 비트코인: {supply:,.2f} / 21,000,000 BTC ({supply/21000000*100:.1f}%)")

        # 다음 반감기
        next_halving = ((height // 210000) + 1) * 210000
        remaining = next_halving - height
        days_approx = remaining * 10 / 60 / 24  # 블록당 ~10분
        lines.append(f"다음 반감기: {remaining:,}블록 남음 (약 {days_approx:.0f}일)")

        # 마지막 비트코인
        lines.append(f"마지막 비트코인 채굴: 약 2140년 예정")

    # 수수료
    if _cache.get("fee_fast"):
        lines.append(f"수수료: 빠름 {_cache['fee_fast']} / 보통 {_cache['fee_half']} / 느림 {_cache['fee_hour']} sat/vB")

    # 해시레이트
    if _cache.get("hashrate"):
        hr = _cache["hashrate"]
        if hr > 1e18:
            lines.append(f"해시레이트: {hr/1e18:.1f} EH/s")
        elif hr > 1e15:
            lines.append(f"해시레이트: {hr/1e15:.1f} PH/s")

    if not lines:
        return ""
    return "## 비트코인 현황\n" + "\n".join(lines)
