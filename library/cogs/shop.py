"""
경제 시스템 — /charge (Lightning 충전), /buy (선물 구매)
"""

import asyncio
import discord
from discord import app_commands
from discord.ext import commands, tasks
import logging
from library.utils import (
    BotView, info_embed, success_embed, error_embed,
    sat_fmt, make_qr_file, LIGHTNING_COLOR,
)
from config import AI_NAME, MIN_CHARGE_SAT, INVOICE_EXPIRE, ADMIN_IDS
from librarian.db import LibrarianDB

logger = logging.getLogger("ShopCog")

# ── 아이템 목록 (가격 오름차순) ──────────────────────────

# 페이지 1: 일반 선물 (봇도 유저에게 줄 수 있는 아이템)
SHOP_PAGE1 = [
    {"id": "water",     "name": "생수",         "emoji": "💧", "price": 12,   "description": "시원한 생수 한 병",           "effects": {"hydration": 5, "self_energy": 1}},
    {"id": "cookie",    "name": "쿠키",         "emoji": "🍪", "price": 21,   "description": "바삭한 쿠키 한 봉지",         "effects": {"fullness": 3, "self_mood": 1}},
    {"id": "tea",       "name": "차",           "emoji": "🍵", "price": 30,   "description": "따뜻한 차 한 잔",             "effects": {"hydration": 8, "self_mood": 1, "self_energy": 1}},
    {"id": "coffee",    "name": "커피",         "emoji": "☕", "price": 50,   "description": "향긋한 커피 한 잔",           "effects": {"hydration": 8, "self_mood": 1, "self_energy": 2}},
    {"id": "chocolate", "name": "초콜릿",       "emoji": "🍫", "price": 80,   "description": "달콤한 초콜릿 한 상자",       "effects": {"fullness": 5, "self_mood": 2}},
    {"id": "ramen",     "name": "라면",         "emoji": "🍜", "price": 120,  "description": "뜨끈한 라면 한 그릇",         "effects": {"fullness": 8, "hydration": 5, "self_mood": 2, "self_energy": 1}},
    {"id": "cake",      "name": "케이크",       "emoji": "🍰", "price": 210,  "description": "달콤한 케이크 한 조각",       "effects": {"fullness": 10, "self_mood": 3}},
    {"id": "book",      "name": "책",           "emoji": "📖", "price": 300,  "description": "흥미로운 신간 한 권",         "effects": {"self_mood": 3}},
    {"id": "pizza",     "name": "피자",         "emoji": "🍕", "price": 500,  "description": "든든한 피자 한 판",           "effects": {"fullness": 15, "self_mood": 3, "self_energy": 2}},
    {"id": "flower",    "name": "꽃다발",       "emoji": "💐", "price": 800,  "description": "향기로운 꽃다발",             "effects": {"self_mood": 5}},
    {"id": "beef",      "name": "소고기",       "emoji": "🥩", "price": 1200, "description": "맛있는 소고기",               "effects": {"fullness": 20, "self_mood": 5, "self_energy": 3}},
    {"id": "cat_doll",  "name": "고양이 인형",  "emoji": "🐱", "price": 2100, "description": "커다란 고양이 인형",          "effects": {"self_mood": 8}},
]

# 페이지 2: 봇에게 안 보이는 아이템 (팁 + 장난)
SHOP_PAGE2 = [
    # sat 용돈
    {"id": "tip_1",       "name": "1 sat",        "emoji": "⚡", "price": 1,      "description": "1 사츠만큼의 용돈",              "effects": {}},
    {"id": "tip_21",      "name": "21 sat",       "emoji": "⚡", "price": 21,     "description": "21 사츠만큼의 용돈",             "effects": {}},
    {"id": "tip_210",     "name": "210 sat",      "emoji": "⚡", "price": 210,    "description": "210 사츠만큼의 용돈",            "effects": {}},
    {"id": "tip_2100",    "name": "2,100 sat",    "emoji": "⚡", "price": 2100,   "description": "2,100 사츠만큼의 용돈",          "effects": {}},
    {"id": "tip_21000",   "name": "21,000 sat",   "emoji": "⚡", "price": 21000,  "description": "21,000 사츠만큼의... 용돈...?",  "effects": {}},
    {"id": "tip_210000",  "name": "210,000 sat",  "emoji": "⚡", "price": 210000, "description": "210,000 사츠......?!?!",         "effects": {}},
    # 장난 (가격순)
    {"id": "fiat",        "name": "1달러 지폐",   "emoji": "💵", "price": 300,    "description": "똥 닦을 때 쓰는 종이",            "effects": {}},
    {"id": "homework",    "name": "숙제",         "emoji": "📝", "price": 400,    "description": "비트쨩한테 밀린 숙제 떠넘기기",   "effects": {}},
    {"id": "cigarette",   "name": "담배",         "emoji": "🚬", "price": 500,    "description": "스트레스가 풀리는 담배 한 갑",    "effects": {}},
    {"id": "soju",        "name": "소주",         "emoji": "🍶", "price": 600,    "description": "화끈하게 마시기 좋은 소주 한 병", "effects": {}},
    {"id": "durian",      "name": "두리안",       "emoji": "🍈", "price": 800,    "description": "냄새 폭탄 두리안",               "effects": {}},
    {"id": "bikini",      "name": "비키니",       "emoji": "👙", "price": 2100,   "description": "누가 입으라는 거야",             "effects": {}},
]

