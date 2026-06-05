import discord
from discord import app_commands
from discord.ext import commands
import database
import logging

logger = logging.getLogger("welcomer")

def format_placeholders(text: str, member: discord.Member, channel: discord.TextChannel) -> str:
    """Replaces placeholders in the welcome message with dynamic member/server details."""
    if not text:
        return ""
    avatar_url = member.display_avatar.url if member.display_avatar else "https://i.imgur.com/8Q9Z9oX.png"
    return (
        text.replace("{username}", member.mention)
        .replace("{server}", member.guild.name)
        .replace("{channel}", channel.mention if channel else "#welcome")
        .replace("{member_count}", str(member.guild.member_count))
        .replace("{user_avatar}", avatar_url)
    )

def parse_color(color_hex: str) -> discord.Color:
    """Parses a hex color string into a discord.Color object, defaulting to Blurple on failure."""
    if not color_hex:
        return discord.Color.blurple()
    color_hex = color_hex.strip().lstrip('#')
    try:
        return discord.Color(int(color_hex, 16))
    except ValueError:
        return discord.Color.blurple()


class WelcomeSetupModalPart1(discord.ui.Modal, title="Welcome Setup - Part 1/2"):
    """Part 1 of the welcome setup: Basic configurations and author info."""
    
    normal_text = discord.ui.TextInput(
        label="Normal Text (Outside Embed)",
        style=discord.ui.TextStyle.paragraph,
        placeholder="e.g. Hey {username}, welcome to {server}! You are member #{member_count}.",
        default="Hey {username}, welcome to **{server}**! 🎉 Please check out {channel} to get started! You are member #{member_count}.",
        required=True,
        max_length=500
    )
    
    author_name = discord.ui.TextInput(
        label="Embed Author Name",
        style=discord.ui.TextStyle.short,
        placeholder="e.g. Server Welcome Team",
        default="New Member Arrived!",
        required=False,
        max_length=100
    )
    
    author_icon = discord.ui.TextInput(
        label="Embed Author Icon URL",
        style=discord.ui.TextStyle.short,
        placeholder="e.g. https://domain.com/icon.png",
        default="https://i.imgur.com/8Q9Z9oX.png",
        required=False,
        max_length=256
    )
    
    embed_title = discord.ui.TextInput(
        label="Embed Title",
        style=discord.ui.TextStyle.short,
        placeholder="e.g. Welcome to our Server!",
        default="👋 Welcome to our community!",
        required=False,
        max_length=100
    )
    
    embed_thumbnail = discord.ui.TextInput(
        label="Embed Side Square Logo (Thumbnail URL)",
        style=discord.ui.TextStyle.short,
        placeholder="e.g. https://domain.com/logo.png or {user_avatar}",
        default="{user_avatar}",
        required=False,
        max_length=256
    )

    def __init__(self, cog, channel: discord.TextChannel):
        super().__init__()
        self.cog = cog
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        # Save part 1 data temporarily in the cog
        self.cog.temp_settings[interaction.user.id] = {
            "channel_id": self.channel.id,
            "normal_text": self.normal_text.value,
            "embed_author_name": self.author_name.value,
            "embed_author_icon": self.author_icon.value,
            "embed_title": self.embed_title.value,
            "embed_thumbnail": self.embed_thumbnail.value
        }
        
        # Send a button view to trigger Part 2
        view = WelcomeSetupPart2View(self.cog, self.channel)
        await interaction.response.send_message(
            content=(
                "✅ **Step 1/2 complete!** Basic details have been captured.\n"
                "Click the button below to configure Step 2 (Embed Description, Footer, Banner & Colors)."
            ),
            view=view,
            ephemeral=True
        )


