"""
/library 명령어 그룹: upload, list, download
"""

import os
import uuid
import discord
from discord import app_commands
from discord.ext import commands

from config import UPLOAD_DIR, MAX_FILE_SIZE, LIGHTNING_ADDRESS
from utils import success_embed, error_embed, info_embed, file_size_fmt, BotView


# ── 모달 ──────────────────────────────────────────────

class EntryModal(discord.ui.Modal, title="새 엔트리 생성"):
    """엔트리 정보 (5필드)"""
    book_title = discord.ui.TextInput(
        label="엔트리 제목",
        placeholder="제목",
        max_length=100,
    )
    book_alias = discord.ui.TextInput(
        label="엔트리 별칭 (영어 제목, 줄임말 등)",
        placeholder="쉼표로 구분",
        required=False,
        max_length=200,
    )
    book_author = discord.ui.TextInput(
        label="저자",
        placeholder="저자",
        required=False,
        max_length=100,
    )
    book_author_alias = discord.ui.TextInput(
        label="저자 별칭 (영어 이름, 필명 등)",
        placeholder="쉼표로 구분",
        required=False,
        max_length=200,
    )
    book_desc = discord.ui.TextInput(
        label="설명",
        style=discord.TextStyle.paragraph,
        placeholder="해당 엔트리에 대한 간단한 설명",
        required=False,
        max_length=500,
    )

    def __init__(self, default_name: str = ""):
        super().__init__(timeout=120)
        self.submitted = False
        self.interaction = None
        if default_name:
            self.book_title.default = default_name

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        self.submitted = True
        self.interaction = interaction
        self.stop()


class FileInfoModal(discord.ui.Modal, title="파일 정보 입력"):
    """파일 제목/설명 (2필드) - 기존 엔트리에 추가할 때"""
    file_title = discord.ui.TextInput(
        label="파일 제목",
        placeholder="파일 제목",
        max_length=100,
    )
    file_desc = discord.ui.TextInput(
        label="파일 설명",
        style=discord.TextStyle.paragraph,
        placeholder="해당 파일에 대한 간단한 설명",
        max_length=500,
    )

    def __init__(self, default_name: str = ""):
        super().__init__(timeout=120)
        self.submitted = False
        self.interaction = None
        if default_name:
            self.file_title.default = default_name

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        self.submitted = True
        self.interaction = interaction
        self.stop()


class EditEntryModal(discord.ui.Modal, title="엔트리 편집"):
    book_title = discord.ui.TextInput(label="엔트리 제목", max_length=100)
    book_alias = discord.ui.TextInput(
        label="엔트리 별칭 (영어 제목, 줄임말 등)",
        placeholder="쉼표로 구분", required=False, max_length=200,
    )
    book_author = discord.ui.TextInput(label="저자", required=False, max_length=100)
    book_author_alias = discord.ui.TextInput(
        label="저자 별칭 (영어 이름, 필명 등)",
        placeholder="쉼표로 구분", required=False, max_length=200,
    )
    book_desc = discord.ui.TextInput(
        label="설명", style=discord.TextStyle.paragraph, required=False, max_length=500,
    )

    def __init__(self, book: dict):
        super().__init__(timeout=120)
        self.submitted = False
        self.interaction = None
        self.book_title.default = book.get("title") or ""
        self.book_alias.default = book.get("alias") or ""
        self.book_author.default = book.get("author") or ""
        self.book_author_alias.default = book.get("author_alias") or ""
        self.book_desc.default = book.get("description") or ""

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        self.submitted = True
        self.interaction = interaction
        self.stop()


class EditFileModal(discord.ui.Modal, title="파일 편집"):
    file_title = discord.ui.TextInput(label="파일 제목", max_length=100)
    file_desc = discord.ui.TextInput(
        label="파일 설명", style=discord.TextStyle.paragraph, required=False, max_length=500,
    )

    def __init__(self, file_info: dict):
        super().__init__(timeout=120)
        self.submitted = False
        self.interaction = None
        self.file_title.default = file_info.get("title") or ""
        self.file_desc.default = file_info.get("description") or ""

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        self.submitted = True
        self.interaction = interaction
        self.stop()