SHOP_ITEMS = SHOP_PAGE1 + SHOP_PAGE2
SHOP_MAP = {item["id"]: item for item in SHOP_ITEMS}


# ── 선물 완료 공통 로직 ──────────────────────────────────

async def complete_gift(bot, user_id: str, user_name: str, item: dict,
                        channel_id: str, message: str = None):
    """잔고 차감 + DB 선물 저장 + 공개 알림. 새 잔고 반환 (None이면 잔고 부족)."""
    new_balance = await bot.db.spend_balance(
        user_id, item["price"], note=f"{item['emoji']} {item['name']}",
        item_emoji=item["emoji"], item_name=item["name"], item_price=item["price"])
    if new_balance is None:
        return None

    effects_str = ",".join(f"{k}:{v}" for k, v in item["effects"].items())
    ldb = LibrarianDB()
    await ldb.init()
    await ldb.save_pending_gift(
        channel_id=channel_id,
        buyer_id=user_id,
        item_id=item["id"],
        item_name=item["name"],
        item_emoji=item["emoji"],
        effects=effects_str,
    )

    # 선물 기록 (영구, 유저→봇)
    await ldb.save_gift_log(
        buyer_id=user_id, buyer_name=user_name,
        item_emoji=item["emoji"], item_name=item["name"],
        item_price=item["price"], message=message,
        recipient_name=AI_NAME)

    # 공개 알림
    try:
        channel = bot.get_channel(int(channel_id))
        if channel:
            if item["id"].startswith("tip_"):
                desc = f"{item['emoji']} **{user_name}** 님이 {AI_NAME}에게 **{sat_fmt(item['price'])}**을(를) 보내줬습니다!"
            else:
                desc = f"{item['emoji']} **{user_name}** 님이 {AI_NAME}에게 **{item['name']}**을(를) 사줬습니다! ({sat_fmt(item['price'])})"
            if message:
                desc += f"\n> {user_name}: \"{message}\""
            embed = discord.Embed(description=desc, color=0xF1C40F)
            embed.set_footer(text="/charge 로 충전 · /buy 로 선물")
            await channel.send(embed=embed)
    except Exception as e:
        logger.warning(f"선물 알림 전송 실패: {e}")

    return new_balance


# ══════════════════════════════════════════════
# /status — 상태 확인
# ══════════════════════════════════════════════

PER_PAGE = 5

