"""
비트코인 실시간 데이터 + 환율 + 날씨 캐시
5분마다 갱신. 프롬프트에 삽입.
"""

import asyncio
import logging
import random
import xml.etree.ElementTree as ET
from datetime import datetime

logger = logging.getLogger("BitcoinData")

_cache = {
    "price_usd": None,
    "price_krw": None,
    "usd_krw": None,
    "block_height": None,
    "fee_fast": None,
    "fee_half": None,
    "fee_hour": None,
    "hashrate": None,
    "difficulty": None,
    "updated": None,
}

# 날씨 캐시
_weather_cache = {}

# 뉴스 캐시
_news_cache = {"domestic": [], "international": []}

# 도시 좌표
CITIES = {
    "서울": (37.5665, 126.9780),
    "인천": (37.4563, 126.7052),
    "대전": (36.3504, 127.3845),
    "대구": (35.8714, 128.6014),
    "부산": (35.1796, 129.0756),
    "광주": (35.1595, 126.8526),
    "제주": (33.4996, 126.5312),
    "춘천": (37.8813, 127.7298),
}

# WMO 날씨 코드 → 한글
WMO_CODES = {
    0: "맑음", 1: "대체로 맑음", 2: "구름 조금", 3: "흐림",
    45: "안개", 48: "안개",
    51: "이슬비", 53: "이슬비", 55: "이슬비",
    61: "비", 63: "비", 65: "강한 비",
    71: "눈", 73: "눈", 75: "강한 눈",
    77: "싸락눈", 80: "소나기", 81: "소나기", 82: "강한 소나기",
    85: "눈보라", 86: "눈보라",
    95: "뇌우", 96: "뇌우+우박", 99: "뇌우+우박",
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

            # 한국 시세 (업비트)
            async with session.get("https://api.upbit.com/v1/ticker?markets=KRW-BTC", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data and len(data) > 0:
                        _cache["price_krw"] = data[0].get("trade_price")

            # 환율 (USD → KRW)
            async with session.get("https://open.er-api.com/v6/latest/USD", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    krw = data.get("rates", {}).get("KRW")
                    if krw:
                        _cache["usd_krw"] = krw

            # 해시레이트 + 난이도
            async with session.get("https://mempool.space/api/v1/mining/hashrate/1d", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("hashrates"):
                        _cache["hashrate"] = data["hashrates"][-1].get("avgHashrate")
                    if data.get("difficulty"):
                        _cache["difficulty"] = data["difficulty"][-1].get("difficulty")

            _cache["updated"] = datetime.now()
            logger.info(f"데이터 갱신: ${_cache['price_usd']} / ₩{_cache.get('price_krw', '?')} | $1=₩{_cache.get('usd_krw', '?')} | 블록 {_cache['block_height']}")

    except Exception as e:
        logger.warning(f"비트코인 데이터 갱신 실패: {e}")

    # 날씨 (별도 try — 비트코인 실패해도 날씨는 갱신)
    try:
        async with aiohttp.ClientSession() as session:
            lats = ",".join(str(c[0]) for c in CITIES.values())
            lons = ",".join(str(c[1]) for c in CITIES.values())
            url = (
                f"https://api.open-meteo.com/v1/forecast?"
                f"latitude={lats}&longitude={lons}"
                f"&current=temperature_2m,weather_code"
                f"&timezone=Asia/Seoul"
            )
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    cities = list(CITIES.keys())
                    # 복수 좌표 → 리스트, 단일 → dict
                    if isinstance(data, list):
                        items = data
                    else:
                        items = [data]
                    for i, city in enumerate(cities):
                        if i < len(items):
                            current = items[i].get("current", {})
                            temp = current.get("temperature_2m")
                            code = current.get("weather_code", 0)
                            _weather_cache[city] = {
                                "temp": temp,
                                "desc": WMO_CODES.get(code, "알 수 없음"),
                            }
                    logger.info(f"날씨 데이터 갱신: {len(_weather_cache)}개 도시")
    except Exception as e:
        logger.warning(f"날씨 데이터 갱신 실패: {e}")

    # 뉴스
    try:
        async with aiohttp.ClientSession() as session:
            for key, url in [
                ("domestic", "https://news.google.com/rss?hl=ko&gl=KR&ceid=KR:ko"),
                ("international", "https://news.google.com/rss?hl=en&gl=US&ceid=US:en"),
            ]:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        root = ET.fromstring(text)
                        titles = [item.find("title").text for item in root.findall(".//item") if item.find("title") is not None]
                        _news_cache[key] = random.sample(titles, min(5, len(titles))) if titles else []
            logger.info(f"뉴스 갱신: 국내 {len(_news_cache['domestic'])}건, 국제 {len(_news_cache['international'])}건")
    except Exception as e:
        logger.warning(f"뉴스 데이터 갱신 실패: {e}")


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
        price_line = f"가격: ${_cache['price_usd']:,.0f}"
        if _cache.get("price_krw"):
            price_line += f" (₩{_cache['price_krw']:,.0f})"
        lines.append(price_line)
    if _cache.get("usd_krw"):
        lines.append(f"환율: $1 = ₩{_cache['usd_krw']:,.0f}")
    if _cache.get("price_krw") and _cache.get("price_usd") and _cache.get("usd_krw"):
        fair_krw = _cache["price_usd"] * _cache["usd_krw"]
        kimchi = (_cache["price_krw"] / fair_krw - 1) * 100
        lines.append(f"김치 프리미엄: {kimchi:+.1f}%")

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

    block = "## 비트코인 현황\n" + "\n".join(lines)

    # 날씨
    if _weather_cache:
        weather_parts = [f"{city} {w['temp']:.0f}°C {w['desc']}" for city, w in _weather_cache.items() if w.get("temp") is not None]
        if weather_parts:
            block += "\n\n## 날씨\n" + " | ".join(weather_parts)

    return block


def get_news() -> dict:
    """뉴스 캐시 반환 (search용)"""
    return _news_cache


async def get_weather_for(city_name: str) -> str | None:
    """도시명으로 날씨 조회 (국내 캐시 → 없으면 geocoding → Open-Meteo)"""
    # 국내 캐시 확인
    if city_name in _weather_cache:
        w = _weather_cache[city_name]
        if w.get("temp") is not None:
            return f"{city_name} {w['temp']:.0f}°C {w['desc']}"

    # 국제: geocoding → 날씨 조회
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={city_name}&count=1&language=ko"
            async with session.get(geo_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return None
                geo = await resp.json()
                results = geo.get("results")
                if not results:
                    return None
                lat = results[0]["latitude"]
                lon = results[0]["longitude"]
                name = results[0].get("name", city_name)

            weather_url = (
                f"https://api.open-meteo.com/v1/forecast?"
                f"latitude={lat}&longitude={lon}"
                f"&current=temperature_2m,weather_code"
                f"&timezone=auto"
            )
            async with session.get(weather_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                current = data.get("current", {})
                temp = current.get("temperature_2m")
                code = current.get("weather_code", 0)
                if temp is not None:
                    return f"{name} {temp:.0f}°C {WMO_CODES.get(code, '알 수 없음')}"
    except Exception as e:
        logger.warning(f"날씨 조회 실패 ({city_name}): {e}")
    return None
