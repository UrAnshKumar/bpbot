import discord
from discord import app_commands
from discord.ext import commands
import logging
import asyncio
import io
import datetime
from datetime import timedelta
import aiohttp
from PIL import Image, ImageDraw, ImageFont, ImageOps

logger = logging.getLogger("pomodoro")

FONT_PATH = "c:/Windows/Fonts/arialbd.ttf"

def get_font(size: int) -> ImageFont.FreeTypeFont:
    """Loads Arial Bold with the specified size, falling back to default if unavailable."""
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except IOError:
        # Fallback to default
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()

async def fetch_avatar(user: discord.Member) -> Image.Image:
    """Fetches a member's avatar and returns it as a PIL image. Falls back to a grey circle on failure."""
    avatar_url = user.display_avatar.url
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(str(avatar_url)) as response:
                if response.status == 200:
                    data = await response.read()
                    return Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception as e:
        logger.warning(f"Failed to fetch avatar for {user}: {e}")
    
    # Generate a fallback avatar image
    img = Image.new("RGBA", (100, 100), color=(15, 30, 40, 255))
    draw = ImageDraw.Draw(img)
    draw.ellipse([(10, 10), (90, 90)], fill=(229, 169, 60, 255)) # Gold color circle
    return img

def crop_circle(img: Image.Image, size: int) -> Image.Image:
    """Crops an image into a circle with transparency."""
    img = img.resize((size, size), Image.Resampling.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, size, size), fill=255)
    output = ImageOps.fit(img, (size, size), centering=(0.5, 0.5))
    output.putalpha(mask)
    return output