# ── 뷰 ───────────────────────────────────────────────

class EditEntriesView(BotView):
    """자신이 만든 엔트리 편집 드롭다운"""
    def __init__(self, bot, books: list[dict]):
        super().__init__(timeout=120)
        self.bot = bot

        options = []
        for b in books[:25]:
            desc = (b.get("description") or "")[:100]
            options.append(discord.SelectOption(
                label=b["title"][:100],
                description=f"{b['file_count']}개 파일 | {desc}"[:100] if desc else f"{b['file_count']}개 파일",
                value=str(b["id"]),
                emoji="📕",
            ))
        select = discord.ui.Select(placeholder="편집할 엔트리 선택", options=options)
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        book_id = int(interaction.data["values"][0])
        book = await self.bot.db.get_book(book_id)
        if not book:
            return await interaction.response.send_message(
                embed=error_embed("엔트리 없음", "해당 엔트리를 찾을 수 없습니다."), ephemeral=True,
            )

        modal = EditEntryModal(book)
        await interaction.response.send_modal(modal)
        await modal.wait()
        if not modal.submitted:
            return

        await self.bot.db.update_book(
            book_id=book_id,
            title=modal.book_title.value.strip(),
            alias=modal.book_alias.value.strip() or None,
            author=modal.book_author.value.strip() or None,
            author_alias=modal.book_author_alias.value.strip() or None,
            description=modal.book_desc.value.strip() or None,
        )

        await modal.interaction.edit_original_response(
            embed=success_embed("편집 완료", f"**{modal.book_title.value.strip()}** 엔트리가 수정되었습니다."),
            view=None,
        )
        self.stop()


class EditFilesView(BotView):
    """자신이 올린 파일 편집 드롭다운"""
    def __init__(self, bot, files: list[dict]):
        super().__init__(timeout=120)
        self.bot = bot

        options = []
        for f in files[:25]:
            ext = os.path.splitext(f["filename"])[1] or ""
            options.append(discord.SelectOption(
                label=f"{f['title']}{ext} ({file_size_fmt(f['file_size'])})"[:100],
                description=f["book_title"][:100] if f.get("book_title") else "",
                value=str(f["id"]),
                emoji="💾",
            ))
        select = discord.ui.Select(placeholder="편집할 파일 선택", options=options)
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        file_id = int(interaction.data["values"][0])
        file_info = await self.bot.db.get_file(file_id)
        if not file_info:
            return await interaction.response.send_message(
                embed=error_embed("파일 없음", "해당 파일을 찾을 수 없습니다."), ephemeral=True,
            )

        modal = EditFileModal(file_info)
        await interaction.response.send_modal(modal)
        await modal.wait()
        if not modal.submitted:
            return

        ext = os.path.splitext(file_info["filename"])[1] or ""
        new_filename = modal.file_title.value.strip() + ext

        await self.bot.db.update_file(
            file_id=file_id,
            title=modal.file_title.value.strip(),
            description=modal.file_desc.value.strip(),
            filename=new_filename,
        )

        await modal.interaction.edit_original_response(
            embed=success_embed("편집 완료", f"**{modal.file_title.value.strip()}{ext}** 파일이 수정되었습니다."),
            view=None,
        )
        self.stop()