class WelcomeSetupPart2View(discord.ui.View):
    """Temporary view to allow the user to trigger Part 2 modal."""
    def __init__(self, cog, channel: discord.TextChannel):
        super().__init__(timeout=300) # 5 minutes timeout
        self.cog = cog
        self.channel = channel

    @discord.ui.button(label="Configure Step 2: Content & Images", style=discord.ButtonStyle.primary, emoji="✏️")
    async def configure_part2(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Ensure only the user who started the setup can click
        if interaction.user.id not in self.cog.temp_settings:
            await interaction.response.send_message(
                "❌ You do not have an active welcome setup session. Use `/setup welcome` first.",
                ephemeral=True
            )
            return

        modal = WelcomeSetupModalPart2(self.cog, self.channel)
        await interaction.response.send_modal(modal)
        # Disable button after clicking
        button.disabled = True
        await interaction.message.edit(view=self)


class WelcomeSetupModalPart2(discord.ui.Modal, title="Welcome Setup - Part 2/2"):
    """Part 2 of the welcome setup: Main content, footer, banner and accent color."""
    
    embed_desc = discord.ui.TextInput(
        label="Embed Description (Supports Placeholders)",
        style=discord.ui.TextStyle.paragraph,
        placeholder="e.g. Welcome {username} to {server}...",
        default=(
            "✨ **Welcome to {server}, {username}!** ✨\n\n"
            "We are absolutely thrilled to have you join our community! 🚀\n"
            "You are member number **{member_count}**! 🎉\n\n"
            "🌟 **Getting Started:**\n"
            "📍 Read our rules in {channel} to keep our community safe.\n"
            "💬 Introduce yourself so we can get to know you!\n"
            "📢 Stay tuned for announcements and updates.\n\n"
            "💡 **Available Placeholders:**\n"
            "Use these in your settings:\n"
            "• `{username}` - Mentions the new member\n"
            "• `{server}` - Displays the server name\n"
            "• `{channel}` - Mentions this welcome channel\n"
            "• `{member_count}` - Shows the server member count\n\n"
            "We hope you have a fantastic time here! 💖"
        ),
        required=True,
        max_length=1000
    )
    
    embed_footer = discord.ui.TextInput(
        label="Embed Footer Text",
        style=discord.ui.TextStyle.short,
        placeholder="e.g. Enjoy your stay! | Member #{member_count}",
        default="Hope you have an amazing time here!",
        required=False,
        max_length=200
    )
    
    embed_banner = discord.ui.TextInput(
        label="Embed Bottom Banner Image URL",
        style=discord.ui.TextStyle.short,
        placeholder="e.g. https://domain.com/banner.png",
        default="https://i.imgur.com/aC3H2Q2.png",
        required=False,
        max_length=256
    )
    
    embed_color = discord.ui.TextInput(
        label="Embed Accent Hex Color",
        style=discord.ui.TextStyle.short,
        placeholder="e.g. #5865F2",
        default="#5865F2",
        required=False,
        max_length=7
    )

    def __init__(self, cog, channel: discord.TextChannel):
        super().__init__()
        self.cog = cog
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        p1 = self.cog.temp_settings.get(user_id)
        
        if not p1:
            await interaction.response.send_message(
                "❌ Setup timed out or was reset. Please start again with `/setup welcome`.",
                ephemeral=True
            )
            return
        
        # Save complete configurations to database
        database.save_welcome_settings(
            guild_id=interaction.guild_id,
            channel_id=p1["channel_id"],
            normal_text=p1["normal_text"],
            embed_title=p1["embed_title"],
            embed_description=self.embed_desc.value,
            embed_author_name=p1["embed_author_name"],
            embed_author_icon=p1["embed_author_icon"],
            embed_thumbnail=p1["embed_thumbnail"],
            embed_banner=self.embed_banner.value,
            embed_footer=self.embed_footer.value,
            embed_color=self.embed_color.value
        )
        
        # Clean up temp memory
        del self.cog.temp_settings[user_id]
        
        # Build live preview to show user
        parsed_color = parse_color(self.embed_color.value)
        normal_text_preview = format_placeholders(p1["normal_text"], interaction.user, self.channel)
        
        embed = discord.Embed(
            title=format_placeholders(p1["embed_title"], interaction.user, self.channel),
            description=format_placeholders(self.embed_desc.value, interaction.user, self.channel),
            color=parsed_color
        )
        
        if p1["embed_author_name"]:
            author_icon_url = format_placeholders(p1["embed_author_icon"], interaction.user, self.channel)
            embed.set_author(
                name=format_placeholders(p1["embed_author_name"], interaction.user, self.channel),
                icon_url=author_icon_url or None
            )
        
        if p1["embed_thumbnail"]:
            thumbnail_url_formatted = format_placeholders(p1["embed_thumbnail"], interaction.user, self.channel)
            embed.set_thumbnail(url=thumbnail_url_formatted)
            
        if self.embed_banner.value:
            banner_url_formatted = format_placeholders(self.embed_banner.value, interaction.user, self.channel)
            embed.set_image(url=banner_url_formatted)
            
        if self.embed_footer.value:
            embed.set_footer(
                text=format_placeholders(self.embed_footer.value, interaction.user, self.channel)
            )
            
        await interaction.response.send_message(
            content=f"🎉 **Welcome message configuration saved successfully!**\nHere is a live preview of how it will look in {self.channel.mention}:",
            embed=embed,
            ephemeral=True
        )
        
        # Also post normal text preview if it exists
        if normal_text_preview:
            await interaction.followup.send(
                content=f"**[Normal Text Preview]:**\n{normal_text_preview}",
                ephemeral=True
            )


class Welcomer(commands.Cog):
    """Cog containing welcome greeting configuration and event listener."""
    
    setup_group = app_commands.Group(name="setup", description="Server configuration setup commands.")
    dm_group = app_commands.Group(name="dm", description="DM configuration commands.")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.temp_settings = {} # Stores user_id -> temp_part1_data

        # Build nested group: /dm welcome set <enabled>
        welcome_subgroup = app_commands.Group(name="welcome", description="Manage welcome DM configurations.")
        
        @welcome_subgroup.command(name="set", description="Enable or disable sending the welcome message to joining members via DM.")
        @app_commands.describe(enabled="True to enable, False to disable.")
        @app_commands.default_permissions(manage_guild=True)
        async def set_dm_welcome(interaction: discord.Interaction, enabled: bool):
            if not interaction.guild:
                await interaction.response.send_message("❌ This command can only be used within a server.", ephemeral=True)
                return
            
            success = database.update_dm_welcome(interaction.guild_id, enabled)
            if success:
                status_str = "enabled" if enabled else "disabled"
                await interaction.response.send_message(
                    f"✅ **DM Welcome messages have been {status_str}!**\nNew members will {'now' if enabled else 'no longer'} receive the welcome message in their DMs.",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "❌ Welcome settings have not been configured for this server yet.\nPlease configure them first using `/setup welcome`.",
                    ephemeral=True
                )
        
        self.dm_group.add_command(welcome_subgroup)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Triggered when a member joins the guild; sends the welcome template if configured."""
        guild = member.guild
        settings = database.get_welcome_settings(guild.id)
        if not settings:
            return

        channel_id = settings.get("channel_id")
        channel = guild.get_channel(channel_id)

        # Prepare formatting parameters
        normal_text = format_placeholders(settings.get("normal_text"), member, channel)
        embed_title = format_placeholders(settings.get("embed_title"), member, channel)
        embed_desc = format_placeholders(settings.get("embed_description"), member, channel)
        author_name = format_placeholders(settings.get("embed_author_name"), member, channel)
        author_icon = format_placeholders(settings.get("embed_author_icon"), member, channel)
        thumbnail_url = format_placeholders(settings.get("embed_thumbnail"), member, channel)
        banner_url = format_placeholders(settings.get("embed_banner"), member, channel)
        footer_text = format_placeholders(settings.get("embed_footer"), member, channel)
        color_hex = settings.get("embed_color")

        # Build discord Embed
        parsed_color = parse_color(color_hex)
        embed = discord.Embed(
            title=embed_title or None,
            description=embed_desc or None,
            color=parsed_color
        )

        if author_name:
            embed.set_author(name=author_name, icon_url=author_icon or None)

        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)

        if banner_url:
            embed.set_image(url=banner_url)

        if footer_text:
            embed.set_footer(text=footer_text)

        # Send the final welcome message to the server channel
        if channel:
            try:
                await channel.send(content=normal_text or None, embed=embed)
            except Exception as e:
                logger.error(f"Failed to send welcome message in guild {guild.id} (channel {channel_id}): {e}")

        # Send the final welcome message via DM if enabled
        if settings.get("dm_welcome") == 1:
            try:
                await member.send(content=normal_text or None, embed=embed)
            except discord.Forbidden:
                logger.warning(f"Could not send welcome DM to {member.name} (DMs are closed/disabled).")
            except Exception as e:
                logger.error(f"Failed to send welcome DM to {member.name}: {e}")

    @setup_group.command(name="welcome", description="Setup interactive welcome message configurations for new members.")
    @app_commands.describe(channel="Select the text channel where welcome messages should be posted.")
    @app_commands.default_permissions(manage_guild=True)
    async def welcome(self, interaction: discord.Interaction, channel: discord.TextChannel):
        """Slash command /setup welcome channel to begin welcome setup."""
        if not interaction.guild:
            await interaction.response.send_message("❌ This command can only be used within a server.", ephemeral=True)
            return

        # Open the first modal
        modal = WelcomeSetupModalPart1(self, channel)
        await interaction.response.send_modal(modal)

async def setup(bot: commands.Bot):
    await bot.add_cog(Welcomer(bot))