class PomodoroSession:
    """Main object representing an active Pomodoro session in a voice channel."""
    
    def __init__(
        self,
        cog,
        voice_channel: discord.VoiceChannel,
        timer_channel: discord.abc.GuildChannel,
        notification_channel: discord.abc.GuildChannel,
        focus_length: int,
        break_length: int,
        name: str,
        video_required: bool,
        inactive_threshold: int
    ):
        self.cog = cog
        self.voice_channel = voice_channel
        self.timer_channel = timer_channel
        self.notification_channel = notification_channel
        
        # Core durations in seconds
        self.focus_length = focus_length * 60
        self.break_length = break_length * 60
        self.name = name
        self.video_required = video_required
        self.inactive_threshold = inactive_threshold * 60
        
        self.current_phase = "FOCUS"
        self.phase_start_time = datetime.datetime.now()
        self.phase_end_time = self.phase_start_time + timedelta(seconds=self.focus_length)
        
        self.message = None # Discord message carrying the timer card
        self.active = True
        
        # User tracking dictionaries
        self.join_times = {} # user_id -> datetime of VC join
        self.inactive_times = {} # user_id -> datetime when camera disabled
        self.warned_users = set() # user_ids warned for video inactivity
        
        # Initialize join times for members already in the VC
        now = datetime.datetime.now()
        for member in voice_channel.members:
            if not member.bot:
                self.join_times[member.id] = now
                if self.video_required and not member.voice.self_video:
                    self.inactive_times[member.id] = now

        # Start the update task loop
        self.task = asyncio.create_task(self.update_loop())

    async def update_loop(self):
        """Asynchronous update loop ticking every 30 seconds to refresh card and warn/kick inactive users."""
        await asyncio.sleep(5) # Let initial command response send first
        
        while self.active:
            try:
                now = datetime.datetime.now()
                remaining = int((self.phase_end_time - now).total_seconds())
                
                # Check for Phase Transition
                if remaining <= 0:
                    if self.current_phase == "FOCUS":
                        self.current_phase = "BREAK"
                        self.phase_end_time = now + timedelta(seconds=self.break_length)
                        try:
                            await self.notification_channel.send(
                                f"🔔 **{self.name}** Focus session finished! Time for a **{self.break_length // 60} minutes** break. 🟢"
                            )
                        except Exception:
                            pass
                    else:
                        self.current_phase = "FOCUS"
                        self.phase_end_time = now + timedelta(seconds=self.focus_length)
                        try:
                            await self.notification_channel.send(
                                f"🔴 **{self.name}** Break session finished! Back to focus for **{self.focus_length // 60} minutes**. 🚀"
                            )
                        except Exception:
                            pass
                            
                    self.phase_start_time = now
                    remaining = int((self.phase_end_time - now).total_seconds())

                # Clean tracking for members who left VC
                current_member_ids = {m.id for m in self.voice_channel.members if not m.bot}
                for uid in list(self.join_times.keys()):
                    if uid not in current_member_ids:
                        self.join_times.pop(uid, None)
                        self.inactive_times.pop(uid, None)
                        self.warned_users.discard(uid)

                # Track and check camera state (enforce camera rule)
                for member in self.voice_channel.members:
                    if member.bot:
                        continue
                        
                    # Maintain join times if missed
                    if member.id not in self.join_times:
                        self.join_times[member.id] = now

                    if self.video_required:
                        has_video = member.voice and member.voice.self_video
                        if not has_video:
                            if member.id not in self.inactive_times:
                                self.inactive_times[member.id] = now
                            else:
                                inactive_duration = int((now - self.inactive_times[member.id]).total_seconds())
                                
                                # Kick if inactive threshold exceeded
                                if inactive_duration >= self.inactive_threshold:
                                    try:
                                        await member.move_to(None, reason="Pomodoro video stream enforcement failed")
                                        self.inactive_times.pop(member.id, None)
                                        self.join_times.pop(member.id, None)
                                        self.warned_users.discard(member.id)
                                        
                                        # Send DM notification
                                        embed = discord.Embed(
                                            title="❌ Disconnected from voice",
                                            description=(
                                                f"You were disconnected from the voice channel **{self.voice_channel.name}** because "
                                                f"your camera was off for more than {self.inactive_threshold // 60} minutes during study session."
                                            ),
                                            color=discord.Color.red()
                                        )
                                        await member.send(embed=embed)
                                    except Exception as e:
                                        logger.error(f"Failed to kick spam/inactive member {member.name}: {e}")
                                
                                # DM Warning at half threshold time
                                elif inactive_duration >= (self.inactive_threshold / 2):
                                    if member.id not in self.warned_users:
                                        try:
                                            remaining_warn = (self.inactive_threshold - inactive_duration) // 60
                                            embed = discord.Embed(
                                                title="⚠️ Camera Off Warning",
                                                description=(
                                                    f"Your camera is off in **{self.voice_channel.name}**. "
                                                    f"Please turn it on within **{remaining_warn} minutes** or you will be kicked from the voice channel."
                                                ),
                                                color=discord.Color.gold()
                                            )
                                            await member.send(embed=embed)
                                            self.warned_users.add(member.id)
                                        except Exception:
                                            pass
                        else:
                            # User has video on, clear trackings
                            self.inactive_times.pop(member.id, None)
                            self.warned_users.discard(member.id)

                # Generate Pillow Timer Card image
                file = await self.generate_card_file(remaining)
                
                embed = discord.Embed(
                    title=f"⏳ Active Study Timer: {self.name}",
                    description=f"Active in: {self.voice_channel.mention} | Focus: {self.focus_length//60}m | Break: {self.break_length//60}m",
                    color=discord.Color.gold() if self.current_phase == "FOCUS" else discord.Color.green()
                )
                embed.set_image(url="attachment://timer.png")
                
                # Edit status message
                if self.message:
                    try:
                        await self.message.edit(embed=embed, attachments=[file])
                    except Exception as e:
                        logger.error(f"Failed to edit pomodoro message card: {e}")
                        
            except Exception as e:
                logger.error(f"Error in Pomodoro update loop: {e}")
                
            await asyncio.sleep(30) # Refresh every 30 seconds

    async def generate_card_file(self, remaining_seconds: int) -> discord.File:
        """Generates the aesthetic PIL study card and returns it as a discord.File attachment."""
        # Create blank canvas: 1120x620 dark navy
        canvas = Image.new("RGBA", (1120, 620), color=(13, 25, 33, 255))
        draw = ImageDraw.Draw(canvas)
        
        # Load fonts
        font_title = get_font(38)
        font_timer = get_font(100)
        font_header_bold = get_font(26)
        font_label = get_font(24)
        font_sub = get_font(18)
        
        # Draw Header Bar (Dark slate blue banner)
        draw.rectangle([(0, 0), (1120, 85)], fill=(15, 30, 40, 255))
        
        # Render Session Title in header
        draw.text((35, 18), self.name.upper(), font=font_title, fill=(255, 255, 255, 255))
        
        # Draw Status Badge on the right side of header banner
        badge_text = "FOCUS PHASE" if self.current_phase == "FOCUS" else "BREAK TIME"
        badge_color = (255, 92, 92, 255) if self.current_phase == "FOCUS" else (92, 255, 92, 255)
        # Rounded rect for badge
        draw.rounded_rectangle([(920, 18), (1085, 67)], radius=8, fill=badge_color)
        # Center badge text
        bbox = draw.textbbox((0, 0), badge_text, font=font_sub)
        bw, bh = bbox[2] - bbox[0], bbox[3] - bbox[1]
        bx = 920 + (165 - bw) // 2
        by = 18 + (49 - bh) // 2
        draw.text((bx, by), badge_text, font=font_sub, fill=(0, 0, 0, 255) if self.current_phase == "BREAK" else (255, 255, 255, 255))
        
        # Draw Study Group section (Left column)
        draw.text((35, 115), "STUDY GROUP", font=font_header_bold, fill=(229, 169, 60, 255)) # Gold color
        
        # List VC Members
        y_pos = 165
        members = [m for m in self.voice_channel.members if not m.bot]
        
        # Sort by join time
        members.sort(key=lambda m: self.join_times.get(m.id, datetime.datetime.now()))
        
        # Draw up to 5 members
        now = datetime.datetime.now()
        for idx, member in enumerate(members[:5]):
            # Fetch avatar and crop circular
            pfp = await fetch_avatar(member)
            circle_pfp = crop_circle(pfp, 64)
            canvas.alpha_composite(circle_pfp, (35, y_pos))
            
            # Username
            username = member.display_name
            draw.text((115, y_pos + 6), username, font=font_label, fill=(255, 255, 255, 255))
            
            # Joined duration
            join_time = self.join_times.get(member.id, now)
            duration_mins = int((now - join_time).total_seconds() // 60)
            duration_str = f"Joined {duration_mins}m ago" if duration_mins > 0 else "Joined just now"
            
            # Check camera state for display icon indicator
            cam_str = ""
            if self.video_required:
                cam_str = " | [CAM ON]" if member.voice.self_video else " | [CAM OFF]"
                
            draw.text((115, y_pos + 36), f"{duration_str}{cam_str}", font=font_sub, fill=(176, 196, 222, 255))
            
            y_pos += 80

        # Display offset if more than 5 members are present
        if len(members) > 5:
            draw.text((35, y_pos + 10), f"+ {len(members) - 5} more participants", font=font_sub, fill=(229, 169, 60, 255))

        # --- Draw Aesthetic Ring Timer (Right column) ---
        RING_CX = 830
        RING_CY = 350
        RING_R = 170
        
        # Define bounding box
        ring_box = [(RING_CX - RING_R, RING_CY - RING_R), (RING_CX + RING_R, RING_CY + RING_R)]
        
        # Background arc circle (dark slate blue)
        draw.arc(ring_box, start=0, end=360, fill=(35, 45, 55, 255), width=22)
        
        # Calculate ratio of remaining time
        total = self.focus_length if self.current_phase == "FOCUS" else self.break_length
        ratio = max(0.0, min(1.0, remaining_seconds / total))
        
        # Progress Arc
        start_angle = -90
        end_angle = -90 + int(360 * ratio)
        draw.arc(ring_box, start=start_angle, end=end_angle, fill=badge_color, width=22)
        
        # Format remaining time
        rem_min = remaining_seconds // 60
        rem_sec = remaining_seconds % 60
        time_str = f"{rem_min:02}:{rem_sec:02}"
        
        # Center remaining clock digits inside the ring
        bbox = draw.textbbox((0, 0), time_str, font=font_timer)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        tx = RING_CX - tw // 2
        ty = RING_CY - th // 2 - 15
        draw.text((tx, ty), time_str, font=font_timer, fill=(255, 255, 255, 255))
        
        # Phase Sub-title below clock digits
        phase_sub = "REMAINING"
        bbox = draw.textbbox((0, 0), phase_sub, font=font_sub)
        sw, sh = bbox[2] - bbox[0], bbox[3] - bbox[1]
        sx = RING_CX - sw // 2
        sy = RING_CY + th // 2 + 5
        draw.text((sx, sy), phase_sub, font=font_sub, fill=(176, 196, 222, 255))
        
        # Save to buffer
        fp = io.BytesIO()
        canvas.save(fp, format="PNG")
        fp.seek(0)
        
        return discord.File(fp, filename="timer.png")

    def stop(self):
        """Cancels updates and stops the Pomodoro session."""
        self.active = False
        self.task.cancel()


class Pomodoro(commands.Cog):
    """Cog running pomodoro timers and camera requirements per voice channel."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sessions = {} # voice_channel_id -> PomodoroSession

    @app_commands.command(name="pomodoro", description="Start a Pomodoro study session in your voice channel.")
    @app_commands.describe(
        focus_length="Focus duration in minutes (e.g. 25).",
        break_length="Break duration in minutes (e.g. 5).",
        name="Name of this study session.",
        timer_channel="The text channel where the timer status card will be refreshed.",
        notification_channel="The channel where phase start alerts will be posted.",
        video_required="Enforce camera sharing (True/False).",
        inactive_threshold="Allowed camera-off time in minutes before kick (e.g. 2)."
    )
    async def pomodoro(
        self,
        interaction: discord.Interaction,
        focus_length: int,
        break_length: int,
        name: str,
        timer_channel: discord.abc.GuildChannel,
        notification_channel: discord.abc.GuildChannel,
        video_required: bool,
        inactive_threshold: int
    ):
        # Enforce command check: only allowed in server VC
        if not interaction.guild:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return

        # Check if caller is in voice channel
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("❌ You must be connected to a voice channel to start a Pomodoro session!", ephemeral=True)
            return

        voice_channel = interaction.user.voice.channel
        
        # Validate selected channels are text-writable
        # Voice channels and Text channels both support interaction/sending messages in discord.py v2
        if not isinstance(timer_channel, (discord.TextChannel, discord.VoiceChannel)) or \
           not isinstance(notification_channel, (discord.TextChannel, discord.VoiceChannel)):
            await interaction.response.send_message("❌ The selected timer and notification channels must support message postings.", ephemeral=True)
            return

        # Check for existing session in this voice channel
        if voice_channel.id in self.sessions:
            await interaction.response.send_message(
                f"❌ A Pomodoro session is already active in **{voice_channel.name}**.",
                ephemeral=True
            )
            return

        # Defer response as rendering/database setup takes a split second
        await interaction.response.defer(ephemeral=False)

        # Create session
        session = PomodoroSession(
            cog=self,
            voice_channel=voice_channel,
            timer_channel=timer_channel,
            notification_channel=notification_channel,
            focus_length=focus_length,
            break_length=break_length,
            name=name,
            video_required=video_required,
            inactive_threshold=inactive_threshold
        )
        self.sessions[voice_channel.id] = session

        # Generate initial status card
        file = await session.generate_card_file(focus_length * 60)
        
        embed = discord.Embed(
            title=f"⏳ Active Study Timer: {session.name}",
            description=f"Active in: {voice_channel.mention} | Focus: {focus_length}m | Break: {break_length}m",
            color=discord.Color.gold()
        )
        embed.set_image(url="attachment://timer.png")

        try:
            # Post initial card and save the message object
            msg = await timer_channel.send(embed=embed, file=file)
            session.message = msg
            
            # Send initial focus notification
            await notification_channel.send(
                f"🔴 **Focus session started for {voice_channel.mention}!** Back to focus for **{focus_length} minutes**. 🚀"
            )
            
            # Update caller
            await interaction.followup.send(
                f"✅ **Pomodoro session successfully started!**\n"
                f"• **Voice Room:** {voice_channel.mention}\n"
                f"• **Timer updates in:** {timer_channel.mention}\n"
                f"• **Camera Check:** {'Enabled' if video_required else 'Disabled'} (Threshold: {inactive_threshold}m)"
            )
        except Exception as e:
            # Cleanup on failure
            session.stop()
            self.sessions.pop(voice_channel.id, None)
            await interaction.followup.send(f"❌ Failed to start session: {e}")

    @app_commands.command(name="timer", description="Display details of the Pomodoro session in your voice channel.")
    async def timer(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return

        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("❌ You must be connected to a voice channel to check the timer.", ephemeral=True)
            return

        vc_id = interaction.user.voice.channel.id
        session = self.sessions.get(vc_id)
        
        if not session:
            await interaction.response.send_message("❌ No active Pomodoro session was found in your voice channel.", ephemeral=True)
            return

        now = datetime.datetime.now()
        remaining = int((session.phase_end_time - now).total_seconds())
        rem_min = max(0, remaining // 60)
        rem_sec = max(0, remaining % 60)

        embed = discord.Embed(
            title=f"⏱️ Study Status — {session.name}",
            description=f"**VC Room:** {interaction.user.voice.channel.mention}",
            color=discord.Color.gold() if session.current_phase == "FOCUS" else discord.Color.green()
        )
        embed.add_field(name="Current Phase", value=f"🔴 **{session.current_phase}**", inline=True)
        embed.add_field(name="Remaining Time", value=f"⏳ **{rem_min:02}:{rem_sec:02}**", inline=True)
        embed.add_field(name="Enforce Camera", value="✅ Yes" if session.video_required else "❌ No", inline=True)
        
        if session.message:
            embed.add_field(name="Timer Dashboard", value=f"[Jump to Dashboard]({session.message.jump_url})", inline=False)
            
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """Listener handling automatic join/leave updates and camera toggles."""
        if member.bot:
            return

        now = datetime.datetime.now()
        
        # Member changed voice channel
        if before.channel != after.channel:
            # Left a voice channel
            if before.channel and before.channel.id in self.sessions:
                session = self.sessions[before.channel.id]
                session.join_times.pop(member.id, None)
                session.inactive_times.pop(member.id, None)
                session.warned_users.discard(member.id)
                
                # If voice channel becomes completely empty, stop and cleanup session
                non_bot_members = [m for m in before.channel.members if not m.bot]
                if not non_bot_members:
                    session.stop()
                    self.sessions.pop(before.channel.id, None)
                    try:
                        await session.notification_channel.send(
                            f"ℹ️ Pomodoro session in **{before.channel.name}** has ended because the channel became empty."
                        )
                    except Exception:
                        pass

            # Joined a voice channel
            if after.channel and after.channel.id in self.sessions:
                session = self.sessions[after.channel.id]
                session.join_times[member.id] = now
                if session.video_required and not after.self_video:
                    session.inactive_times[member.id] = now

        # Member toggled camera stream in same channel
        elif before.channel == after.channel and after.channel and after.channel.id in self.sessions:
            session = self.sessions[after.channel.id]
            if session.video_required:
                if before.self_video != after.self_video:
                    if not after.self_video:
                        # Video turned off
                        session.inactive_times[member.id] = now
                    else:
                        # Video turned on
                        session.inactive_times.pop(member.id, None)
                        session.warned_users.discard(member.id)

async def setup(bot: commands.Bot):
    await bot.add_cog(Pomodoro(bot))