class InfoView(BotView):
    """엔트리 선택 → 파일 목록 표시 (공개, 명령어 친 사람만 조작 가능)"""
    def __init__(self, bot, books: list[dict], owner_id: int, public: bool = False):
        super().__init__(timeout=120)
        self.bot = bot
        self.owner_id = owner_id
        self.public = public

        options = []
        for b in books[:25]:
            desc_text = b.get("description") or ""
            options.append(discord.SelectOption(
                label=b["title"][:100],
                description=f"{b['file_count']}개 파일 | {desc_text[:40]}"[:100],
                value=str(b["id"]),
            ))
        select = discord.ui.Select(placeholder="엔트리를 선택하세요", options=options)
        select.callback = self._on_select
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "이 메뉴는 명령어를 사용한 사람만 조작할 수 있습니다.", ephemeral=True
            )
            return False
        return await super().interaction_check(interaction)

    async def _on_select(self, interaction: discord.Interaction):
        selected_id = int(interaction.data["values"][0])
        book = await self.bot.db.get_book(selected_id)
        if not book:
            return await interaction.response.send_message(
                embed=error_embed("엔트리 없음", "해당 엔트리를 찾을 수 없습니다."),
                ephemeral=True,
            )

        files = await self.bot.db.list_book_files(selected_id)

        desc_parts = []
        if book.get("author"):
            desc_parts.append(f"**저자:** {book['author']}")
            desc_parts.append("")
        if book.get("description"):
            desc_parts.append(book["description"])

        if files:
            desc_parts.append("")
            desc_parts.append(f"**파일 ({len(files)}개)**")
            for f in files:
                ext = os.path.splitext(f["filename"])[1] or ""
                line = f"💾 **{f['title']}{ext}** ({file_size_fmt(f['file_size'])})"
                if f.get("description"):
                    line += f"\n{f['description']}"
                desc_parts.append("")
                desc_parts.append(line)
        else:
            desc_parts.append("\n등록된 파일이 없습니다.")

        embed = info_embed(book["title"], "\n".join(desc_parts))
        detail_view = InfoDetailView(self.bot, self.owner_id, files, public=self.public)
        detail_view._message_ref = getattr(self, "_message_ref", None)
        await interaction.response.edit_message(embed=embed, view=detail_view)


class InfoDetailView(BotView):
    """엔트리 상세 - 파일 다운로드 드롭다운 + 목록으로 버튼"""
    def __init__(self, bot, owner_id: int, files: list[dict], public: bool = False):
        super().__init__(timeout=120)
        self.bot = bot
        self.owner_id = owner_id
        self.public = public

        if files:
            options = []
            for f in files[:25]:
                ext = os.path.splitext(f["filename"])[1] or ""
                label = f"{f['title']}{ext} ({file_size_fmt(f['file_size'])})"[:100]
                desc = (f.get("description") or "")[:100]
                options.append(discord.SelectOption(
                    label=label,
                    description=desc,
                    value=str(f["id"]),
                    emoji="💾",
                ))
            select = discord.ui.Select(placeholder="💾 다운로드할 파일 선택", options=options, row=0)
            select.callback = self._on_download
            self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "이 메뉴는 명령어를 사용한 사람만 조작할 수 있습니다.", ephemeral=True
            )
            return False
        return await super().interaction_check(interaction)

    async def _on_download(self, interaction: discord.Interaction):
        file_id = int(interaction.data["values"][0])
        file_info = await self.bot.db.get_file(file_id)
        if not file_info:
            return await interaction.response.send_message(
                embed=error_embed("파일 없음", "해당 파일을 찾을 수 없습니다."),
                ephemeral=True,
            )

        save_path = os.path.join(UPLOAD_DIR, file_info["stored_name"])
        if not os.path.exists(save_path):
            return await interaction.response.send_message(
                embed=error_embed("파일 없음", "로컬 파일이 존재하지 않습니다."),
                ephemeral=True,
            )

        ephemeral = not self.public
        await interaction.response.defer(ephemeral=ephemeral)
        discord_file = discord.File(save_path, filename=file_info["filename"])
        await interaction.followup.send(file=discord_file, ephemeral=ephemeral)
        await self.bot.db.increment_download(file_id)

    @discord.ui.button(label="← 목록으로", style=discord.ButtonStyle.secondary, row=1)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        books = await self.bot.db.list_all_books()
        view = InfoView(self.bot, books, self.owner_id, public=self.public)
        view._message_ref = getattr(self, "_message_ref", None)
        await interaction.response.edit_message(
            embed=info_embed("라이브러리", "엔트리를 선택하면 파일 목록을 확인할 수 있습니다."),
            view=view,
        )