class StatusView(BotView):
    def __init__(self, uid: str, bot):
        super().__init__(timeout=120)
        self.uid = uid
        self.bot = bot
        self.page = 0
        self.total = 0

    async def make_embed(self) -> discord.Embed:
        bal = await self.bot.db.get_balance(self.uid)
        total_gifted = await self.bot.db.get_total_gifted(self.uid)
        self.total = await self.bot.db.get_gift_count(self.uid)
        gifts = await self.bot.db.get_gift_history(self.uid, limit=PER_PAGE, offset=self.page * PER_PAGE)
        max_page = max(0, (self.total - 1) // PER_PAGE)

        lines = [f"잔고: {sat_fmt(bal)}", f"선물: {sat_fmt(total_gifted)}"]
        if gifts:
            lines.append("")
            for g in gifts:
                date = g["created_at"][:10]
                emoji = g.get("item_emoji") or ""
                name = g.get("item_name") or ""
                price = g.get("item_price")
                price_str = sat_fmt(price) if price else ""
                lines.append(f"  {date} {emoji} {name} {price_str}")
            if self.total > PER_PAGE:
                lines.append(f"\n{self.page + 1}/{max_page + 1} 페이지")
        else:
            lines.append("\n선물 기록 없음")

        embed = info_embed("내 상태", "\n".join(lines))
        embed.set_footer(text="/charge 로 충전 · /buy 로 선물")
        return embed

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button):
        if self.page > 0:
            self.page -= 1
        await interaction.response.edit_message(embed=await self.make_embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button):
        max_page = max(0, (self.total - 1) // PER_PAGE)
        if self.page < max_page:
            self.page += 1
        await interaction.response.edit_message(embed=await self.make_embed(), view=self)

    @discord.ui.button(label="충전", style=discord.ButtonStyle.primary, emoji="⚡")
    async def charge_btn(self, interaction: discord.Interaction, button):
        await interaction.response.send_message(
            embed=info_embed("충전", "`/charge [금액]` 으로 Lightning 충전할 수 있습니다."),
            ephemeral=True)

    @discord.ui.button(label="닫기", style=discord.ButtonStyle.secondary)
    async def close_btn(self, interaction: discord.Interaction, button):
        await interaction.response.edit_message(view=None)
        self.stop()


# ══════════════════════════════════════════════
# /charge — Lightning 충전
# ══════════════════════════════════════════════

class ChargeView(BotView):
    """충전 인보이스 + 입금 확인 + 취소"""

    def __init__(self, bot, bolt11: str, payment_hash: str, user_id: str, amount: int):
        super().__init__(timeout=INVOICE_EXPIRE)
        self.bot = bot
        self.bolt11 = bolt11
        self.payment_hash = payment_hash
        self.user_id = user_id
        self.amount = amount
        self.invoice_msg = None

    @discord.ui.button(label="입금 확인", style=discord.ButtonStyle.success, emoji="🔄")
    async def check(self, interaction: discord.Interaction, button):
        await interaction.response.defer()
        paid = await self.bot.ln.check_invoice(self.payment_hash)
        if paid:
            result = await self.bot.db.mark_invoice_paid(self.payment_hash)
            for item in self.children:
                item.disabled = True
            new_balance = await self.bot.db.get_balance(self.user_id)
            e = success_embed("충전 완료",
                f"{sat_fmt(self.amount)} 이 잔고에 추가되었습니다.\n현재 잔고: {sat_fmt(new_balance)}")
            e.set_footer(text="/charge 로 충전 · /buy 로 선물")
            await interaction.edit_original_response(embed=e, view=None, attachments=[])
            if self.invoice_msg:
                try: await self.invoice_msg.delete()
                except Exception: pass
            self.stop()
        else:
            await interaction.followup.send(
                embed=info_embed("아직 미입금", "아직 결제가 확인되지 않았습니다."),
                ephemeral=True)

    @discord.ui.button(label="인보이스 복사", style=discord.ButtonStyle.secondary, emoji="📋")
    async def copy_invoice(self, interaction: discord.Interaction, button):
        await interaction.response.send_message(self.bolt11, ephemeral=True)

    @discord.ui.button(label="취소", style=discord.ButtonStyle.danger, emoji="❌")
    async def cancel(self, interaction: discord.Interaction, button):
        await interaction.response.defer()
        await self.bot.db.cancel_invoice(self.user_id, self.payment_hash)
        for item in self.children:
            item.disabled = True
        e = error_embed("인보이스 취소됨", "충전 요청이 취소되었습니다.")
        await interaction.edit_original_response(embed=e, view=None, attachments=[])
        if self.invoice_msg:
            try: await self.invoice_msg.delete()
            except Exception: pass
        self.stop()


# ══════════════════════════════════════════════
# 선물 메시지 입력 모달
# ══════════════════════════════════════════════

class GiftMessageModal(discord.ui.Modal, title="선물 메시지"):
    msg_input = discord.ui.TextInput(
        label="메시지 (선택)", placeholder="한마디 남기기",
        required=False, max_length=100, style=discord.TextStyle.short)

    def __init__(self, buy_view, item: dict, original_interaction):
        super().__init__(timeout=60)
        self.buy_view = buy_view
        self.item = item
        self.original_interaction = original_interaction

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        msg = self.msg_input.value.strip() or None
        await self.buy_view._process_gift(interaction, self.item, message=msg)


# ══════════════════════════════════════════════
# /buy — 선물 구매 (잔고 부족 시 인보이스 자동 발행)
# ══════════════════════════════════════════════

SHOP_PAGES = [SHOP_PAGE1, SHOP_PAGE2]
PAGE_LABELS = ["선물", "기타"]


class BuyView(BotView):
    """아이템 선택 드롭다운 (row 0) + 페이지/충전/취소 버튼 (row 1)"""

    def __init__(self, buyer: discord.Member, balance: int, page: int = 0):
        super().__init__(timeout=120)
        self.buyer = buyer
        self.balance = balance
        self.page = page
        self._build_select()

    def _build_select(self):
        # 기존 select 제거
        for child in list(self.children):
            if isinstance(child, discord.ui.Select):
                self.remove_item(child)

        items = SHOP_PAGES[self.page]
        options = [
            discord.SelectOption(
                label=f"{item['emoji']} {item['name']} — {sat_fmt(item['price'])}",
                description=item["description"],
                value=item["id"],
            )
            for item in items
        ]
        select = discord.ui.Select(
            placeholder="선물을 골라주세요",
            options=options[:25],
            row=0,
        )
        select.callback = self._on_select
        self.add_item(select)

    def _make_embed(self):
        items = SHOP_PAGES[self.page]
        lines = []
        for item in items:
            if item["id"].startswith("tip_"):
                lines.append(f"{item['emoji']} {sat_fmt(item['price'])}")
                lines.append(f"> {item['description']}")
            else:
                lines.append(f"{item['emoji']} {item['name']} — {sat_fmt(item['price'])}")
                lines.append(f"> {item['description']}")
        items_text = "\n".join(lines)
        return info_embed(
            f"{AI_NAME}에게 선물하기 ({PAGE_LABELS[self.page]})",
            f"잔고: {sat_fmt(self.balance)}\n\n{items_text}\n\n"
            f"잔고가 부족하면 부족분만큼 인보이스가 자동 발행됩니다.",
        )

    @discord.ui.button(label="◀ 이전", style=discord.ButtonStyle.secondary, row=1)
    async def prev_btn(self, interaction: discord.Interaction, button):
        if interaction.user.id != self.buyer.id:
            return await interaction.response.send_message("본인만 사용할 수 있습니다.", ephemeral=True)
        self.page = (self.page - 1) % len(SHOP_PAGES)
        self._build_select()
        await interaction.response.edit_message(embed=self._make_embed(), view=self)

    @discord.ui.button(label="다음 ▶", style=discord.ButtonStyle.secondary, row=1)
    async def next_btn(self, interaction: discord.Interaction, button):
        if interaction.user.id != self.buyer.id:
            return await interaction.response.send_message("본인만 사용할 수 있습니다.", ephemeral=True)
        self.page = (self.page + 1) % len(SHOP_PAGES)
        self._build_select()
        await interaction.response.edit_message(embed=self._make_embed(), view=self)

    @discord.ui.button(label="충전", style=discord.ButtonStyle.primary, emoji="⚡", row=1)
    async def charge_btn(self, interaction: discord.Interaction, button):
        if interaction.user.id != self.buyer.id:
            return await interaction.response.send_message("본인만 사용할 수 있습니다.", ephemeral=True)
        await interaction.response.send_message(
            embed=info_embed("충전", "`/charge [금액]` 으로 Lightning 충전할 수 있습니다."),
            ephemeral=True)

    @discord.ui.button(label="취소", style=discord.ButtonStyle.secondary, row=1)
    async def cancel_btn(self, interaction: discord.Interaction, button):
        if interaction.user.id != self.buyer.id:
            return await interaction.response.send_message("본인만 사용할 수 있습니다.", ephemeral=True)
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            embed=info_embed("취소됨", "선물 구매가 취소되었습니다."), view=self)
        self.stop()

    async def _on_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.buyer.id:
            return await interaction.response.send_message("본인만 선택할 수 있습니다.", ephemeral=True)

        item_id = interaction.data["values"][0]
        item = SHOP_MAP.get(item_id)
        if not item:
            return await interaction.response.send_message("알 수 없는 아이템입니다.", ephemeral=True)

        # 메시지 입력 모달
        modal = GiftMessageModal(self, item, interaction)
        await interaction.response.send_modal(modal)

    async def _process_gift(self, interaction: discord.Interaction, item: dict, message: str = None):
        """아이템 선택 + 메시지 입력 후 실제 구매 처리."""
        db = interaction.client.db
        balance = await db.get_balance(str(interaction.user.id))

        if balance >= item["price"]:
            new_balance = await complete_gift(
                interaction.client,
                str(interaction.user.id),
                interaction.user.display_name,
                item,
                str(interaction.channel_id),
                message=message)

            if new_balance is None:
                balance = await db.get_balance(str(interaction.user.id))
                deficit = item["price"] - balance
                await self._issue_deficit_invoice(interaction, item, balance, deficit, message)
                return

            for child in self.children:
                child.disabled = True
            await interaction.edit_original_response(
                embed=success_embed(
                    "선물 완료",
                    f"{item['emoji']} {item['name']}을(를) 선물했습니다!\n남은 잔고: {sat_fmt(new_balance)}"),
                view=None)
            self.stop()
        else:
            deficit = item["price"] - balance
            await self._issue_deficit_invoice(interaction, item, balance, deficit, message)

    async def _issue_deficit_invoice(self, interaction: discord.Interaction, item: dict,
                                      balance: int, deficit: int, message: str = None):
        """부족분만큼 인보이스 발행 → BuyInvoiceView로 전환"""
        bot = interaction.client
        user = interaction.user

        # 기존 pending 인보이스 취소
        await bot.db.cancel_user_pending_invoices(str(user.id))

        try:
            inv = await bot.ln.create_invoice(
                amount_sat=deficit,
                memo=f"Library {item['emoji']}{item['name']} @{user.display_name}",
                expiry=INVOICE_EXPIRE,
            )
        except Exception as e:
            logger.error(f"인보이스 생성 오류: {e}")
            await interaction.followup.send(
                embed=error_embed("인보이스 생성 실패", str(e)), ephemeral=True)
            return

        embed = discord.Embed(
            title=f"{item['emoji']} {item['name']} 구매 — 부족분 충전",
            description=(
                f"가격: {sat_fmt(item['price'])}\n"
                f"잔고: {sat_fmt(balance)}\n"
                f"부족분: {sat_fmt(deficit)}\n\n"
                f"QR코드를 스캔하거나 인보이스를 복사해 결제하세요.\n"
                f"결제 완료 후 자동으로 선물이 전달됩니다."
            ),
            color=LIGHTNING_COLOR,
        )
        embed.add_field(name="만료", value=f"{INVOICE_EXPIRE // 60}분", inline=True)
        embed.add_field(name="충전 금액", value=sat_fmt(deficit), inline=True)
        embed.add_field(name="상태", value="결제 대기 중", inline=True)
        embed.set_image(url="attachment://invoice_qr.png")
        embed.set_footer(text="결제 완료 시 자동으로 선물이 전달됩니다")

        view = BuyInvoiceView(bot, inv["bolt11"], inv["payment_hash"],
                              str(user.id), user.display_name,
                              deficit, item, str(interaction.channel_id), message)

        try:
            msg = await interaction.edit_original_response(
                embed=embed, view=view, attachments=[make_qr_file(inv["bolt11"])])
        except Exception:
            msg = await interaction.edit_original_response(embed=embed, view=view)

        # 인보이스 텍스트 (모바일 복사)
        view.invoice_msg = await interaction.followup.send(inv["bolt11"], ephemeral=True, wait=True)

        channel_id = str(interaction.channel_id) if interaction.channel_id else None
        await bot.db.save_invoice(
            inv["payment_hash"], str(user.id), deficit, inv["bolt11"],
            message_id=str(msg.id) if hasattr(msg, 'id') else None,
            channel_id=channel_id,
            buy_item_id=item["id"])

        self.stop()


