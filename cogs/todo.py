import discord
from discord.ext import commands
from discord import app_commands
import database

class TodoCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # --- Interactive Dropdown View ---
    class TaskDropdown(discord.ui.Select):
        def __init__(self, tasks, parent_cog):
            options = []
            for t in tasks[:25]: # Select menus hold max 25 options
                options.append(discord.SelectOption(
                    label=t['task_text'][:100], 
                    value=str(t['id']), 
                    description="Click to complete this task!"
                ))
            super().__init__(placeholder="Select a task to check off...", options=options)
            self.parent_cog = parent_cog

        async def callback(self, interaction: discord.Interaction):
            task_id = int(self.values[0])
            user_id = interaction.user.id
            
            success = database.complete_task(user_id, task_id)
            if success:
                # Award 20 coins reward for finishing a task
                database.add_coins(user_id, 20)
                await interaction.response.send_message("✅ Task checked off! Earned **20 🪙**!", ephemeral=True)
                # Re-render todo list
                await self.parent_cog.send_todo_panel(interaction, edit=True)
            else:
                await interaction.response.send_message("❌ Task could not be completed or does not belong to you.", ephemeral=True)

    class TodoView(discord.ui.View):
        def __init__(self, tasks, parent_cog):
            super().__init__(timeout=None)
            if tasks:
                self.add_item(TodoCog.TaskDropdown(tasks, parent_cog))

    async def send_todo_panel(self, interaction: discord.Interaction, edit=False):
        user_id = interaction.user.id if hasattr(interaction, "user") else interaction.author.id
        tasks = database.get_user_tasks(user_id)
        
        embed = discord.Embed(
            title=f"📋 Todo List: {interaction.user.display_name}",
            color=discord.Color.blue()
        )
        
        active_tasks = [t for t in tasks if t['status'] == 'todo']
        completed_tasks = [t for t in tasks if t['status'] == 'done']
        
        # Format active tasks
        if active_tasks:
            active_list = []
            for index, t in enumerate(active_tasks):
                active_list.append(f"`{index + 1}` ❌ {t['task_text']}")
            embed.add_field(name="📌 Active Tasks", value="\n".join(active_list), inline=False)
        else:
            embed.add_field(name="📌 Active Tasks", value="No active tasks! Add some with `/todo add`.", inline=False)
            
        # Format completed tasks (limit to last 5 to keep tidy)
        if completed_tasks:
            completed_list = []
            for t in completed_tasks[:5]:
                completed_list.append(f"~~`✓` {t['task_text']}~~")
            embed.add_field(name="✅ Recently Completed", value="\n".join(completed_list), inline=False)
            
        view = self.TodoView(active_tasks, self)
        
        if edit:
            await interaction.message.edit(embed=embed, view=view)
        else:
            await interaction.response.send_message(embed=embed, view=view)

    # --- Commands ---
    @app_commands.command(name="todo-add", description="Add a new task to your personal todo checklist.")
    @app_commands.describe(text="The description of the task you want to complete")
    async def todo_add(self, interaction: discord.Interaction, text: str):
        user_id = interaction.user.id
        database.add_task(user_id, text)
        
        embed = discord.Embed(
            description=f"📌 Added task: **{text}** to your list!",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="todo", description="View and manage your personal todo checklist.")
    async def todo_list(self, interaction: discord.Interaction):
        await self.send_todo_panel(interaction)

    @app_commands.command(name="todo-delete", description="Delete a task from your checklist.")
    @app_commands.describe(task_number="The index of the task (from /todo list)")
    async def todo_delete(self, interaction: discord.Interaction, task_number: int):
        user_id = interaction.user.id
        tasks = database.get_user_tasks(user_id)
        active_tasks = [t for t in tasks if t['status'] == 'todo']
        
        if task_number < 1 or task_number > len(active_tasks):
            await interaction.response.send_message("❌ Invalid task number index!", ephemeral=True)
            return
            
        task = active_tasks[task_number - 1]
        success = database.delete_task(user_id, task['id'])
        
        if success:
            await interaction.response.send_message(f"🗑️ Deleted task: **{task['task_text']}**", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Failed to delete task.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(TodoCog(bot))
