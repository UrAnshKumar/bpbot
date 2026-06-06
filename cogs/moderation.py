import discord
from discord import app_commands
from discord.ext import commands
import database
import logging
import re
import datetime
from datetime import timedelta

logger = logging.getLogger("moderation")

# List of common vulgar words
VULGAR_WORDS = {'fuck', 'shit', 'bitch', 'asshole', 'cunt', 'nigger', 'faggot', 'dick', 'pussy', 'bastard', 'motherfucker'}

def check_vulgar(content: str) -> bool:
    """Checks if the content contains any vulgar words using simple word token search."""
    cleaned = re.sub(r'[^a-zA-Z\s]', '', content).lower()
    words = cleaned.split()
    return any(word in VULGAR_WORDS for word in words)

def check_caps_spam(content: str) -> bool:
    """Checks if the message consists of excessive CAPITAL letters (caps spam)."""
    # Only check messages longer than 10 characters
    if len(content) <= 10:
        return False
    # Calculate uppercase ratio among alphabetic characters
    alpha_chars = [c for c in content if c.isalpha()]
    if not alpha_chars:
        return False
    caps_count = sum(1 for c in alpha_chars if c.isupper())
    return (caps_count / len(alpha_chars)) > 0.70

async def is_moderator_check(interaction: discord.Interaction) -> bool:
    """Checks if the interaction user is an administrator or has a registered moderator role."""
    if not interaction.guild:
        await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        return False
    if interaction.user.guild_permissions.administrator:
        return True
    mod_roles = database.get_mod_roles(interaction.guild_id)
    user_role_ids = [role.id for role in interaction.user.roles]
    if any(r_id in mod_roles for r_id in user_role_ids):
        return True
    await interaction.response.send_message(
        "❌ You do not have permission to run this command. (Moderator role or Administrator permission required)",
        ephemeral=True
    )
    return False