class BuyInvoiceView(BotView):
    """잔고 부족 시 발행된 인보이스: 입금 확인 + 복사 + 취소"""

    def __init__(self, bot, bolt11: str, payment_hash: str,
                 user_id: str, user_name: str, amount: int,
                 item: dict, channel_id: str, message: str = None):
        super().__init__(timeout=INVOICE_EXPIRE)
        self.bot = bot
        self.bolt11 = bolt11
        self.payment_hash = payment_hash
        self.user_id = user_id
        self.user_name = user_name
        self.amount = amount
        self.gift_message = message
        self.item = item
        self.channel_id = channel_id
        self.invoice_msg = None

    @discord.ui.button(label="입금 확인", style=discord.ButtonStyle.success, emoji="🔄")
    async def check(self, interaction: discord.Interaction, button):
        await interaction.response.defer()
        paid = await self.bot.ln.check_invoice(self.payment_hash)
        if paid:
            # 잔고 추가
            result = await self.bot.db.mark_invoice_paid(self.payment_hash)
            # 선물 완료
            new_balance = await complete_gift(
                self.bot, self.user_id, self.user_name,
                self.item, self.channel_id, message=self.gift_message)

            for item in self.children:
                item.disabled = True
            if new_balance is not None:
                e = success_embed("선물 완료",
                    f"{self.item['emoji']} {self.item['name']}을(를) 선물했습니다!\n"
                    f"남은 잔고: {sat_fmt(new_balance)}")
            else:
                e = error_embed("결제 완료 — 잔고 부족",
                    f"충전은 완료되었지만 잔고가 부족합니다.\n`/buy` 로 다시 시도해주세요.")
            e.set_footer(text="/charge 로 충전 · /buy 로 선물")
            await interaction.edit_original_response(embed=e, view=None, attachments=[])
            if self.invoice_msg:
                try: await self.invoice_msg.delete()
                except Exception: pass
            self.stop()
        else:
            await interaction.followup.send(
                embed=info_embed("아직 미입금", "아직 결제가 확인되지 않았습니다."),
                ephemeral=True)

    @discord.ui.button(label="인보이스 복사", style=discord.ButtonStyle.secondary, emoji="📋")
    async def copy_invoice(self, interaction: discord.Interaction, button):
        await interaction.response.send_message(self.bolt11, ephemeral=True)

    @discord.ui.button(label="취소", style=discord.ButtonStyle.danger, emoji="❌")
    async def cancel(self, interaction: discord.Interaction, button):
        await interaction.response.defer()
        await self.bot.db.cancel_invoice(self.user_id, self.payment_hash)
        for item in self.children:
            item.disabled = True
        e = info_embed("취소됨", "선물 구매가 취소되었습니다.")
        await interaction.edit_original_response(embed=e, view=None, attachments=[])
        if self.invoice_msg:
            try: await self.invoice_msg.delete()
            except Exception: pass
        self.stop()


