"""
어드민 전용 커맨드
/admin stop    — 봇 일시 중지
/admin resume  — 봇 재개
/admin update  — git pull 후 재시작
/admin backup  — DB 백업 DM 전송
/admin stats   — 운영 현황
"""

import os
import discord
from discord.ext import commands
from discord import app_commands
import logging
from utils import success_embed, error_embed, info_embed, file_size_fmt, BotView
from config import ADMIN_IDS, UPLOAD_DIR

logger = logging.getLogger("AdminCog")


def is_admin(interaction: discord.Interaction) -> bool:
    uid = str(interaction.user.id)
    if uid in ADMIN_IDS:
        return True
    if interaction.guild and interaction.user.guild_permissions.administrator:
        return True
    return False


class AdminCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    admin = app_commands.Group(name="admin", description="어드민 전용 명령어")

    # ── /admin stop ────────────────────────────────────────
    @admin.command(name="stop", description="모든 봇 명령어를 일시 중지합니다")
    async def admin_stop(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            return await interaction.response.send_message(
                embed=error_embed("권한 없음", "어드민만 사용할 수 있습니다."), ephemeral=True
            )
        self.bot.stopped = True
        await interaction.response.send_message(
            embed=error_embed("봇 중지됨", "모든 명령어가 중지되었습니다.\n`/admin resume` 으로 재개할 수 있습니다."),
            ephemeral=False
        )
        logger.info(f"봇 중지: by {interaction.user.display_name}")

    # ── /admin resume ───────────────────────────────────────
    @admin.command(name="resume", description="중지된 봇 명령어를 재개합니다")
    async def admin_resume(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            return await interaction.response.send_message(
                embed=error_embed("권한 없음", "어드민만 사용할 수 있습니다."), ephemeral=True
            )
        self.bot.stopped = False
        await interaction.response.send_message(
            embed=success_embed("봇 재개됨", "모든 명령어가 다시 활성화되었습니다."),
            ephemeral=False
        )
        logger.info(f"봇 재개: by {interaction.user.display_name}")

    # ── /admin update ───────────────────────────────────────
    @admin.command(name="update", description="git pull 후 봇을 재시작합니다")
    async def admin_update(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            return await interaction.response.send_message(
                embed=error_embed("권한 없음", "어드민만 사용할 수 있습니다."), ephemeral=True
            )
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(
            embed=success_embed("업데이트 시작", "잠시 후 재시작됩니다."), ephemeral=True
        )
        logger.info(f"업데이트 재시작 요청: by {interaction.user.display_name}")
        self.bot.restart_on_exit = True
        await self.bot.close()

    # ── /admin backup ───────────────────────────────────────
    @admin.command(name="backup", description="데이터베이스 백업 파일을 DM으로 전송합니다")
    async def admin_backup(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            return await interaction.response.send_message(
                embed=error_embed("권한 없음", "어드민만 사용할 수 있습니다."), ephemeral=True
            )
        await interaction.response.defer(ephemeral=True)

        import shutil, os
        from datetime import datetime, timezone
        from config import LIBRARY_DB_PATH

        if not os.path.exists(LIBRARY_DB_PATH):
            return await interaction.followup.send(
                embed=error_embed("백업 실패", "데이터베이스 파일을 찾을 수 없습니다."), ephemeral=True
            )

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_path = f"backup_{ts}.db"
        shutil.copy2(LIBRARY_DB_PATH, backup_path)

        try:
            await interaction.user.send(
                content=f"LibrarianBot DB 백업 `{ts} UTC`",
                file=discord.File(backup_path, filename=f"librarian_{ts}.db")
            )
            await interaction.followup.send(
                embed=success_embed("백업 완료", "DM으로 백업 파일을 전송했습니다."), ephemeral=True
            )
        except discord.Forbidden:
            await interaction.followup.send(
                embed=error_embed("전송 실패", "DM을 보낼 수 없습니다.\nDM 설정을 확인해주세요."), ephemeral=True
            )
        finally:
            try:
                os.remove(backup_path)
            except Exception:
                pass
        logger.info(f"DB 백업 전송: {ts} → {interaction.user.display_name}")

    # ── /admin stats ────────────────────────────────────────
    @admin.command(name="stats", description="봇 운영 현황 요약을 확인합니다")
    async def admin_stats(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            return await interaction.response.send_message(
                embed=error_embed("권한 없음", "어드민만 사용할 수 있습니다."), ephemeral=True
            )
        await interaction.response.defer(ephemeral=True)

        import aiosqlite
        from config import LIBRARY_DB_PATH
        from utils import file_size_fmt
        async with aiosqlite.connect(LIBRARY_DB_PATH) as db:
            async def q(sql):
                async with db.execute(sql) as cur:
                    return await cur.fetchone()
            total_books   = (await q("SELECT COUNT(*) FROM books"))[0]
            total_files   = (await q("SELECT COUNT(*) FROM files"))[0]
            total_size    = (await q("SELECT COALESCE(SUM(file_size),0) FROM files"))[0]
            total_dl      = (await q("SELECT COALESCE(SUM(download_count),0) FROM files"))[0]

        from datetime import datetime, timezone
        e = discord.Embed(title="운영 현황", color=0x3498DB)
        e.add_field(name="전체 엔트리", value=f"{total_books:,}개", inline=True)
        e.add_field(name="전체 파일", value=f"{total_files:,}개", inline=True)
        e.add_field(name="총 용량", value=file_size_fmt(total_size), inline=True)
        e.add_field(name="총 다운로드", value=f"{total_dl:,}회", inline=True)
        e.set_footer(text=f"조회: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC")
        await interaction.followup.send(embed=e, ephemeral=True)


    # ── /admin entries ──────────────────────────────────────
    @admin.command(name="entries", description="전체 엔트리 편집/삭제")
    async def admin_entries(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            return await interaction.response.send_message(
                embed=error_embed("권한 없음", "어드민만 사용할 수 있습니다."), ephemeral=True
            )
        books = await self.bot.db.list_all_books()
        if not books:
            return await interaction.response.send_message(
                embed=info_embed("엔트리 관리", "등록된 엔트리가 없습니다."), ephemeral=True
            )
        view = AdminEntriesView(self.bot, books)
        await interaction.response.send_message(
            embed=info_embed("엔트리 관리", "편집 또는 삭제할 엔트리를 선택하세요."),
            view=view, ephemeral=True,
        )
        view._message_ref = await interaction.original_response()

    # ── /admin files ─────────────────────────────────────
    @admin.command(name="files", description="전체 파일 편집/삭제")
    async def admin_files(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            return await interaction.response.send_message(
                embed=error_embed("권한 없음", "어드민만 사용할 수 있습니다."), ephemeral=True
            )
        # 엔트리 선택 → 파일 선택
        books = await self.bot.db.list_all_books()
        if not books:
            return await interaction.response.send_message(
                embed=info_embed("파일 관리", "등록된 엔트리가 없습니다."), ephemeral=True
            )
        view = AdminFileEntryView(self.bot, books)
        await interaction.response.send_message(
            embed=info_embed("파일 관리", "먼저 엔트리를 선택하세요."),
            view=view, ephemeral=True,
        )
        view._message_ref = await interaction.original_response()


# ── 어드민 뷰 ───────────────────────────────────────

class AdminEntriesView(BotView):
    def __init__(self, bot, books: list[dict]):
        super().__init__(timeout=120)
        self.bot = bot

        options = []
        for b in books[:25]:
            desc = f"{b['file_count']}개 파일 | {b.get('creator_name', '')}"[:100]
            options.append(discord.SelectOption(
                label=b["title"][:100], description=desc, value=str(b["id"]), emoji="📕",
            ))
        select = discord.ui.Select(placeholder="엔트리 선택", options=options, row=0)
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        book_id = int(interaction.data["values"][0])
        book = await self.bot.db.get_book(book_id)
        if not book:
            return await interaction.response.send_message(
                embed=error_embed("엔트리 없음", "해당 엔트리를 찾을 수 없습니다."), ephemeral=True,
            )
        view = AdminEntryActionView(self.bot, book)
        view._message_ref = getattr(self, "_message_ref", None)

        desc_parts = [f"**{book['title']}**"]
        if book.get("author"):
            desc_parts.append(f"저자: {book['author']}")
        if book.get("description"):
            desc_parts.append(book["description"])

        await interaction.response.edit_message(
            embed=info_embed("엔트리 관리", "\n".join(desc_parts)), view=view,
        )


class AdminEntryActionView(BotView):
    def __init__(self, bot, book: dict):
        super().__init__(timeout=120)
        self.bot = bot
        self.book = book

    @discord.ui.button(label="편집", style=discord.ButtonStyle.primary, row=0)
    async def edit_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        from cogs.library import EditEntryModal
        modal = EditEntryModal(self.book)
        await interaction.response.send_modal(modal)
        await modal.wait()
        if not modal.submitted:
            return

        await self.bot.db.update_book(
            book_id=self.book["id"],
            title=modal.book_title.value.strip(),
            alias=modal.book_alias.value.strip() or None,
            author=modal.book_author.value.strip() or None,
            author_alias=modal.book_author_alias.value.strip() or None,
            description=modal.book_desc.value.strip() or None,
        )
        await modal.interaction.edit_original_response(
            embed=success_embed("편집 완료", f"**{modal.book_title.value.strip()}** 수정됨"), view=None,
        )
        self.stop()

    @discord.ui.button(label="삭제", style=discord.ButtonStyle.danger, row=0)
    async def delete_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 파일도 같이 삭제
        files = await self.bot.db.list_book_files(self.book["id"])
        for f in files:
            path = os.path.join(UPLOAD_DIR, f["stored_name"])
            try:
                os.remove(path)
            except OSError:
                pass

        await self.bot.db.delete_book(self.book["id"])
        await interaction.response.edit_message(
            embed=success_embed("삭제 완료", f"**{self.book['title']}** 엔트리와 파일이 삭제되었습니다."),
            view=None,
        )
        self.stop()

    @discord.ui.button(label="← 돌아가기", style=discord.ButtonStyle.secondary, row=0)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        books = await self.bot.db.list_all_books()
        view = AdminEntriesView(self.bot, books)
        view._message_ref = getattr(self, "_message_ref", None)
        await interaction.response.edit_message(
            embed=info_embed("엔트리 관리", "편집 또는 삭제할 엔트리를 선택하세요."), view=view,
        )


class AdminFileEntryView(BotView):
    """먼저 엔트리를 선택"""
    def __init__(self, bot, books: list[dict]):
        super().__init__(timeout=120)
        self.bot = bot

        options = []
        for b in books[:25]:
            options.append(discord.SelectOption(
                label=b["title"][:100],
                description=f"{b['file_count']}개 파일"[:100],
                value=str(b["id"]),
                emoji="📕",
            ))
        select = discord.ui.Select(placeholder="엔트리 선택", options=options)
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        book_id = int(interaction.data["values"][0])
        files = await self.bot.db.list_book_files(book_id)
        if not files:
            return await interaction.response.send_message(
                embed=info_embed("파일 관리", "이 엔트리에 파일이 없습니다."), ephemeral=True,
            )
        view = AdminFilesView(self.bot, files, book_id)
        view._message_ref = getattr(self, "_message_ref", None)
        await interaction.response.edit_message(
            embed=info_embed("파일 관리", "편집 또는 삭제할 파일을 선택하세요."), view=view,
        )


class AdminFilesView(BotView):
    def __init__(self, bot, files: list[dict], book_id: int):
        super().__init__(timeout=120)
        self.bot = bot
        self.book_id = book_id

        options = []
        for f in files[:25]:
            ext = os.path.splitext(f["filename"])[1] or ""
            options.append(discord.SelectOption(
                label=f"{f['title']}{ext} ({file_size_fmt(f['file_size'])})"[:100],
                description=(f.get("description") or "")[:100],
                value=str(f["id"]),
                emoji="💾",
            ))
        select = discord.ui.Select(placeholder="파일 선택", options=options, row=0)
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        file_id = int(interaction.data["values"][0])
        file_info = await self.bot.db.get_file(file_id)
        if not file_info:
            return await interaction.response.send_message(
                embed=error_embed("파일 없음", "해당 파일을 찾을 수 없습니다."), ephemeral=True,
            )
        view = AdminFileActionView(self.bot, file_info, self.book_id)
        view._message_ref = getattr(self, "_message_ref", None)

        ext = os.path.splitext(file_info["filename"])[1] or ""
        await interaction.response.edit_message(
            embed=info_embed("파일 관리", f"**{file_info['title']}{ext}** ({file_size_fmt(file_info['file_size'])})"),
            view=view,
        )


class AdminFileActionView(BotView):
    def __init__(self, bot, file_info: dict, book_id: int):
        super().__init__(timeout=120)
        self.bot = bot
        self.file_info = file_info
        self.book_id = book_id

    @discord.ui.button(label="편집", style=discord.ButtonStyle.primary, row=0)
    async def edit_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        from cogs.library import EditFileModal
        modal = EditFileModal(self.file_info)
        await interaction.response.send_modal(modal)
        await modal.wait()
        if not modal.submitted:
            return

        ext = os.path.splitext(self.file_info["filename"])[1] or ""
        new_filename = modal.file_title.value.strip() + ext

        await self.bot.db.update_file(
            file_id=self.file_info["id"],
            title=modal.file_title.value.strip(),
            description=modal.file_desc.value.strip(),
            filename=new_filename,
        )
        await modal.interaction.edit_original_response(
            embed=success_embed("편집 완료", f"**{modal.file_title.value.strip()}{ext}** 수정됨"), view=None,
        )
        self.stop()

    @discord.ui.button(label="삭제", style=discord.ButtonStyle.danger, row=0)
    async def delete_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        path = os.path.join(UPLOAD_DIR, self.file_info["stored_name"])
        try:
            os.remove(path)
        except OSError:
            pass

        await self.bot.db.delete_file(self.file_info["id"])
        await interaction.response.edit_message(
            embed=success_embed("삭제 완료", f"**{self.file_info['title']}** 파일이 삭제되었습니다."),
            view=None,
        )
        self.stop()

    @discord.ui.button(label="← 돌아가기", style=discord.ButtonStyle.secondary, row=0)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        files = await self.bot.db.list_book_files(self.book_id)
        view = AdminFilesView(self.bot, files, self.book_id)
        view._message_ref = getattr(self, "_message_ref", None)
        await interaction.response.edit_message(
            embed=info_embed("파일 관리", "편집 또는 삭제할 파일을 선택하세요."), view=view,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
