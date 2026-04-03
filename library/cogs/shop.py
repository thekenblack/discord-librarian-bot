"""
/buy 커맨드 — 사서봇에게 선물 사주기
"""

import discord
from discord import app_commands
from discord.ext import commands
from library.utils import BotView, info_embed, success_embed
from config import AI_NAME

# 아이템 목록 (나중에 config.json이나 DB로 이동 가능)
SHOP_ITEMS = [
    {"id": "coffee", "name": "커피", "emoji": "☕", "description": "따뜻한 커피 한 잔", "effects": {"self_energy": 8, "self_mood": 5, "comfort": 3, "affinity": 5}},
    {"id": "cake", "name": "케이크", "emoji": "🍰", "description": "달콤한 케이크 한 조각", "effects": {"self_mood": 10, "self_energy": 3, "comfort": 3, "affinity": 5}},
    {"id": "book", "name": "책", "emoji": "📖", "description": "흥미로운 신간 한 권", "effects": {"self_energy": 5, "self_mood": 8, "comfort": 3, "trust": 5, "affinity": 3}},
    {"id": "flower", "name": "꽃다발", "emoji": "💐", "description": "향기로운 꽃다발", "effects": {"self_mood": 12, "affinity": 8, "comfort": 5}},
    {"id": "pizza", "name": "피자", "emoji": "🍕", "description": "든든한 피자 한 판", "effects": {"self_energy": 12, "self_mood": 3, "comfort": 5, "affinity": 3}},
]

# 선물 메시지 마커 (사서봇이 감지할 수 있도록)
GIFT_MARKER = "[GIFT]"


class BuyView(BotView):
    """아이템 선택 드롭다운"""

    def __init__(self, buyer: discord.Member):
        super().__init__(timeout=60)
        self.buyer = buyer

        options = [
            discord.SelectOption(
                label=f"{item['emoji']} {item['name']}",
                description=item["description"],
                value=item["id"],
            )
            for item in SHOP_ITEMS
        ]

        select = discord.ui.Select(
            placeholder="선물을 골라주세요",
            options=options,
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.buyer.id:
            return await interaction.response.send_message(
                "본인만 선택할 수 있습니다.", ephemeral=True)

        item_id = interaction.data["values"][0]
        item = next((i for i in SHOP_ITEMS if i["id"] == item_id), None)
        if not item:
            return await interaction.response.send_message(
                "알 수 없는 아이템입니다.", ephemeral=True)

        # 에페메럴 메시지 업데이트
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            embed=success_embed("선물 완료", f"{item['emoji']} {item['name']}을(를) 선물했습니다!"),
            view=self,
        )

        # 공개 메시지 전송 (화자: Citadel Library)
        effects_json = ",".join(f"{k}:{v}" for k, v in item["effects"].items())
        # 마커는 임베드 footer에 숨김 (사서봇이 감지용)
        embed = discord.Embed(
            description=f"{item['emoji']} **{interaction.user.display_name}** 님이 {AI_NAME}에게 **{item['name']}**을(를) 사줬습니다!",
            color=0xF1C40F,
        )
        embed.set_footer(text=f"{GIFT_MARKER} {item_id} {effects_json} {interaction.user.id}")

        await interaction.channel.send(embed=embed)

        self.stop()


class ShopCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="buy", description=f"사서봇에게 선물 사주기")
    async def buy(self, interaction: discord.Interaction):
        view = BuyView(buyer=interaction.user)
        embed = info_embed(
            f"{AI_NAME}에게 선물하기",
            "아래에서 선물할 아이템을 골라주세요.",
        )
        await interaction.response.send_message(
            embed=embed, view=view, ephemeral=True)
        view._message_ref = await interaction.original_response()


async def setup(bot: commands.Bot):
    await bot.add_cog(ShopCog(bot))
