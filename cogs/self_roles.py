import discord
from discord.ext import commands
from discord import app_commands
import database
import logging

logger = logging.getLogger("StudyBot")

class SelfRoleButton(discord.ui.Button):
    def __init__(self, role_id, label, emoji):
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label=label,
            emoji=emoji if emoji else None,
            custom_id=f"selfrole:{role_id}"
        )
        self.role_id = role_id

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        member = interaction.user
        role = guild.get_role(self.role_id)
        
        if not role:
            await interaction.response.send_message("❌ This role no longer exists in the server.", ephemeral=True)
            return
            
        if role in member.roles:
            try:
                await member.remove_roles(role, reason="Self-assignable role removal.")
                await interaction.response.send_message(f"✅ Removed role **{role.name}** from your profile!", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("❌ I do not have permissions to remove this role from you. Please check my role position.", ephemeral=True)
        else:
            try:
                await member.add_roles(role, reason="Self-assignable role assignment.")
                await interaction.response.send_message(f"✅ Added role **{role.name}** to your profile!", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("❌ I do not have permissions to assign this role to you. Please check my role position.", ephemeral=True)

class SelfRolesView(discord.ui.View):
    def __init__(self, roles):
        super().__init__(timeout=None)
        for role_info in roles:
            self.add_item(SelfRoleButton(
                role_id=role_info['role_id'],
                label=role_info['label'],
                emoji=role_info['emoji']
            ))

class SelfRolesCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        try:
            conn = database.get_db_connection()
            guild_rows = conn.execute("SELECT DISTINCT guild_id FROM self_roles").fetchall()
            conn.close()
            
            count = 0
            for row in guild_rows:
                guild_id = row['guild_id']
                roles = database.get_self_roles(guild_id)
                if roles:
                    self.bot.add_view(SelfRolesView(roles))
                    count += 1
            logger.info(f"Registered {count} persistent self-role views.")
        except Exception as e:
            logger.error(f"Failed to register self-role views on startup: {e}")

    @app_commands.command(name="selfrole-add", description="Add a role to the self-assignable roles list.")
    @app_commands.describe(
        role="The role to make self-assignable",
        label="The button label for this role",
        emoji="Optional emoji for the button"
    )
    @app_commands.default_permissions(manage_roles=True)
    async def selfrole_add(self, interaction: discord.Interaction, role: discord.Role, label: str, emoji: str = None):
        if not interaction.guild:
            await interaction.response.send_message("❌ This command can only be used in a server!", ephemeral=True)
            return

        database.add_self_role(interaction.guild.id, role.id, emoji, label)
        
        # Re-register view
        roles = database.get_self_roles(interaction.guild.id)
        self.bot.add_view(SelfRolesView(roles))
        
        embed = discord.Embed(
            title="🌈 Self-Role Added",
            description=f"Role {role.mention} has been added as self-assignable.\n"
                        f"**Label:** {label}\n"
                        f"**Emoji:** {emoji if emoji else 'None'}",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="selfrole-remove", description="Remove a role from the self-assignable roles list.")
    @app_commands.describe(role="The role to remove from self-assignable list")
    @app_commands.default_permissions(manage_roles=True)
    async def selfrole_remove(self, interaction: discord.Interaction, role: discord.Role):
        if not interaction.guild:
            await interaction.response.send_message("❌ This command can only be used in a server!", ephemeral=True)
            return

        database.delete_self_role(interaction.guild.id, role.id)
        
        # Re-register view
        roles = database.get_self_roles(interaction.guild.id)
        self.bot.add_view(SelfRolesView(roles))
        
        embed = discord.Embed(
            title="🗑️ Self-Role Removed",
            description=f"Role {role.mention} was removed from the self-assignable roles list.",
            color=discord.Color.orange()
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="roles-panel", description="Send the interactive panel for self-assignable roles.")
    @app_commands.describe(channel="The channel to send the panel to (optional)")
    @app_commands.default_permissions(manage_roles=True)
    async def roles_panel(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        if not interaction.guild:
            await interaction.response.send_message("❌ This command can only be used in a server!", ephemeral=True)
            return

        target_channel = channel or interaction.channel
        roles = database.get_self_roles(interaction.guild.id)
        
        if not roles:
            await interaction.response.send_message("❌ No self-assignable roles configured for this server. Use `/selfrole-add` first.", ephemeral=True)
            return
            
        embed = discord.Embed(
            title="🌈 Self-Assignable Roles",
            description="Click the buttons below to assign or remove roles from your profile!",
            color=discord.Color.purple()
        )
        
        view = SelfRolesView(roles)
        
        try:
            await target_channel.send(embed=embed, view=view)
            await interaction.response.send_message(f"✅ Roles panel sent to {target_channel.mention}!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(f"❌ I do not have permission to send messages in {target_channel.mention}!", ephemeral=True)

async def setup(bot):
    await bot.add_cog(SelfRolesCog(bot))
