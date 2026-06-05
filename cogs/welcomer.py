import discord
from discord.ext import commands
from discord import app_commands
import database
import logging

logger = logging.getLogger("StudyBot")

class WelcomerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="welcomer-setup", description="Set up the welcome message, channel, and role for new members.")
    @app_commands.describe(
        channel="The channel where welcome messages will be sent",
        message="The message templates. Use {user}, {guild}, or {count}",
        role="The role to assign to new members (optional)"
    )
    @app_commands.default_permissions(manage_guild=True)
    async def welcomer_setup(
        self, 
        interaction: discord.Interaction, 
        channel: discord.TextChannel, 
        message: str, 
        role: discord.Role = None
    ):
        if not interaction.guild:
            await interaction.response.send_message("❌ This command can only be used in a server!", ephemeral=True)
            return

        database.update_guild_config(
            guild_id=interaction.guild.id,
            welcome_channel_id=channel.id,
            welcome_message=message,
            welcome_role_id=role.id if role else None
        )
        
        embed = discord.Embed(
            title="👋 Welcomer Configured",
            description=f"Welcome messages will be sent to {channel.mention}.\n"
                        f"**Role:** {role.mention if role else 'None'}\n"
                        f"**Message template:**\n```\n{message}\n```",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="welcomer-view", description="View the current welcome configuration.")
    @app_commands.default_permissions(manage_guild=True)
    async def welcomer_view(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ This command can only be used in a server!", ephemeral=True)
            return

        config = database.get_guild_config(interaction.guild.id)
        if not config:
            await interaction.response.send_message("❌ Welcomer is not configured yet. Use `/welcomer-setup` to configure it.", ephemeral=True)
            return

        channel = interaction.guild.get_channel(config['welcome_channel_id'])
        channel_mention = channel.mention if channel else f"Deleted Channel (ID: {config['welcome_channel_id']})"
        
        role = interaction.guild.get_role(config['welcome_role_id']) if config['welcome_role_id'] else None
        role_mention = role.mention if role else "None"
        
        embed = discord.Embed(
            title="👋 Welcomer Settings",
            description=f"**Welcome Channel:** {channel_mention}\n"
                        f"**Auto-assign Role:** {role_mention}\n"
                        f"**Message Template:**\n```\n{config['welcome_message']}\n```",
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        # Fetch config
        config = database.get_guild_config(member.guild.id)
        if not config:
            return
            
        welcome_channel_id = config['welcome_channel_id']
        welcome_message = config['welcome_message']
        welcome_role_id = config['welcome_role_id']
        
        # Send message
        if welcome_channel_id:
            channel = member.guild.get_channel(welcome_channel_id)
            if channel:
                # Format template variables
                # {user} -> member.mention
                # {guild} -> member.guild.name
                # {count} -> member.guild.member_count
                formatted_message = welcome_message.replace("{user}", member.mention)
                formatted_message = formatted_message.replace("{guild}", member.guild.name)
                formatted_message = formatted_message.replace("{count}", str(member.guild.member_count))
                
                embed = discord.Embed(
                    title=f"Welcome to {member.guild.name}!",
                    description=formatted_message,
                    color=discord.Color.green()
                )
                embed.set_thumbnail(url=member.display_avatar.url if member.display_avatar else None)
                
                try:
                    await channel.send(embed=embed)
                except discord.Forbidden:
                    logger.warning(f"Missing permissions to send welcome message in channel {welcome_channel_id} of guild {member.guild.name}")
                    
        # Assign role
        if welcome_role_id:
            role = member.guild.get_role(welcome_role_id)
            if role:
                try:
                    await member.add_roles(role, reason="Auto-assigned on join.")
                except discord.Forbidden:
                    logger.warning(f"Missing permissions to assign welcome role {welcome_role_id} in guild {member.guild.name}")

async def setup(bot):
    await bot.add_cog(WelcomerCog(bot))
