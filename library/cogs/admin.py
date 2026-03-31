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
from library.utils import success_embed, error_embed, info_embed, file_size_fmt, BotView
from config import ADMIN_IDS, FILES_DIR, LOG_DIR

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
                content=f"LibraryBot DB 백업 `{ts} UTC`",
                file=discord.File(backup_path, filename=f"library_{ts}.db")
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
        from library.utils import file_size_fmt
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

    # ── /admin log ──────────────────────────────────────────
    @admin.command(name="log", description="AI 봇 로그 파일 전송")
    async def admin_log(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            return await interaction.response.send_message(
                embed=error_embed("권한 없음", "어드민만 사용할 수 있습니다."), ephemeral=True
            )
        await interaction.response.defer(ephemeral=True)

        from datetime import datetime as dt
        today = dt.now().strftime("%Y-%m-%d")
        log_path = os.path.join(LOG_DIR, f"bot.{today}.log")

        if not os.path.exists(log_path):
            return await interaction.followup.send(
                embed=error_embed("로그 없음", "로그 파일이 없습니다."), ephemeral=True
            )

        # 마지막 30줄 코드블락
        try:
            with open(log_path, encoding="utf-8") as f:
                tail_lines = f.readlines()[-30:]
            log_text = "".join(tail_lines)
            code_block = f"```\n{log_text[-1800:]}\n```"
        except Exception:
            code_block = "(로그 읽기 실패)"

        try:
            await interaction.user.send(content=code_block)
            await interaction.user.send(
                content="봇 로그 파일",
                file=discord.File(log_path, filename=f"bot.{today}.log")
            )
            await interaction.followup.send(
                embed=success_embed("로그 전송", "DM으로 로그를 전송했습니다."), ephemeral=True
            )
        except discord.Forbidden:
            await interaction.followup.send(
                embed=error_embed("전송 실패", "DM을 보낼 수 없습니다."), ephemeral=True
            )

    # ── /admin entries ──────────────────────────────────────
    @admin.command(name="edit_entry", description="전체 엔트리 편집")
    async def admin_edit(self, interaction: discord.Interaction):
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
    @admin.command(name="edit_files", description="전체 파일 편집")
    async def admin_files(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            return await interaction.response.send_message(
                embed=error_embed("권한 없음", "어드민만 사용할 수 있습니다."), ephemeral=True
            )
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

    # ── /admin hide ──────────────────────────────────────
    @admin.command(name="hide_entry", description="엔트리 숨기기/보이기")
    async def admin_hide(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            return await interaction.response.send_message(
                embed=error_embed("권한 없음", "어드민만 사용할 수 있습니다."), ephemeral=True
            )
        books = await self.bot.db.list_all_books(include_hidden=True)
        if not books:
            return await interaction.response.send_message(
                embed=info_embed("숨기기", "등록된 엔트리가 없습니다."), ephemeral=True
            )
        view = AdminHideView(self.bot, books)
        await interaction.response.send_message(
            embed=info_embed("숨기기", "숨기거나 다시 보이게 할 엔트리를 선택하세요."),
            view=view, ephemeral=True,
        )
        view._message_ref = await interaction.original_response()

    # ── /admin add ───────────────────────────────────────
    @admin.command(name="add_page", description="페이지 추가")
    async def admin_add(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            return await interaction.response.send_message(
                embed=error_embed("권한 없음", "어드민만 사용할 수 있습니다."), ephemeral=True
            )
        modal = PageModal()
        await interaction.response.send_modal(modal)
        await modal.wait()
        if not modal.submitted:
            return
        title = modal.page_title.value.strip()
        sort_order = int(modal.page_order.value.strip() or "0")
        page_id = await self.bot.db.create_page(title, sort_order)
        await modal.interaction.followup.send(
            embed=success_embed("페이지 추가", f"**{title}** (ID: {page_id})"), ephemeral=True
        )

    # ── /admin pages ─────────────────────────────────────
    @admin.command(name="pages", description="페이지 관리 (편집/삭제)")
    async def admin_pages(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            return await interaction.response.send_message(
                embed=error_embed("권한 없음", "어드민만 사용할 수 있습니다."), ephemeral=True
            )
        pages = await self.bot.db.list_pages(include_hidden=True)
        if not pages:
            return await interaction.response.send_message(
                embed=info_embed("페이지 관리", "등록된 페이지가 없습니다.\n`/admin add`로 페이지를 추가하세요."),
                ephemeral=True
            )
        view = AdminPagesView(self.bot, pages)
        await interaction.response.send_message(
            embed=info_embed("페이지 관리", "편집 또는 삭제할 페이지를 선택하세요."),
            view=view, ephemeral=True,
        )
        view._message_ref = await interaction.original_response()

    # ── /admin page ──────────────────────────────────────
    @admin.command(name="page", description="엔트리 페이지 배정")
    async def admin_page(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            return await interaction.response.send_message(
                embed=error_embed("권한 없음", "어드민만 사용할 수 있습니다."), ephemeral=True
            )
        books = await self.bot.db.list_all_books()
        if not books:
            return await interaction.response.send_message(
                embed=info_embed("페이지 배정", "등록된 엔트리가 없습니다."), ephemeral=True
            )
        pages = await self.bot.db.list_pages(include_hidden=True)
        if not pages:
            return await interaction.response.send_message(
                embed=info_embed("페이지 배정", "페이지가 없습니다.\n`/admin add`로 페이지를 먼저 추가하세요."),
                ephemeral=True
            )
        view = AdminPageAssignView(self.bot, books, pages)
        await interaction.response.send_message(
            embed=info_embed("페이지 배정", "엔트리를 선택하세요."),
            view=view, ephemeral=True,
        )
        view._message_ref = await interaction.original_response()


# ── 페이지 모달 ─────────────────────────────────────

class PageModal(discord.ui.Modal, title="페이지 추가"):
    page_title = discord.ui.TextInput(
        label="페이지 제목", placeholder="예: 비트코인 필독서", max_length=100)
    page_order = discord.ui.TextInput(
        label="표시 순서 (숫자, 비우면 맨 뒤)", placeholder="1", required=False, max_length=5)

    def __init__(self, default_title: str = "", default_order: str = ""):
        super().__init__(timeout=120)
        self.submitted = False
        self.interaction = None
        if default_title:
            self.page_title.default = default_title
        if default_order:
            self.page_order.default = default_order

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        self.submitted = True
        self.interaction = interaction
        self.stop()


class PageAssignModal(discord.ui.Modal, title="페이지 배정"):
    sort_order = discord.ui.TextInput(
        label="페이지 내 순서 (숫자, 비우면 맨 뒤)", placeholder="1", required=False, max_length=5)

    def __init__(self):
        super().__init__(timeout=120)
        self.submitted = False
        self.interaction = None

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        self.submitted = True
        self.interaction = interaction
        self.stop()


# ── 페이지 관리 뷰 ──────────────────────────────────

class AdminHideView(BotView):
    def __init__(self, bot, books: list[dict]):
        super().__init__(timeout=120)
        self.bot = bot

        options = []
        for b in books[:25]:
            hidden = b.get("hidden", 0)
            status = "🔒 숨김" if hidden else "📕 공개"
            options.append(discord.SelectOption(
                label=f"{status} {b['title']}"[:100],
                description=f"{b['file_count']}개 파일",
                value=str(b["id"]),
            ))
        select = discord.ui.Select(placeholder="엔트리 선택", options=options)
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        book_id = int(interaction.data["values"][0])
        book = await self.bot.db.get_book(book_id)
        if not book:
            return await interaction.response.send_message(
                embed=error_embed("없음", "해당 엔트리를 찾을 수 없습니다."), ephemeral=True)
        is_hidden = book.get("hidden", 0)
        new_hidden = not is_hidden
        await self.bot.db.set_hidden(book_id, new_hidden)
        status = "숨김" if new_hidden else "공개"
        await interaction.response.edit_message(
            embed=success_embed("변경 완료", f"**{book['title']}** → {status}"),
            view=None)
        self.stop()


class PageHideConfirmView(BotView):
    def __init__(self, bot, page: dict, entry_count: int):
        super().__init__(timeout=30)
        self.bot = bot
        self.page = page
        self.entry_count = entry_count

    @discord.ui.button(label="숨기기 (미배정으로 이동)", style=discord.ButtonStyle.danger, row=0)
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.bot.db.unassign_page_books(self.page["id"])
        await self.bot.db.set_page_hidden(self.page["id"], True)
        await interaction.response.edit_message(
            embed=success_embed("변경 완료", f"**{self.page['title']}** → 숨김\n엔트리 {self.entry_count}개가 미배정으로 이동됨"),
            view=None)
        self.stop()

    @discord.ui.button(label="취소", style=discord.ButtonStyle.secondary, row=0)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            embed=info_embed("취소됨", "페이지 숨기기가 취소되었습니다."),
            view=None)
        self.stop()


class AdminPagesView(BotView):
    def __init__(self, bot, pages: list[dict]):
        super().__init__(timeout=120)
        self.bot = bot

        options = []
        for p in pages[:25]:
            desc = f"순서: {p['sort_order']}" if p['sort_order'] else "순서 미지정"
            options.append(discord.SelectOption(
                label=p["title"][:100], description=desc, value=str(p["id"]), emoji="📄",
            ))
        select = discord.ui.Select(placeholder="페이지 선택", options=options)
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        page_id = int(interaction.data["values"][0])
        page = await self.bot.db.get_page(page_id)
        if not page:
            return await interaction.response.send_message(
                embed=error_embed("없음", "해당 페이지를 찾을 수 없습니다."), ephemeral=True)
        view = AdminPageActionView(self.bot, page)
        view._message_ref = getattr(self, "_message_ref", None)
        await interaction.response.edit_message(
            embed=info_embed("페이지 관리", f"**{page['title']}** (순서: {page['sort_order']})"),
            view=view,
        )


class AdminPageActionView(BotView):
    def __init__(self, bot, page: dict):
        super().__init__(timeout=120)
        self.bot = bot
        self.page = page

    @discord.ui.button(label="편집", style=discord.ButtonStyle.primary, row=0)
    async def edit_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = PageModal(self.page["title"], str(self.page["sort_order"]))
        await interaction.response.send_modal(modal)
        await modal.wait()
        if not modal.submitted:
            return
        title = modal.page_title.value.strip()
        sort_order = int(modal.page_order.value.strip() or "0")
        await self.bot.db.update_page(self.page["id"], title, sort_order)
        await modal.interaction.edit_original_response(
            embed=success_embed("편집 완료", f"**{title}** (순서: {sort_order})"), view=None)
        self.stop()

    @discord.ui.button(label="숨기기", style=discord.ButtonStyle.danger, row=0)
    async def hide_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        is_hidden = self.page.get("hidden", 0)
        if is_hidden:
            await self.bot.db.set_page_hidden(self.page["id"], False)
            await interaction.response.edit_message(
                embed=success_embed("변경 완료", f"**{self.page['title']}** → 공개"),
                view=None)
            self.stop()
            return
        books = await self.bot.db.list_all_books(include_hidden=True)
        page_books = [b for b in books if b.get("page_id") == self.page["id"]]
        if page_books:
            view = PageHideConfirmView(self.bot, self.page, len(page_books))
            view._message_ref = getattr(self, "_message_ref", None)
            await interaction.response.edit_message(
                embed=info_embed("확인", f"이 페이지에 엔트리 {len(page_books)}개가 있습니다.\n숨기면 해당 엔트리들은 미배정으로 이동됩니다."),
                view=view)
        else:
            await self.bot.db.set_page_hidden(self.page["id"], True)
            await interaction.response.edit_message(
                embed=success_embed("변경 완료", f"**{self.page['title']}** → 숨김"),
                view=None)
        self.stop()

    @discord.ui.button(label="← 돌아가기", style=discord.ButtonStyle.secondary, row=0)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        pages = await self.bot.db.list_pages(include_hidden=True)
        view = AdminPagesView(self.bot, pages)
        view._message_ref = getattr(self, "_message_ref", None)
        await interaction.response.edit_message(
            embed=info_embed("페이지 관리", "편집 또는 삭제할 페이지를 선택하세요."), view=view)


# ── 페이지 배정 뷰 ──────────────────────────────────

class AdminPageAssignView(BotView):
    def __init__(self, bot, books: list[dict], pages: list[dict]):
        super().__init__(timeout=120)
        self.bot = bot
        self.pages = pages

        options = []
        for b in books[:25]:
            page_info = f"페이지 {b.get('page_id', 0)}" if b.get('page_id') else "미배정"
            options.append(discord.SelectOption(
                label=b["title"][:100], description=page_info, value=str(b["id"]), emoji="📕",
            ))
        select = discord.ui.Select(placeholder="엔트리 선택", options=options)
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        book_id = int(interaction.data["values"][0])
        view = AdminPageSelectView(self.bot, book_id, self.pages)
        view._message_ref = getattr(self, "_message_ref", None)
        await interaction.response.edit_message(
            embed=info_embed("페이지 배정", "배정할 페이지를 선택하세요."), view=view)


class AdminPageSelectView(BotView):
    def __init__(self, bot, book_id: int, pages: list[dict]):
        super().__init__(timeout=120)
        self.bot = bot
        self.book_id = book_id

        options = [discord.SelectOption(label="미배정", value="0", emoji="❌")]
        for p in pages[:24]:
            options.append(discord.SelectOption(
                label=p["title"][:100], value=str(p["id"]), emoji="📄",
            ))
        select = discord.ui.Select(placeholder="페이지 선택", options=options)
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        page_id = int(interaction.data["values"][0])
        if page_id == 0:
            await self.bot.db.assign_book_page(self.book_id, 0, 0)
            await interaction.response.edit_message(
                embed=success_embed("배정 해제", "미배정으로 변경됨"), view=None)
            self.stop()
            return
        modal = PageAssignModal()
        await interaction.response.send_modal(modal)
        await modal.wait()
        if not modal.submitted:
            return
        sort_order = int(modal.sort_order.value.strip() or "0")
        await self.bot.db.assign_book_page(self.book_id, page_id, sort_order)
        page = await self.bot.db.get_page(page_id)
        page_title = page["title"] if page else f"ID {page_id}"
        await modal.interaction.edit_original_response(
            embed=success_embed("배정 완료", f"**{page_title}** 페이지, 순서 {sort_order}"),
            view=None)
        self.stop()


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
        from library.cogs.commands import EditEntryModal
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

    @discord.ui.button(label="숨기기", style=discord.ButtonStyle.danger, row=0)
    async def hide_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        is_hidden = self.book.get("hidden", 0)
        await self.bot.db.set_hidden(self.book["id"], not is_hidden)
        status = "숨김" if not is_hidden else "공개"
        await interaction.response.edit_message(
            embed=success_embed("변경 완료", f"**{self.book['title']}** → {status}"),
            view=None,
        )
        self.stop()

    @discord.ui.button(label="← 돌아가기", style=discord.ButtonStyle.secondary, row=0)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        books = await self.bot.db.list_all_books(include_hidden=True)
        view = AdminEntriesView(self.bot, books)
        view._message_ref = getattr(self, "_message_ref", None)
        await interaction.response.edit_message(
            embed=info_embed("엔트리 관리", "편집 또는 삭제할 엔트리를 선택하세요."), view=view,
        )


class AdminFileEntryView(BotView):
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
        from library.cogs.commands import EditFileModal
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

    @discord.ui.button(label="숨기기", style=discord.ButtonStyle.danger, row=0)
    async def hide_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        is_hidden = self.file_info.get("hidden", 0)
        await self.bot.db.set_file_hidden(self.file_info["id"], not is_hidden)
        status = "숨김" if not is_hidden else "공개"
        await interaction.response.edit_message(
            embed=success_embed("변경 완료", f"**{self.file_info['title']}** → {status}"),
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