class UploadView(BotView):
    """기존 엔트리 드롭다운 + 새 엔트리 생성 버튼을 동시에 표시"""
    def __init__(self, cog, file: discord.Attachment, books: list[dict], default_name: str = ""):
        super().__init__(timeout=60)
        self.cog = cog
        self.file = file
        self.default_name = default_name

        # 드롭다운 항상 표시
        if books:
            options = []
            for b in books[:25]:
                desc_text = b.get("description") or ""
                options.append(discord.SelectOption(
                    label=b["title"][:100],
                    description=f"{b['file_count']}개 파일 | {desc_text[:40]}"[:100],
                    value=str(b["id"]),
                    emoji="📕",
                ))
            select = discord.ui.Select(placeholder="기존 엔트리에 추가", options=options, row=0)
            select.callback = self._on_select
            self.add_item(select)
        else:
            select = discord.ui.Select(
                placeholder="기존 엔트리가 없습니다.",
                options=[discord.SelectOption(label="없음", value="_")],
                disabled=True, row=0
            )
            self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        selected_id = int(interaction.data["values"][0])

        book = await self.cog.bot.db.get_book(selected_id)
        modal = FileInfoModal(book["title"] if book else "")
        await interaction.response.send_modal(modal)
        await modal.wait()
        if not modal.submitted:
            return

        file_id = await self.cog._save_file(
            book_id=selected_id,
            file=self.file,
            uploader=interaction.user,
            title=modal.file_title.value.strip(),
            description=modal.file_desc.value.strip(),
        )

        embed = success_embed(
            "업로드 완료",
            f"**{book['title']}** 에 파일 추가\n"
            f"파일: {self.file.filename} ({file_size_fmt(self.file.size)})"
        )
        await modal.interaction.edit_original_response(embed=embed, view=None)
        self.stop()

    @discord.ui.button(label="새 엔트리 생성", style=discord.ButtonStyle.primary, row=1)
    async def new_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 1단계: 엔트리 정보 모달
        entry_modal = EntryModal(self.default_name)
        await interaction.response.send_modal(entry_modal)
        await entry_modal.wait()
        if not entry_modal.submitted:
            return

        book_id = await self.cog.bot.db.create_book(
            creator_id=str(interaction.user.id),
            creator_name=interaction.user.display_name,
            title=entry_modal.book_title.value.strip(),
            alias=entry_modal.book_alias.value.strip() or None,
            author=entry_modal.book_author.value.strip() or None,
            author_alias=entry_modal.book_author_alias.value.strip() or None,
            description=entry_modal.book_desc.value.strip() or None,
        )

        # 2단계: 파일 정보 모달 (버튼으로 트리거)
        trigger_view = _FileModalTriggerView(self.cog, self.file, interaction.user, book_id,
                                              entry_modal.book_title.value.strip(),
                                              self.default_name)
        await entry_modal.interaction.edit_original_response(
            embed=info_embed("엔트리 생성 완료", "이제 파일 정보를 입력하세요."),
            view=trigger_view,
        )
        self.stop()



class _FileModalTriggerView(BotView):
    """모달을 띄울 수 없는 상황에서 버튼으로 파일 정보 모달 트리거"""
    def __init__(self, cog, file: discord.Attachment,
                 uploader: discord.User | discord.Member,
                 book_id: int, book_title: str, default_name: str = ""):
        super().__init__(timeout=60)
        self.cog = cog
        self.file = file
        self.uploader = uploader
        self.book_id = book_id
        self.book_title = book_title
        self.default_name = default_name

    @discord.ui.button(label="파일 정보 입력", style=discord.ButtonStyle.primary)
    async def trigger_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = FileInfoModal(self.book_title)
        await interaction.response.send_modal(modal)
        await modal.wait()
        if not modal.submitted:
            return

        file_id = await self.cog._save_file(
            book_id=self.book_id,
            file=self.file,
            uploader=self.uploader,
            title=modal.file_title.value.strip(),
            description=modal.file_desc.value.strip(),
        )

        embed = success_embed(
            "업로드 완료",
            f"**{self.book_title}** 에 파일 등록\n"
            f"파일: {self.file.filename} ({file_size_fmt(self.file.size)})"
        )
        await modal.interaction.edit_original_response(embed=embed, view=None)
        self.stop()


