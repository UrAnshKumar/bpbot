import discord
from discord.ext import commands, tasks
from discord import app_commands
import database
import logging
from datetime import datetime

logger = logging.getLogger("StudyBot")

class PrivateRoomsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cleanup_expired_rooms.start()

    def cog_unload(self):
        self.cleanup_expired_rooms.cancel()

    @tasks.loop(seconds=60)
    async def cleanup_expired_rooms(self):
        try:
            expired = database.get_expired_rooms()
            for room in expired:
                channel_id = room['channel_id']
                owner_id = room['owner_id']
                
                # Fetch channel from bot
                channel = self.bot.get_channel(channel_id)
                if not channel:
                    try:
                        channel = await self.bot.fetch_channel(channel_id)
                    except Exception:
                        channel = None
                        
                if channel:
                    try:
                        await channel.delete(reason="Private voice channel rental expired.")
                    except discord.Forbidden:
                        logger.warning(f"Failed to delete expired voice channel {channel_id} due to missing permissions.")
                    except Exception as e:
                        logger.error(f"Error deleting expired voice channel {channel_id}: {e}")
                        
                # Delete from DB
                database.delete_private_room(channel_id)
                
                # Notify owner via DM
                owner = self.bot.get_user(owner_id)
                if not owner:
                    try:
                        owner = await self.bot.fetch_user(owner_id)
                    except Exception:
                        owner = None
                if owner:
                    try:
                        embed = discord.Embed(
                            title="⏱️ Private Voice Channel Expired",
                            description="Your rented private voice channel has expired and was deleted.",
                            color=discord.Color.orange()
                        )
                        await owner.send(embed=embed)
                    except Exception:
                        pass # DMs closed or blocked
        except Exception as e:
            logger.error(f"Error in cleanup_expired_rooms loop: {e}")

    @cleanup_expired_rooms.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()

    async def get_user_room_channel(self, interaction: discord.Interaction):
        member = interaction.user
        
        # 1. Check if user is currently inside their rented channel
        if member.voice and member.voice.channel:
            room = database.get_private_room(member.voice.channel.id)
            if room and room['owner_id'] == member.id:
                return member.voice.channel
                
        # 2. Lookup user's owned channel in database
        if not interaction.guild:
            return None
            
        conn = database.get_db_connection()
        row = conn.execute("SELECT channel_id FROM private_rooms WHERE owner_id = ?", (member.id,)).fetchone()
        conn.close()
        
        if row:
            channel = interaction.guild.get_channel(row['channel_id'])
            if channel:
                return channel
                
        return None

    @app_commands.command(name="voice-rent", description="Rent a private voice channel with study coins.")
    @app_commands.describe(duration_hours="The number of hours to rent the channel (50 coins/hour)")
    async def voice_rent(self, interaction: discord.Interaction, duration_hours: int = 1):
        if not interaction.guild:
            await interaction.response.send_message("❌ This command can only be used in a server!", ephemeral=True)
            return

        if duration_hours <= 0:
            await interaction.response.send_message("❌ Rent duration must be at least 1 hour!", ephemeral=True)
            return

        user_id = interaction.user.id
        rate = 50
        cost = rate * duration_hours
        
        # Check if user already owns an active room
        conn = database.get_db_connection()
        existing = conn.execute("SELECT * FROM private_rooms WHERE owner_id = ?", (user_id,)).fetchone()
        conn.close()
        
        if existing:
            await interaction.response.send_message("❌ You already have an active private voice channel! Use `/voice-extend` to add more time.", ephemeral=True)
            return

        # Deduct coins
        success = database.deduct_coins(user_id, cost)
        if not success:
            user_data = database.get_user(user_id)
            await interaction.response.send_message(
                f"❌ Insufficient coins! You need **{cost} 🪙** but only have **{user_data['coins']} 🪙**.\n"
                f"Earn coins by studying in voice channels or checking off todo tasks!",
                ephemeral=True
            )
            return

        # Check or create "Private Rooms" category
        guild = interaction.guild
        category = discord.utils.get(guild.categories, name="Private Rooms")
        if not category:
            try:
                category = await guild.create_category_channel("Private Rooms", reason="Category for private voice channels.")
            except discord.Forbidden:
                category = None # create at root level if category creation is blocked

        # Define channel permission overwrites
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(connect=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, connect=True, manage_channels=True, move_members=True),
            interaction.user: discord.PermissionOverwrite(view_channel=True, connect=True, manage_channels=True, move_members=True)
        }

        try:
            channel = await guild.create_voice_channel(
                name=f"🔒 {interaction.user.display_name}'s Room",
                category=category,
                overwrites=overwrites,
                reason="Private voice channel rental."
            )
        except discord.Forbidden:
            # refund coins
            database.add_coins(user_id, cost)
            await interaction.response.send_message("❌ I do not have permission to create voice channels in this server!", ephemeral=True)
            return

        # Save to DB
        expiry = database.add_private_room(channel.id, user_id, duration_hours, rate)
        timestamp_int = int(expiry)
        expiry_formatted = f"<t:{timestamp_int}:F> (<t:{timestamp_int}:R>)"

        embed = discord.Embed(
            title="🎙️ Private Voice Channel Rented",
            description=f"Successfully rented private voice channel: {channel.mention}!\n\n"
                        f"**Cost Paid:** {cost} 🪙\n"
                        f"**Expires On:** {expiry_formatted}\n\n"
                        f"Use the following commands to manage your channel:\n"
                        f"• `/voice-lock` - Deny access to everyone\n"
                        f"• `/voice-unlock` - Grant access to everyone\n"
                        f"• `/voice-permit [member]` - Allow a specific member inside\n"
                        f"• `/voice-reject [member]` - Ban and kick a member from your room\n"
                        f"• `/voice-rename [name]` - Rename your room\n"
                        f"• `/voice-extend [hours]` - Pay to keep the room longer",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="voice-extend", description="Extend the rental duration of your private voice channel.")
    @app_commands.describe(duration_hours="The number of hours to extend (50 coins/hour)")
    async def voice_extend(self, interaction: discord.Interaction, duration_hours: int = 1):
        if not interaction.guild:
            await interaction.response.send_message("❌ This command can only be used in a server!", ephemeral=True)
            return

        if duration_hours <= 0:
            await interaction.response.send_message("❌ Extension duration must be at least 1 hour!", ephemeral=True)
            return

        channel = await self.get_user_room_channel(interaction)
        if not channel:
            await interaction.response.send_message("❌ You do not own any active rented voice channel in this server!", ephemeral=True)
            return

        user_id = interaction.user.id
        rate = 50
        cost = rate * duration_hours

        # Deduct coins
        success = database.deduct_coins(user_id, cost)
        if not success:
            user_data = database.get_user(user_id)
            await interaction.response.send_message(
                f"❌ Insufficient coins! You need **{cost} 🪙** but only have **{user_data['coins']} 🪙**.",
                ephemeral=True
            )
            return

        # Extend in DB
        new_expiry = database.extend_private_room(channel.id, duration_hours)
        timestamp_int = int(new_expiry)
        expiry_formatted = f"<t:{timestamp_int}:F> (<t:{timestamp_int}:R>)"

        embed = discord.Embed(
            title="⏱️ Private Room Extended",
            description=f"Successfully extended rental for {channel.mention}!\n\n"
                        f"**Cost Paid:** {cost} 🪙\n"
                        f"**New Expiry:** {expiry_formatted}",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="voice-lock", description="Lock your private voice channel so only permitted members can join.")
    async def voice_lock(self, interaction: discord.Interaction):
        channel = await self.get_user_room_channel(interaction)
        if not channel:
            await interaction.response.send_message("❌ You do not own any active rented voice channel in this server!", ephemeral=True)
            return

        try:
            overwrites = channel.overwrites_for(interaction.guild.default_role)
            overwrites.connect = False
            await channel.set_permissions(interaction.guild.default_role, overwrite=overwrites, reason="Locked by room owner.")
            await interaction.response.send_message(f"🔒 Locked {channel.mention}. Only permitted members can connect now.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I do not have permission to modify this channel's permissions!", ephemeral=True)

    @app_commands.command(name="voice-unlock", description="Unlock your private voice channel so everyone can join.")
    async def voice_unlock(self, interaction: discord.Interaction):
        channel = await self.get_user_room_channel(interaction)
        if not channel:
            await interaction.response.send_message("❌ You do not own any active rented voice channel in this server!", ephemeral=True)
            return

        try:
            overwrites = channel.overwrites_for(interaction.guild.default_role)
            overwrites.connect = True
            await channel.set_permissions(interaction.guild.default_role, overwrite=overwrites, reason="Unlocked by room owner.")
            await interaction.response.send_message(f"🔓 Unlocked {channel.mention}. Anyone can connect now.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I do not have permission to modify this channel's permissions!", ephemeral=True)

    @app_commands.command(name="voice-permit", description="Allow a specific member to join your locked private channel.")
    @app_commands.describe(member="The member to permit")
    async def voice_permit(self, interaction: discord.Interaction, member: discord.Member):
        channel = await self.get_user_room_channel(interaction)
        if not channel:
            await interaction.response.send_message("❌ You do not own any active rented voice channel in this server!", ephemeral=True)
            return

        if member.id == interaction.user.id:
            await interaction.response.send_message("❌ You are already the owner of this channel!", ephemeral=True)
            return

        try:
            overwrites = channel.overwrites_for(member)
            overwrites.connect = True
            overwrites.view_channel = True
            await channel.set_permissions(member, overwrite=overwrites, reason="Permitted by room owner.")
            await interaction.response.send_message(f"✅ Permitted {member.mention} to connect to {channel.mention}.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I do not have permission to modify this channel's permissions!", ephemeral=True)

    @app_commands.command(name="voice-reject", description="Deny a member from joining your channel and disconnect them if they are inside.")
    @app_commands.describe(member="The member to reject")
    async def voice_reject(self, interaction: discord.Interaction, member: discord.Member):
        channel = await self.get_user_room_channel(interaction)
        if not channel:
            await interaction.response.send_message("❌ You do not own any active rented voice channel in this server!", ephemeral=True)
            return

        if member.id == interaction.user.id:
            await interaction.response.send_message("❌ You cannot reject yourself!", ephemeral=True)
            return

        try:
            overwrites = channel.overwrites_for(member)
            overwrites.connect = False
            await channel.set_permissions(member, overwrite=overwrites, reason="Rejected by room owner.")
            
            msg = f"🚫 Rejected {member.mention} from connecting to {channel.mention}."
            
            # Kick member out if they are currently inside the channel
            if member.voice and member.voice.channel == channel:
                try:
                    await member.move_to(None, reason="Rejected from voice channel by room owner.")
                    msg += " They have been disconnected."
                except discord.Forbidden:
                    msg += " (Failed to disconnect them; please ensure my bot role has 'Move Members' permission!)"

            await interaction.response.send_message(msg, ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I do not have permission to modify this channel's permissions!", ephemeral=True)

    @app_commands.command(name="voice-rename", description="Rename your private voice channel.")
    @app_commands.describe(name="The new name for the channel")
    async def voice_rename(self, interaction: discord.Interaction, name: str):
        channel = await self.get_user_room_channel(interaction)
        if not channel:
            await interaction.response.send_message("❌ You do not own any active rented voice channel in this server!", ephemeral=True)
            return

        if not name.strip():
            await interaction.response.send_message("❌ Invalid name!", ephemeral=True)
            return

        try:
            old_name = channel.name
            await channel.edit(name=name, reason="Renamed by room owner.")
            await interaction.response.send_message(f"📝 Renamed voice channel from `{old_name}` to `{name}`.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I do not have permission to rename this channel!", ephemeral=True)
        except discord.HTTPException as e:
            await interaction.response.send_message(f"❌ Failed to rename channel: {e}", ephemeral=True)

async def setup(bot):
    await bot.add_cog(PrivateRoomsCog(bot))