# ══════════════════════════════════════════════
# Cog
# ══════════════════════════════════════════════

class ShopCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.poll_invoices.start()

    def cog_unload(self):
        self.poll_invoices.cancel()

    # ── /charge ────────────────────────────────────────────
    @app_commands.command(name="charge", description="Lightning으로 잔고를 충전합니다")
    @app_commands.describe(amount="충전할 satoshi 금액")
    async def charge(self, interaction: discord.Interaction, amount: int):
        await interaction.response.defer(ephemeral=True)

        if amount < MIN_CHARGE_SAT:
            return await interaction.followup.send(
                embed=error_embed("금액 오류", f"최소 충전액은 {sat_fmt(MIN_CHARGE_SAT)} 입니다."),
                ephemeral=True)

        user = interaction.user
        await self.bot.db.get_or_create_wallet(str(user.id), user.display_name)

        # 기존 pending 인보이스 취소
        await self.bot.db.cancel_user_pending_invoices(str(user.id))

        try:
            inv = await self.bot.ln.create_invoice(
                amount_sat=amount,
                memo=f"Library @{user.display_name}",
                expiry=INVOICE_EXPIRE,
            )
        except Exception as e:
            logger.error(f"인보이스 생성 오류: {e}")
            return await interaction.followup.send(
                embed=error_embed("인보이스 생성 실패", str(e)), ephemeral=True)

        embed = discord.Embed(
            title=f"{sat_fmt(amount)} 충전 인보이스",
            description=(
                "QR코드를 스캔하거나 인보이스를 복사해 결제하세요.\n"
                "결제 완료 후 자동으로 잔고에 반영됩니다."
            ),
            color=LIGHTNING_COLOR,
        )
        embed.add_field(name="만료", value=f"{INVOICE_EXPIRE // 60}분", inline=True)
        embed.add_field(name="금액", value=sat_fmt(amount), inline=True)
        embed.add_field(name="상태", value="결제 대기 중", inline=True)
        embed.set_image(url="attachment://invoice_qr.png")
        embed.set_footer(text="/charge 로 충전 · /buy 로 선물")

        view = ChargeView(self.bot, inv["bolt11"], inv["payment_hash"], str(user.id), amount)
        try:
            msg = await interaction.followup.send(
                embed=embed, file=make_qr_file(inv["bolt11"]),
                view=view, ephemeral=True, wait=True)
        except Exception:
            msg = await interaction.followup.send(
                embed=embed, view=view, ephemeral=True, wait=True)

        # 인보이스 텍스트 (모바일 복사)
        view.invoice_msg = await interaction.followup.send(inv["bolt11"], ephemeral=True, wait=True)

        channel_id = str(interaction.channel_id) if interaction.channel_id else None
        await self.bot.db.save_invoice(
            inv["payment_hash"], str(user.id), amount, inv["bolt11"],
            message_id=str(msg.id), channel_id=channel_id)

    # ── /buy ───────────────────────────────────────────────
    @app_commands.command(name="buy", description=f"{AI_NAME}에게 선물 사주기")
    async def buy(self, interaction: discord.Interaction):
        balance = await self.bot.db.get_balance(str(interaction.user.id))
        view = BuyView(buyer=interaction.user, balance=balance)
        embed = view._make_embed()
        embed.set_footer(text="/charge 로 충전 · /buy 로 선물")

        await interaction.response.send_message(
            embed=embed, view=view, ephemeral=True)
        view._message_ref = await interaction.original_response()

    # ── /status ────────────────────────────────────────────
    @app_commands.command(name="status", description="내 상태 확인")
    async def status(self, interaction: discord.Interaction):
        view = StatusView(str(interaction.user.id), self.bot)
        embed = await view.make_embed()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # ── 폴링: 인보이스 결제 확인 ───────────────────────────
    @tasks.loop(seconds=15)
    async def poll_invoices(self):
        try:
            pending = await self.bot.db.get_pending_invoices(expire_seconds=INVOICE_EXPIRE)
        except Exception as e:
            logger.error(f"폴링 DB 오류: {e}")
            return

        for inv in pending:
            try:
                if not await self.bot.ln.check_invoice(inv["payment_hash"]):
                    await asyncio.sleep(0.3)
                    continue

                result = await self.bot.db.mark_invoice_paid(inv["payment_hash"])
                if not result:
                    continue

                deposited = result["amount"]
                user_id = inv["user_id"]
                buy_item_id = inv.get("buy_item_id")
                logger.info(f"충전 완료: user={user_id} amount={deposited} buy={buy_item_id}")

                # buy 연결 인보이스 → 자동 선물
                if buy_item_id:
                    item = SHOP_MAP.get(buy_item_id)
                    if item:
                        user = self.bot.get_user(int(user_id))
                        user_name = user.display_name if user else f"<@{user_id}>"
                        ch_id = inv.get("channel_id")
                        if ch_id:
                            new_balance = await complete_gift(
                                self.bot, user_id, user_name, item, ch_id)
                            if new_balance is not None:
                                logger.info(f"자동 선물 완료: {item['name']} -> {user_name}")
                            else:
                                logger.warning(f"자동 선물 실패 (잔고 부족): {user_name}")

                # 인보이스 메시지 업데이트
                msg_id = inv.get("message_id")
                ch_id = inv.get("channel_id")
                if msg_id and ch_id:
                    try:
                        channel = self.bot.get_channel(int(ch_id))
                        if channel:
                            msg = await channel.fetch_message(int(msg_id))
                            new_balance = await self.bot.db.get_balance(user_id)
                            if buy_item_id and SHOP_MAP.get(buy_item_id):
                                _item = SHOP_MAP[buy_item_id]
                                e = success_embed("선물 완료",
                                    f"{_item['emoji']} {_item['name']}을(를) 선물했습니다!\n"
                                    f"남은 잔고: {sat_fmt(new_balance)}")
                            else:
                                e = success_embed("충전 완료",
                                    f"{sat_fmt(deposited)} 이 잔고에 추가되었습니다.\n"
                                    f"현재 잔고: {sat_fmt(new_balance)}")
                            e.set_footer(text="/charge 로 충전 · /buy 로 선물")
                            await msg.edit(embed=e, view=None, attachments=[])
                    except Exception:
                        pass

            except Exception as e:
                logger.error(f"폴링 오류: {e}")

    @poll_invoices.before_loop
    async def before_poll(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(ShopCog(bot))