# ── Cog ───────────────────────────────────────────────

class LibraryCog(commands.Cog):
    library = app_commands.Group(name="library", description="파일 라이브러리 관리")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        os.makedirs(UPLOAD_DIR, exist_ok=True)

    @app_commands.command(name="donate", description="⚡ 라이트닝 후원")
    async def donate(self, interaction: discord.Interaction):
        if not LIGHTNING_ADDRESS:
            return await interaction.response.send_message(
                embed=info_embed("⚡ 후원", "후원 주소가 설정되지 않았습니다."),
                ephemeral=True,
            )
        embed = info_embed(
            "⚡ 라이트닝 후원",
            f"시타델 도서관을 후원해주세요!\n\n"
            f"**Lightning Address**\n`{LIGHTNING_ADDRESS}`\n\n"
            f"아무 라이트닝 지갑에서 위 주소로 보내면 됩니다."
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="help", description="명령어 도움말")
    async def help(self, interaction: discord.Interaction):
        embed = info_embed(
            "📕 라이브러리 봇 도움말",
            "**자료 조회**\n"
            "`/library list` - 전체 엔트리 목록\n"
            "`/library info` - 엔트리 상세 조회 및 다운로드\n"
            "`/library share` - 엔트리 정보를 채널에 공유\n"
            "\n**자료 등록**\n"
            "`/library new` - 새 엔트리 생성\n"
            "`/library add` - 파일 업로드\n"
            "\n**편집**\n"
            "`/library edit` - 내가 만든 엔트리 편집\n"
            "`/library files` - 내가 올린 파일 편집\n"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @library.command(name="new", description="새 엔트리 생성")
    async def create_entry(self, interaction: discord.Interaction):
        modal = EntryModal()
        await interaction.response.send_modal(modal)
        await modal.wait()
        if not modal.submitted:
            return

        book_id = await self.bot.db.create_book(
            creator_id=str(interaction.user.id),
            creator_name=interaction.user.display_name,
            title=modal.book_title.value.strip(),
            alias=modal.book_alias.value.strip() or None,
            author=modal.book_author.value.strip() or None,
            author_alias=modal.book_author_alias.value.strip() or None,
            description=modal.book_desc.value.strip() or None,
        )

        embed = success_embed(
            "엔트리 생성 완료",
            f"**{modal.book_title.value.strip()}**\n"
            f"엔트리 ID: `{book_id}`\n"
            f"`/library add`으로 파일을 추가하세요."
        )
        await modal.interaction.followup.send(embed=embed, ephemeral=True)

    async def _save_file(self, book_id: int, file: discord.Attachment,
                         uploader: discord.User | discord.Member,
                         title: str, description: str) -> int:
        """파일을 로컬에 저장하고 DB에 기록. file_id 반환"""
        ext = os.path.splitext(file.filename)[1]
        stored_name = f"{uuid.uuid4().hex}{ext}"
        save_path = os.path.join(UPLOAD_DIR, stored_name)
        await file.save(save_path)

        # 다운로드 파일명: 파일 제목 + 원본 확장자
        download_name = title + ext

        file_id = await self.bot.db.add_file(
            book_id=book_id,
            uploader_id=str(uploader.id),
            uploader_name=uploader.display_name,
            title=title,
            description=description,
            filename=download_name,
            stored_name=stored_name,
            file_size=file.size,
            mime_type=file.content_type,
        )
        return file_id

    @library.command(name="add", description="파일을 라이브러리에 업로드")
    @app_commands.describe(
        file="업로드할 파일 (최대 10MB)",
    )
    async def upload(self, interaction: discord.Interaction,
                     file: discord.Attachment):
        if file.size > MAX_FILE_SIZE:
            return await interaction.response.send_message(
                embed=error_embed("업로드 실패", f"파일 크기가 {file_size_fmt(MAX_FILE_SIZE)} 제한을 초과합니다."),
                ephemeral=True
            )

        books = await self.bot.db.list_all_books()
        view = UploadView(self, file, books)
        await interaction.response.send_message(
            embed=info_embed("업로드", "기존 엔트리를 선택하거나 새 엔트리를 생성하세요."),
            view=view, ephemeral=True
        )
        view._message_ref = await interaction.original_response()

    @library.command(name="info", description="엔트리 상세 정보 조회")
    async def info(self, interaction: discord.Interaction):
        books = await self.bot.db.list_all_books()

        if not books:
            return await interaction.response.send_message(
                embed=info_embed("라이브러리", "등록된 엔트리가 없습니다."),
                ephemeral=True,
            )

        view = InfoView(self.bot, books, interaction.user.id)
        await interaction.response.send_message(
            embed=info_embed("라이브러리", "엔트리를 선택하면 파일 목록을 확인할 수 있습니다."),
            view=view,
            ephemeral=True,
        )
        view._message_ref = await interaction.original_response()

    @library.command(name="share", description="엔트리 상세 정보 공유")
    async def share(self, interaction: discord.Interaction):
        books = await self.bot.db.list_all_books()

        if not books:
            return await interaction.response.send_message(
                embed=info_embed("라이브러리", "등록된 엔트리가 없습니다."),
            )

        view = InfoView(self.bot, books, interaction.user.id, public=True)
        await interaction.response.send_message(
            embed=info_embed("라이브러리", "엔트리를 선택하면 파일 목록을 확인할 수 있습니다."),
            view=view,
        )
        view._message_ref = await interaction.original_response()

    @library.command(name="list", description="라이브러리 엔트리 목록 조회")
    async def list_books(self, interaction: discord.Interaction):
        books = await self.bot.db.list_all_books()

        if not books:
            return await interaction.response.send_message(
                embed=info_embed("라이브러리", "등록된 엔트리가 없습니다."),
            )

        lines = []
        for b in books:
            line = f"📕 **{b['title']}** ({b['file_count']}개 파일)"
            if b.get("description"):
                first_line = b["description"].split("\n")[0]
                line += f"\n> {first_line}"
            lines.append(line)

        embed = info_embed(
            f"라이브러리 ({len(books)}개)",
            "\n\n".join(lines),
        )
        embed.set_footer(text="/library info로 상세 조회 및 다운로드")
        await interaction.response.send_message(embed=embed)

    @library.command(name="edit", description="내가 만든 엔트리 편집")
    async def edit_entries(self, interaction: discord.Interaction):
        books = await self.bot.db.list_books_by_user(str(interaction.user.id))

        if not books:
            return await interaction.response.send_message(
                embed=info_embed("내 엔트리", "내가 만든 엔트리가 없습니다."),
                ephemeral=True,
            )

        view = EditEntriesView(self.bot, books)
        await interaction.response.send_message(
            embed=info_embed("내 엔트리", "편집할 엔트리를 선택하세요."),
            view=view, ephemeral=True,
        )
        view._message_ref = await interaction.original_response()

    @library.command(name="files", description="내가 올린 파일 편집")
    async def edit_files(self, interaction: discord.Interaction):
        files = await self.bot.db.list_files_by_user(str(interaction.user.id))

        if not files:
            return await interaction.response.send_message(
                embed=info_embed("내 파일", "내가 올린 파일이 없습니다."),
                ephemeral=True,
            )

        view = EditFilesView(self.bot, files)
        await interaction.response.send_message(
            embed=info_embed("내 파일", "편집할 파일을 선택하세요."),
            view=view, ephemeral=True,
        )
        view._message_ref = await interaction.original_response()


async def setup(bot: commands.Bot):
    await bot.add_cog(LibraryCog(bot))
