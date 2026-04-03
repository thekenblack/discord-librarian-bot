"""
Blink API 래퍼 (GraphQL) — 충전 전용 (인보이스 생성 + 결제 확인)
"""
import aiohttp
import logging
from config import BLINK_API_KEY, BLINK_API_URL

logger = logging.getLogger("Lightning")

Q_WALLET = "query { me { defaultAccount { wallets { id walletCurrency balance } } } }"

Q_CREATE_INVOICE = """
mutation LnInvoiceCreate($input: LnInvoiceCreateInput!) {
  lnInvoiceCreate(input: $input) {
    invoice { paymentRequest paymentHash }
    errors { message }
  }
}
"""

Q_CHECK_BY_HASH = """
query LnInvoicePaymentStatusByHash($input: LnInvoicePaymentStatusByHashInput!) {
  lnInvoicePaymentStatusByHash(input: $input) {
    status
    errors { message }
  }
}
"""

Q_TRANSACTIONS = """
query GetTransactions($after: String) {
  me {
    defaultAccount {
      transactions(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        edges {
          node {
            status
            direction
            initiationVia {
              ... on InitiationViaLn {
                paymentHash
              }
            }
          }
        }
      }
    }
  }
}
"""


class BlinkLightningManager:

    def __init__(self):
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"X-API-KEY": BLINK_API_KEY, "Content-Type": "application/json"}
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
            import asyncio
            await asyncio.sleep(0.25)

    async def _gql(self, query: str, variables: dict = None) -> dict:
        payload = {"query": query}
        if variables:
            payload["variables"] = variables
        session = await self._get_session()
        async with session.post(BLINK_API_URL, json=payload) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"Blink API {resp.status}: {text}")
            data = await resp.json()
        if "errors" in data:
            raise RuntimeError(f"GraphQL 오류: {data['errors']}")
        return data["data"]

    async def get_btc_wallet_id(self) -> str:
        data = await self._gql(Q_WALLET)
        for w in data["me"]["defaultAccount"]["wallets"]:
            if w["walletCurrency"] == "BTC":
                return w["id"]
        raise RuntimeError("BTC 지갑을 찾을 수 없습니다.")

    async def create_invoice(self, amount_sat: int, memo: str, expiry: int = 3600) -> dict:
        wallet_id = await self.get_btc_wallet_id()
        data = await self._gql(Q_CREATE_INVOICE, {
            "input": {
                "walletId": wallet_id,
                "amount": amount_sat,
                "memo": memo,
                "expiresIn": max(1, expiry // 60),
            }
        })
        result = data["lnInvoiceCreate"]
        if result.get("errors"):
            raise RuntimeError(f"인보이스 생성 실패: {result['errors']}")
        return {
            "payment_hash": result["invoice"]["paymentHash"],
            "bolt11": result["invoice"]["paymentRequest"],
        }

    async def check_invoice(self, payment_hash: str) -> bool:
        try:
            data = await self._gql(Q_CHECK_BY_HASH, {"input": {"paymentHash": payment_hash}})
            return data["lnInvoicePaymentStatusByHash"]["status"] == "PAID"
        except Exception:
            pass
        # fallback: 트랜잭션 히스토리 조회
        try:
            cursor = None
            for _ in range(5):
                variables = {"after": cursor} if cursor else {}
                data = await self._gql(Q_TRANSACTIONS, variables)
                txs = data["me"]["defaultAccount"]["transactions"]
                for edge in txs["edges"]:
                    n = edge["node"]
                    via = n.get("initiationVia") or {}
                    if (via.get("paymentHash") == payment_hash
                            and n["status"] == "SUCCESS"
                            and n["direction"] == "RECEIVE"):
                        return True
                page_info = txs["pageInfo"]
                if not page_info["hasNextPage"]:
                    break
                cursor = page_info["endCursor"]
        except Exception as e:
            logger.error(f"check_invoice fallback 오류: {e}")
        return False


class MockLightningManager(BlinkLightningManager):
    async def create_invoice(self, amount_sat: int, memo: str, expiry: int = 3600) -> dict:
        import random, string
        h = "".join(random.choices(string.hexdigits.lower(), k=64))
        b = f"lnbc{amount_sat}n1mock{''.join(random.choices(string.ascii_lowercase, k=20))}"
        logger.info(f"[MOCK] 인보이스 생성: {amount_sat} sat")
        return {"payment_hash": h, "bolt11": b}

    async def check_invoice(self, payment_hash: str) -> bool:
        return False


def LightningManager():
    if not BLINK_API_KEY:
        logger.warning("BLINK_API_KEY 없음 - Mock 모드로 실행")
        return MockLightningManager()
    return BlinkLightningManager()