class Moderation(commands.Cog):
    """Cog containing moderator commands, automod rules, and activity logging."""

    modrole_group = app_commands.Group(name="modrole", description="Configure moderator roles.")
    config_group = app_commands.Group(name="config", description="Configure logging and moderator settings.")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.spam_cooldown = {} # (guild_id, user_id) -> list of timestamps

    # ==========================================
    # MODROLE MANAGEMENT (Admin Only)
    # ==========================================

    @modrole_group.command(name="add", description="Add a role to the list of authorized moderators.")
    @app_commands.describe(role="Select the role to add as a moderator.")
    @app_commands.default_permissions(administrator=True)
    async def add_role(self, interaction: discord.Interaction, role: discord.Role):
        if not interaction.guild:
            return
        database.add_mod_role(interaction.guild_id, role.id)
        await interaction.response.send_message(f"✅ Role {role.mention} has been added as a moderator role.", ephemeral=True)

    @modrole_group.command(name="remove", description="Remove a role from the list of authorized moderators.")
    @app_commands.describe(role="Select the moderator role to remove.")
    @app_commands.default_permissions(administrator=True)
    async def remove_role(self, interaction: discord.Interaction, role: discord.Role):
        if not interaction.guild:
            return
        database.remove_mod_role(interaction.guild_id, role.id)
        await interaction.response.send_message(f"✅ Role {role.mention} has been removed from moderator roles.", ephemeral=True)

    @modrole_group.command(name="list", description="List all registered moderator roles for this server.")
    @app_commands.default_permissions(administrator=True)
    async def list_roles(self, interaction: discord.Interaction):
        if not interaction.guild:
            return
        role_ids = database.get_mod_roles(interaction.guild_id)
        if not role_ids:
            await interaction.response.send_message("ℹ️ No custom moderator roles registered. Only administrators can run mod commands.", ephemeral=True)
            return

        mentions = []
        for r_id in role_ids:
            role = interaction.guild.get_role(r_id)
            if role:
                mentions.append(role.mention)
            else:
                # Cleanup deleted roles
                database.remove_mod_role(interaction.guild_id, r_id)

        await interaction.response.send_message(
            f"🛡️ **Moderator Roles for this server:**\n" + "\n".join(mentions),
            ephemeral=True
        )

    # ==========================================
    # MODERATION SLASH COMMANDS
    # ==========================================

    @app_commands.command(name="ban", description="Ban a member from the server.")
    @app_commands.describe(
        member="The member to ban.",
        reason="Reason for the ban (required).",
        delete_message_days="Delete their messages from the last N days (0 to 7)."
    )
    async def ban(self, interaction: discord.Interaction, member: discord.Member, reason: str, delete_message_days: int = 0):
        if not await is_moderator_check(interaction):
            return

        # Check role hierarchies
        if member.id == interaction.user.id:
            await interaction.response.send_message("❌ You cannot ban yourself.", ephemeral=True)
            return
        if member.top_role >= interaction.user.top_role and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ You cannot ban this user because they have a higher or equal role hierarchy than you.", ephemeral=True)
            return
        if member.top_role >= interaction.guild.me.top_role:
            await interaction.response.send_message("❌ I cannot ban this user because they have a higher or equal role hierarchy than me.", ephemeral=True)
            return

        # DM warning message
        try:
            embed = discord.Embed(
                title="🔨 You Have Been Banned",
                description=f"You have been banned from **{interaction.guild.name}**.",
                color=discord.Color.red()
            )
            embed.add_field(name="Reason", value=reason, inline=False)
            embed.set_footer(text=f"Banned by {interaction.user}")
            await member.send(embed=embed)
        except Exception:
            pass # Suppress DM closed errors

        # Execute ban
        try:
            delete_message_days = max(0, min(7, delete_message_days))
            await member.ban(reason=reason, delete_message_seconds=delete_message_days * 86400)
            
            # Log action
            database.add_mod_log(
                guild_id=interaction.guild_id,
                user_id=member.id,
                user_name=str(member),
                moderator_id=interaction.user.id,
                moderator_name=str(interaction.user),
                action="BAN",
                reason=reason
            )
            await interaction.response.send_message(f"🔨 **{member}** has been banned successfully. Reason: {reason}")
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to ban member: {e}", ephemeral=True)

    @app_commands.command(name="kick", description="Kick a member from the server.")
    @app_commands.describe(member="The member to kick.", reason="Reason for the kick (required).")
    async def kick(self, interaction: discord.Interaction, member: discord.Member, reason: str):
        if not await is_moderator_check(interaction):
            return

        if member.id == interaction.user.id:
            await interaction.response.send_message("❌ You cannot kick yourself.", ephemeral=True)
            return
        if member.top_role >= interaction.user.top_role and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ You cannot kick this user because they have a higher or equal role hierarchy than you.", ephemeral=True)
            return
        if member.top_role >= interaction.guild.me.top_role:
            await interaction.response.send_message("❌ I cannot kick this user because they have a higher or equal role hierarchy than me.", ephemeral=True)
            return

        # DM warning message
        try:
            embed = discord.Embed(
                title="👢 You Have Been Kicked",
                description=f"You have been kicked from **{interaction.guild.name}**.",
                color=discord.Color.orange()
            )
            embed.add_field(name="Reason", value=reason, inline=False)
            embed.set_footer(text=f"Kicked by {interaction.user}")
            await member.send(embed=embed)
        except Exception:
            pass

        # Execute kick
        try:
            await member.kick(reason=reason)
            database.add_mod_log(
                guild_id=interaction.guild_id,
                user_id=member.id,
                user_name=str(member),
                moderator_id=interaction.user.id,
                moderator_name=str(interaction.user),
                action="KICK",
                reason=reason
            )
            await interaction.response.send_message(f"👢 **{member}** has been kicked successfully. Reason: {reason}")
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to kick member: {e}", ephemeral=True)

    @app_commands.command(name="mute", description="Timeout (mute) a member in the server.")
    @app_commands.describe(member="The member to mute.", duration_minutes="Mute duration in minutes.", reason="Reason for mute (required).")
    async def mute(self, interaction: discord.Interaction, member: discord.Member, duration_minutes: int, reason: str):
        if not await is_moderator_check(interaction):
            return

        if member.id == interaction.user.id:
            await interaction.response.send_message("❌ You cannot mute yourself.", ephemeral=True)
            return
        if member.top_role >= interaction.user.top_role and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ You cannot mute this user because they have a higher or equal role hierarchy than you.", ephemeral=True)
            return
        if member.top_role >= interaction.guild.me.top_role:
            await interaction.response.send_message("❌ I cannot mute this user because they have a higher or equal role hierarchy than me.", ephemeral=True)
            return

        # Execute timeout/mute
        try:
            duration = timedelta(minutes=duration_minutes)
            await member.timeout(duration, reason=reason)
            
            # DM Warning message
            try:
                embed = discord.Embed(
                    title="🔇 You Have Been Muted (Timeout)",
                    description=f"You have been muted in **{interaction.guild.name}**.",
                    color=discord.Color.gold()
                )
                embed.add_field(name="Duration", value=f"{duration_minutes} minute(s)", inline=True)
                embed.add_field(name="Reason", value=reason, inline=False)
                embed.set_footer(text=f"Muted by {interaction.user}")
                await member.send(embed=embed)
            except Exception:
                pass

            database.add_mod_log(
                guild_id=interaction.guild_id,
                user_id=member.id,
                user_name=str(member),
                moderator_id=interaction.user.id,
                moderator_name=str(interaction.user),
                action="MUTE",
                reason=f"Duration: {duration_minutes}m | Reason: {reason}"
            )
            await interaction.response.send_message(f"🔇 **{member}** has been timed out for {duration_minutes} minute(s). Reason: {reason}")
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to mute member: {e}", ephemeral=True)

    @app_commands.command(name="unmute", description="Remove timeout (unmute) a member in the server.")
    @app_commands.describe(member="The member to unmute.", reason="Reason for unmute.")
    async def unmute(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason specified"):
        if not await is_moderator_check(interaction):
            return

        if not member.is_timed_out():
            await interaction.response.send_message("❌ This member is not currently muted/timed out.", ephemeral=True)
            return

        # Execute unmute
        try:
            await member.timeout(None, reason=reason)
            
            try:
                embed = discord.Embed(
                    title="🔊 You Have Been Unmuted",
                    description=f"Your timeout has been removed in **{interaction.guild.name}**.",
                    color=discord.Color.green()
                )
                embed.add_field(name="Reason", value=reason, inline=False)
                await member.send(embed=embed)
            except Exception:
                pass

            database.add_mod_log(
                guild_id=interaction.guild_id,
                user_id=member.id,
                user_name=str(member),
                moderator_id=interaction.user.id,
                moderator_name=str(interaction.user),
                action="UNMUTE",
                reason=reason
            )
            await interaction.response.send_message(f"🔊 **{member}** has been unmuted. Reason: {reason}")
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to unmute member: {e}", ephemeral=True)

    @app_commands.command(name="warn", description="Warn a member.")
    @app_commands.describe(member="The member to warn.", reason="Reason for the warning (required).")
    async def warn(self, interaction: discord.Interaction, member: discord.Member, reason: str):
        if not await is_moderator_check(interaction):
            return

        if member.id == interaction.user.id:
            await interaction.response.send_message("❌ You cannot warn yourself.", ephemeral=True)
            return
        if member.bot:
            await interaction.response.send_message("❌ You cannot warn a bot.", ephemeral=True)
            return

        # Add warning to DB
        database.add_warning(
            guild_id=interaction.guild_id,
            user_id=member.id,
            reason=reason,
            moderator_id=interaction.user.id
        )
        
        # Add to mod logs
        database.add_mod_log(
            guild_id=interaction.guild_id,
            user_id=member.id,
            user_name=str(member),
            moderator_id=interaction.user.id,
            moderator_name=str(interaction.user),
            action="WARN",
            reason=reason
        )

        # DM member
        try:
            embed = discord.Embed(
                title="⚠️ Warning Issued",
                description=f"You have been warned in **{interaction.guild.name}**.",
                color=discord.Color.yellow()
            )
            embed.add_field(name="Reason", value=reason, inline=False)
            embed.set_footer(text=f"Warned by {interaction.user}")
            await member.send(embed=embed)
        except Exception:
            pass

        await interaction.response.send_message(f"⚠️ **{member}** has been warned. Reason: {reason}")

    @app_commands.command(name="warnings", description="List warnings for a member.")
    @app_commands.describe(member="Select a member to view their warnings.")
    async def warnings(self, interaction: discord.Interaction, member: discord.Member):
        if not await is_moderator_check(interaction):
            return

        warns = database.get_warnings(interaction.guild_id, member.id)
        if not warns:
            await interaction.response.send_message(f"ℹ️ **{member}** has 0 active warnings.")
            return

        embed = discord.Embed(
            title=f"⚠️ Warn History — {member}",
            description=f"Total warnings: **{len(warns)}**",
            color=discord.Color.yellow()
        )
        
        # Display up to 10 latest warnings
        for i, w in enumerate(warns[:10], start=1):
            moderator = interaction.guild.get_member(w["moderator_id"])
            mod_str = moderator.mention if moderator else f"ID: {w['moderator_id']}"
            embed.add_field(
                name=f"Warning #{i}",
                value=f"**Reason:** {w['reason']}\n**Moderator:** {mod_str}\n**Date:** {w['timestamp']}",
                inline=False
            )
            
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="clearwarns", description="Clear all warnings for a member.")
    @app_commands.describe(member="Select a member to clear warnings.")
    async def clearwarns(self, interaction: discord.Interaction, member: discord.Member):
        if not await is_moderator_check(interaction):
            return

        warns = database.get_warnings(interaction.guild_id, member.id)
        if not warns:
            await interaction.response.send_message(f"ℹ️ **{member}** has no warnings to clear.", ephemeral=True)
            return

        database.clear_warnings(interaction.guild_id, member.id)
        database.add_mod_log(
            guild_id=interaction.guild_id,
            user_id=member.id,
            user_name=str(member),
            moderator_id=interaction.user.id,
            moderator_name=str(interaction.user),
            action="CLEAR_WARNS",
            reason=f"Cleared {len(warns)} warnings."
        )

        await interaction.response.send_message(f"✅ Successfully cleared all warnings (**{len(warns)}**) for **{member}**.")

    # ==========================================
    # AUTO MODERATION slash configuration
    # ==========================================

    @app_commands.command(name="automod", description="Configure Auto-Moderation settings.")
    @app_commands.describe(
        enabled="Enable or disable auto moderation entirely.",
        vulgar="Filter out curse/NSFW words.",
        caps="Filter out caps spam (longer than 10 letters, >70% caps).",
        spam="Filter message spam (more than 5 messages in 3 seconds)."
    )
    async def automod(self, interaction: discord.Interaction, enabled: bool, vulgar: bool = True, caps: bool = True, spam: bool = True):
        if not await is_moderator_check(interaction):
            return

        database.save_automod_settings(interaction.guild_id, enabled, vulgar, caps, spam)
        
        status = "enabled" if enabled else "disabled"
        embed = discord.Embed(
            title="🛡️ Auto-Moderation Configured",
            description=f"Auto moderation has been **{status}**.",
            color=discord.Color.blue()
        )
        embed.add_field(name="Vulgar/NSFW Filter", value="✅ Enabled" if vulgar else "❌ Disabled", inline=True)
        embed.add_field(name="Caps Abuse Filter", value="✅ Enabled" if caps else "❌ Disabled", inline=True)
        embed.add_field(name="Message Spam Filter", value="✅ Enabled" if spam else "❌ Disabled", inline=True)
        
        await interaction.response.send_message(embed=embed)

    # ==========================================
    # MODERATION LOG LISTER (/config logs)
    # ==========================================

    @config_group.command(name="logs", description="List the latest 10 moderation log entries.")
    async def view_logs(self, interaction: discord.Interaction):
        if not await is_moderator_check(interaction):
            return

        logs = database.get_mod_logs(interaction.guild_id, limit=10)
        if not logs:
            await interaction.response.send_message("ℹ️ No moderation logs exist for this server yet.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"📜 Mod Logs — {interaction.guild.name}",
            color=discord.Color.blue(),
            timestamp=datetime.datetime.now()
        )
        
        for l in logs:
            action_emojis = {
                "BAN": "🔨",
                "KICK": "👢",
                "MUTE": "🔇",
                "UNMUTE": "🔊",
                "WARN": "⚠️",
                "CLEAR_WARNS": "✅",
                "AUTOMOD_VULGAR": "🚫 [Auto]",
                "AUTOMOD_CAPS": "🔠 [Auto]",
                "AUTOMOD_SPAM": "🚨 [Auto]"
            }
            emoji = action_emojis.get(l["action"], "📝")
            embed.add_field(
                name=f"{emoji} {l['action']} - {l['user_name']} (ID: {l['user_id']})",
                value=f"**Mod:** {l['moderator_name']} | **Reason:** {l['reason']}\n**Time:** {l['timestamp']}",
                inline=False
            )
            
        await interaction.response.send_message(embed=embed)

    # ==========================================
    # AUTO-MODERATION ENGINE (Event Listener)
    # ==========================================

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Active scanner that checks messages against automod policies."""
        if not message.guild or message.author.bot:
            return

        # Fetch automod settings
        settings = database.get_automod_settings(message.guild.id)
        if not settings or not settings.get("enabled"):
            return

        # Bypass moderation checks for admins or moderators
        is_mod = message.author.guild_permissions.administrator
        if not is_mod:
            mod_roles = database.get_mod_roles(message.guild.id)
            user_role_ids = [role.id for role in message.author.roles]
            is_mod = any(r_id in mod_roles for r_id in user_role_ids)

        if is_mod:
            return

        # --- Rule 1: Vulgar / NSFW Word Filter ---
        if settings.get("vulgar_filter") and check_vulgar(message.content):
            try:
                await message.delete()
            except Exception:
                pass # Bot lacks permission to delete
                
            # Warn user and log it
            database.add_warning(
                guild_id=message.guild.id,
                user_id=message.author.id,
                reason="Auto-moderation: Vulgar words detected",
                moderator_id=self.bot.user.id
            )
            database.add_mod_log(
                guild_id=message.guild.id,
                user_id=message.author.id,
                user_name=str(message.author),
                moderator_id=self.bot.user.id,
                moderator_name=str(self.bot.user),
                action="AUTOMOD_VULGAR",
                reason=f"Message deleted: '{message.content[:50]}...'"
            )
            
            try:
                await message.channel.send(f"⚠️ {message.author.mention}, vulgar language is not permitted here. Message deleted and warning issued.", delete_after=10)
                # DM warning
                embed = discord.Embed(
                    title="🚫 Message Deleted (Automod)",
                    description=f"Your message in **{message.guild.name}** was deleted for using vulgar or NSFW language.",
                    color=discord.Color.red()
                )
                embed.add_field(name="Deleted Content", value=message.content[:200], inline=False)
                await message.author.send(embed=embed)
            except Exception:
                pass
            return # Block further processing of this message

        # --- Rule 2: Caps Spam Filter ---
        if settings.get("caps_filter") and check_caps_spam(message.content):
            try:
                await message.delete()
            except Exception:
                pass
                
            database.add_mod_log(
                guild_id=message.guild.id,
                user_id=message.author.id,
                user_name=str(message.author),
                moderator_id=self.bot.user.id,
                moderator_name=str(self.bot.user),
                action="AUTOMOD_CAPS",
                reason=f"Message deleted: '{message.content[:50]}...'"
            )
            
            try:
                await message.channel.send(f"⚠️ {message.author.mention}, please stop using excessive caps! Message deleted.", delete_after=8)
            except Exception:
                pass
            return

        # --- Rule 3: Message Spam Filter (Rate Limits) ---
        if settings.get("spam_filter"):
            now = datetime.datetime.now().timestamp()
            key = (message.guild.id, message.author.id)
            
            if key not in self.spam_cooldown:
                self.spam_cooldown[key] = []
                
            self.spam_cooldown[key].append(now)
            # Retain only timestamps within the last 3 seconds
            self.spam_cooldown[key] = [t for t in self.spam_cooldown[key] if now - t <= 3]
            
            if len(self.spam_cooldown[key]) > 5:
                # User has sent more than 5 messages in 3 seconds. Delete and issue mute.
                try:
                    await message.delete()
                except Exception:
                    pass
                
                # Mute/Timeout the spammer for 5 minutes
                try:
                    duration = timedelta(minutes=5)
                    await message.author.timeout(duration, reason="Auto-moderation: Chat spamming")
                    
                    database.add_mod_log(
                        guild_id=message.guild.id,
                        user_id=message.author.id,
                        user_name=str(message.author),
                        moderator_id=self.bot.user.id,
                        moderator_name=str(self.bot.user),
                        action="AUTOMOD_SPAM",
                        reason="Automuted for 5 minutes due to message spam."
                    )
                    
                    await message.channel.send(f"🚨 {message.author.mention} has been muted for 5 minutes for spamming.", delete_after=15)
                    
                    # DM the user
                    embed = discord.Embed(
                        title="🚨 Timed Out (Automod)",
                        description=f"You have been timed out for 5 minutes in **{message.guild.name}** for chat spamming.",
                        color=discord.Color.red()
                    )
                    await message.author.send(embed=embed)
                except Exception as e:
                    logger.error(f"Failed to timeout spam user {message.author.id}: {e}")
                
                # Clean cooldown lists to prevent multiple cascading triggers
                self.spam_cooldown[key] = []
                return

async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
