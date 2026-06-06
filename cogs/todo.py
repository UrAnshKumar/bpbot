import discord
from discord import app_commands
from discord.ext import commands
import logging
import io
import math
from PIL import Image, ImageDraw

import database
from cogs.pomodoro import (
    make_gradient_bg,
    draw_glass_panel,
    fetch_avatar,
    crop_circle,
    bold,
    regular,
    TEXT_W,
    TEXT_M,
    GOLD
)

logger = logging.getLogger("todo")

TASKS_PER_PAGE = 8

async def generate_todo_card(member: discord.Member, tasks: list, page: int = 1) -> discord.File:
    W, H = 1080, 1080
    
    # ── Background & Panel ──────────────────────────────────────────────
    canvas = make_gradient_bg(W, H)
    PX1, PY1, PX2, PY2 = 40, 40, W - 40, H - 40
    canvas = draw_glass_panel(canvas, PX1, PY1, PX2, PY2, radius=24)
    draw = ImageDraw.Draw(canvas)
    
    # ── Fonts ─────────────────────────────────────────────────────────
    f_title = bold(54)
    f_sub   = regular(24)
    f_task  = bold(28)
    f_num   = bold(24)
    
    # ── Header ────────────────────────────────────────────────────────
    title_text = "TO-DO LIST"
    title_bb = draw.textbbox((0, 0), title_text, font=f_title)
    title_w = title_bb[2] - title_bb[0]
    draw.text((W // 2 - title_w // 2, PY1 + 40), title_text, font=f_title, fill=TEXT_W)
    
    user_text = member.display_name.upper()
    user_bb = draw.textbbox((0, 0), user_text, font=f_sub)
    user_w = user_bb[2] - user_bb[0]
    draw.text((W // 2 - user_w // 2, PY1 + 100), user_text, font=f_sub, fill=TEXT_M)
    
    draw.line(
        [(W // 2 - 300, PY1 + 150), (W // 2 + 300, PY1 + 150)],
        fill=(*GOLD, 160), width=3
    )
    
    # ── Avatar ────────────────────────────────────────────────────────
    avatar_size = 120
    pfp = await fetch_avatar(member)
    pfp_circle = crop_circle(pfp, avatar_size)
    pfp_x = W // 2 - avatar_size // 2
    pfp_y = PY1 + 180
    
    canvas.alpha_composite(pfp_circle, (pfp_x, pfp_y))
    
    draw = ImageDraw.Draw(canvas)
    draw.ellipse(
        [pfp_x - 4, pfp_y - 4, pfp_x + avatar_size + 4, pfp_y + avatar_size + 4],
        outline=(*GOLD, 220), width=4
    )
    
    # ── Tasks List ────────────────────────────────────────────────────
    total_pages = max(1, math.ceil(len(tasks) / TASKS_PER_PAGE))
    page = max(1, min(page, total_pages))
    
    start_idx = (page - 1) * TASKS_PER_PAGE
    page_tasks = tasks[start_idx:start_idx + TASKS_PER_PAGE]
    
    list_start_y = pfp_y + avatar_size + 60
    row_y = list_start_y
    row_height = 60
    row_width = 800
    start_x = W // 2 - row_width // 2
    
    if not page_tasks:
        msg = "No tasks yet! Add some to get started."
        msg_bb = draw.textbbox((0,0), msg, font=f_sub)
        msg_w = msg_bb[2] - msg_bb[0]
        draw.text((W // 2 - msg_w // 2, row_y + 40), msg, font=f_sub, fill=TEXT_M)
    
    for i, task_data in enumerate(page_tasks):
        actual_num = start_idx + i + 1
        task_text = task_data["task"]
        completed = task_data["completed"] == 1
        
        # Row background
        draw.rounded_rectangle(
            [start_x, row_y, start_x + row_width, row_y + row_height],
            radius=12, fill=(255, 255, 255, 10 if not completed else 5)
        )
        
        # Number
        draw.text((start_x + 24, row_y + 12), f"#{actual_num}", font=f_num, fill=(*GOLD, 200))
        
        # Task Text
        color = TEXT_W if not completed else (150, 150, 150, 255)
        draw.text((start_x + 80, row_y + 14), task_text[:40] + ("..." if len(task_text) > 40 else ""), font=f_task, fill=color)
        
        # Checkbox
        box_size = 32
        box_x = start_x + row_width - box_size - 20
        box_y = row_y + (row_height - box_size) // 2
        
        draw.rounded_rectangle(
            [box_x, box_y, box_x + box_size, box_y + box_size],
            radius=6, fill=(255,255,255,20), outline=(*GOLD, 200), width=2
        )
        
        if completed:
            # Draw Checkmark
            draw.line(
                [(box_x + 6, box_y + 16), (box_x + 14, box_y + 24), (box_x + 26, box_y + 8)],
                fill=(107, 203, 119, 255), width=4
            )
            # Strikethrough
            draw.line(
                [(start_x + 80, row_y + row_height // 2), (start_x + 70 + len(task_text[:40])*14, row_y + row_height // 2)],
                fill=(150, 150, 150, 255), width=2
            )
        
        row_y += row_height + 14
        
    # Pagination info
    page_text = f"Page {page} of {total_pages}"
    pb = draw.textbbox((0,0), page_text, font=f_sub)
    pw = pb[2] - pb[0]
    draw.text((W // 2 - pw // 2, H - 90), page_text, font=f_sub, fill=TEXT_M)

    # ── Save ──────────────────────────────────────────────────────────
    fp = io.BytesIO()
    canvas.save(fp, format="PNG")
    fp.seek(0)
    return discord.File(fp, filename="todo.png")


# ─── Modals ───────────────────────────────────────────────────────────────────

class SingleTodoModal(discord.ui.Modal, title="Add Task"):
    task = discord.ui.TextInput(
        label="Task Name",
        style=discord.TextStyle.short,
        placeholder="e.g. Read Chapter 4",
        required=True,
        max_length=100
    )

    async def on_submit(self, interaction: discord.Interaction):
        database.add_todo(interaction.user.id, self.task.value)
        await interaction.response.send_message(f"✅ Added task: **{self.task.value}**", ephemeral=True)

class BulkTodoModal(discord.ui.Modal, title="Add Multiple Tasks"):
    tasks = discord.ui.TextInput(
        label="Tasks (One per line)",
        style=discord.TextStyle.paragraph,
        placeholder="Task 1\nTask 2\nTask 3",
        required=True,
        max_length=1000
    )

    async def on_submit(self, interaction: discord.Interaction):
        lines = [line.strip() for line in self.tasks.value.split('\n') if line.strip()]
        if lines:
            database.add_bulk_todos(interaction.user.id, lines)
            await interaction.response.send_message(f"✅ Added {len(lines)} tasks!", ephemeral=True)
        else:
            await interaction.response.send_message("❌ No valid tasks found.", ephemeral=True)


# ─── Views ────────────────────────────────────────────────────────────────────

class TodoSelectView(discord.ui.View):
    def __init__(self, tasks: list, action: str):
        super().__init__(timeout=60)
        self.action = action
        
        options = []
        for task in tasks:
            status = "✅" if task["completed"] else "❌"
            label = f"{status} {task['task'][:90]}"
            options.append(discord.SelectOption(label=label, value=str(task["id"])))
            
        select = discord.ui.Select(
            placeholder="Select a task...",
            min_values=1,
            max_values=1,
            options=options
        )
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        task_id = int(self.children[0].values[0])
        
        if self.action == "toggle":
            database.toggle_todo(task_id, interaction.user.id)
            await interaction.response.send_message("✅ Task status updated!", ephemeral=True)
        elif self.action == "delete":
            database.delete_todo(task_id, interaction.user.id)
            await interaction.response.send_message("🗑️ Task deleted!", ephemeral=True)
            
        self.stop()

class TodoMainView(discord.ui.View):
    def __init__(self, member: discord.Member, current_page: int, total_pages: int):
        super().__init__(timeout=180)
        self.member = member
        self.current_page = current_page
        self.total_pages = total_pages
        
        # Update button states based on pagination
        self.prev_btn.disabled = self.current_page <= 1
        self.next_btn.disabled = self.current_page >= self.total_pages

    async def update_message(self, interaction: discord.Interaction):
        tasks = database.get_todos(self.member.id)
        self.total_pages = max(1, math.ceil(len(tasks) / TASKS_PER_PAGE))
        self.current_page = max(1, min(self.current_page, self.total_pages))
        
        self.prev_btn.disabled = self.current_page <= 1
        self.next_btn.disabled = self.current_page >= self.total_pages
        
        file = await generate_todo_card(self.member, tasks, self.current_page)
        
        content = f"📝 **{self.member.display_name}'s To-Do List**"
        
        await interaction.response.edit_message(content=content, attachments=[file], view=self, embed=None)

    @discord.ui.button(label="Check Todo", style=discord.ButtonStyle.success, custom_id="todo_check")
    async def check_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.member.id:
            return await interaction.response.send_message("❌ This is not your todo list!", ephemeral=True)
            
        tasks = database.get_todos(self.member.id)
        if not tasks:
            return await interaction.response.send_message("You have no tasks to check!", ephemeral=True)
            
        view = TodoSelectView(tasks, "toggle")
        await interaction.response.send_message("Select a task to toggle completion:", view=view, ephemeral=True)

    @discord.ui.button(label="Delete Todo", style=discord.ButtonStyle.danger, custom_id="todo_delete")
    async def delete_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.member.id:
            return await interaction.response.send_message("❌ This is not your todo list!", ephemeral=True)
            
        tasks = database.get_todos(self.member.id)
        if not tasks:
            return await interaction.response.send_message("You have no tasks to delete!", ephemeral=True)
            
        view = TodoSelectView(tasks, "delete")
        await interaction.response.send_message("Select a task to delete:", view=view, ephemeral=True)

    @discord.ui.button(label="Add Todo", style=discord.ButtonStyle.primary, custom_id="todo_add")
    async def add_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.member.id:
            return await interaction.response.send_message("❌ This is not your todo list!", ephemeral=True)
            
        await interaction.response.send_modal(SingleTodoModal())

    @discord.ui.button(label="◀️ Prev", style=discord.ButtonStyle.secondary, custom_id="todo_prev", row=1)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.member.id:
            return await interaction.response.send_message("❌ This is not your todo list!", ephemeral=True)
            
        self.current_page -= 1
        await self.update_message(interaction)

    @discord.ui.button(label="▶️ Next", style=discord.ButtonStyle.secondary, custom_id="todo_next", row=1)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.member.id:
            return await interaction.response.send_message("❌ This is not your todo list!", ephemeral=True)
            
        self.current_page += 1
        await self.update_message(interaction)
        
    @discord.ui.button(label="🔄 Refresh", style=discord.ButtonStyle.secondary, custom_id="todo_refresh", row=1)
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.member.id:
            return await interaction.response.send_message("❌ This is not your todo list!", ephemeral=True)
        await self.update_message(interaction)


# ─── Cog ──────────────────────────────────────────────────────────────────────

class Todo(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="todo", description="View your personal To-Do list.")
    async def todo_view(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            tasks = database.get_todos(interaction.user.id)
            total_pages = max(1, math.ceil(len(tasks) / TASKS_PER_PAGE))
            
            file = await generate_todo_card(interaction.user, tasks, 1)
            
            content = f"📝 **{interaction.user.display_name}'s To-Do List**"
            
            view = TodoMainView(interaction.user, 1, total_pages)
            await interaction.followup.send(content=content, file=file, view=view)
        except Exception as e:
            logger.error(f"Error in /todo: {e}")
            await interaction.followup.send(f"❌ Failed to load todo list: {e}")

    @app_commands.command(name="add_todo", description="Add a single task to your To-Do list.")
    async def add_todo_cmd(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SingleTodoModal())

    @app_commands.command(name="full_todo", description="Add multiple tasks at once to your To-Do list.")
    async def full_todo_cmd(self, interaction: discord.Interaction):
        await interaction.response.send_modal(BulkTodoModal())


async def setup(bot: commands.Bot):
    await bot.add_cog(Todo(bot))
