import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import time
import io
import math
import logging

logger = logging.getLogger("StudyBot")

# Pillow import - soft fail so the bot still starts if Pillow is missing
try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False
    logger.warning("Pillow not installed. Visual image cards will be disabled.")

# ─────────────────────────────────────────────
#  Colour palette
# ─────────────────────────────────────────────
BG_DARK      = (15,  17,  27)
BG_CARD      = (22,  25,  40)
BG_HEADER    = (30,  34,  58)
ACCENT_GOLD  = (212, 175,  55)
ACCENT_RED   = (220,  80,  80)
ACCENT_GREEN = ( 80, 200, 120)
ACCENT_BLUE  = ( 80, 140, 220)
TEXT_MAIN    = (240, 240, 255)
TEXT_SUB     = (160, 165, 200)
GRID_COLOUR  = ( 40,  45,  70)

CARD_W, CARD_H = 860, 420


class PomodoroTimer:
    def __init__(self, guild_id, voice_channel, text_channel, focus_length, break_length, name="Focus Session"):
        self.guild_id        = guild_id
        self.voice_channel   = voice_channel
        self.text_channel    = text_channel
        self.focus_length    = focus_length   # minutes
        self.break_length    = break_length   # minutes
        self.name            = name

        self.state           = "idle"   # idle | focus | break
        self.time_left       = 0        # seconds
        self.current_cycle   = 1
        self.task            = None
        self.status_message  = None

        # Presence / AFK tracking
        self.present_members        = set()   # user IDs who pressed Present
        self.missed_cycles          = {}      # user_id -> missed count
        self.inactivity_threshold   = 3
        self.voice_alerts           = True
        self.original_channel_name  = voice_channel.name

        # Study-duration tracking  (user_id -> seconds)
        self.session_seconds: dict[int, float] = {}
        self._voice_join_time: dict[int, float] = {}  # user_id -> monotonic join timestamp

    # ── snapshot current members when the period starts ──────────────────
    def snapshot_voice_members(self):
        """Record join times for everyone currently in the channel."""
        now = time.monotonic()
        for member in self.voice_channel.members:
            if not member.bot:
                self._voice_join_time[member.id] = now

    # ── called when someone joins/leaves during a session ─────────────────
    def record_join(self, user_id: int):
        self._voice_join_time[user_id] = time.monotonic()

    def record_leave(self, user_id: int):
        joined = self._voice_join_time.pop(user_id, None)
        if joined is not None:
            elapsed = time.monotonic() - joined
            self.session_seconds[user_id] = self.session_seconds.get(user_id, 0) + elapsed

    def flush_current_members(self):
        """Flush durations for members still in the channel."""
        now = time.monotonic()
        for uid, join_t in list(self._voice_join_time.items()):
            elapsed = now - join_t
            self.session_seconds[uid] = self.session_seconds.get(uid, 0) + elapsed
            self._voice_join_time[uid] = now  # reset baseline

    def sorted_participants(self) -> list[tuple[int, float]]:
        """Return (user_id, seconds) sorted descending by duration."""
        self.flush_current_members()
        return sorted(self.session_seconds.items(), key=lambda x: x[1], reverse=True)


# ──────────────────────────────────────────────────────────────────────────────
#  Image-card generation (Pillow)
# ──────────────────────────────────────────────────────────────────────────────

def _fmt_dur(seconds: float) -> str:
    h = int(seconds) // 3600
    m = (int(seconds) % 3600) // 60
    s = int(seconds) % 60
    if h:
        return f"{h}h {m:02d}m"
    return f"{m}m {s:02d}s"


def _circle_crop(img: "Image.Image", size: int) -> "Image.Image":
    """Resize image and apply a circular mask."""
    img = img.convert("RGBA").resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    result.paste(img, mask=mask)
    return result


def _draw_ring(draw: "ImageDraw.ImageDraw", cx: int, cy: int, r: int,
               fraction: float, state: str, thickness: int = 14):
    """Draw a background track ring and a coloured progress arc."""
    bbox = [cx - r, cy - r, cx + r, cy + r]
    # Track
    draw.arc(bbox, 0, 360, fill=GRID_COLOUR, width=thickness)
    if fraction <= 0:
        return
    colour = ACCENT_RED if state == "focus" else ACCENT_GREEN if state == "break" else ACCENT_BLUE
    start_angle = -90
    end_angle   = start_angle + 360 * fraction
    draw.arc(bbox, start_angle, end_angle, fill=colour, width=thickness)


def _try_font(size: int, bold: bool = False) -> "ImageFont.ImageFont":
    """Best-effort font loader; falls back to default."""
    candidates = [
        "c:/Windows/Fonts/segoeui.ttf",
        "c:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    if bold:
        candidates = [
            "c:/Windows/Fonts/segoeuib.ttf",
            "c:/Windows/Fonts/arialbd.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ] + candidates
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def build_status_image(
    timer: "PomodoroTimer",
    avatar_images: dict[int, "Image.Image"],  # user_id -> RGBA PIL image (already fetched)
    guild: discord.Guild,
) -> io.BytesIO:
    """
    Build a 860×420 dark-theme Pomodoro status card and return it as a PNG
    BytesIO ready to be sent as a Discord file attachment.
    """
    img = Image.new("RGB", (CARD_W, CARD_H), BG_DARK)
    draw = ImageDraw.Draw(img)

    # ── Gold grid lines ──────────────────────────────────────────────────
    GRID_STEP = 40
    for x in range(0, CARD_W, GRID_STEP):
        draw.line([(x, 0), (x, CARD_H)], fill=GRID_COLOUR, width=1)
    for y in range(0, CARD_H, GRID_STEP):
        draw.line([(0, y), (CARD_W, y)], fill=GRID_COLOUR, width=1)

    # ── Header tab ───────────────────────────────────────────────────────
    HEADER_H = 56
    draw.rectangle([0, 0, CARD_W, HEADER_H], fill=BG_HEADER)
    # Gold accent bar below header
    draw.rectangle([0, HEADER_H - 3, CARD_W, HEADER_H], fill=ACCENT_GOLD)

    font_h1  = _try_font(22, bold=True)
    font_h2  = _try_font(14)
    font_big = _try_font(40, bold=True)
    font_sm  = _try_font(13)
    font_xs  = _try_font(11)

    state_label = {"focus": "🎯  FOCUS", "break": "☕  BREAK", "idle": "💤  IDLE"}.get(timer.state, "IDLE")
    cycle_label = f"Cycle #{timer.current_cycle}"

    draw.text((20, 10), f"⏱  {timer.name}", font=font_h1, fill=TEXT_MAIN)
    draw.text((20, 36), f"{state_label}   ·   {cycle_label}", font=font_h2, fill=TEXT_SUB)

    # ── Timer ring (left column) ─────────────────────────────────────────
    RING_CX, RING_CY, RING_R = 140, 240, 100

    total = (timer.focus_length if timer.state == "focus" else timer.break_length) * 60 if timer.state != "idle" else 1
    elapsed_in_phase = total - timer.time_left if timer.state != "idle" else 0
    fraction = (elapsed_in_phase / total) if total > 0 else 0
    fraction = max(0.0, min(1.0, fraction))

    _draw_ring(draw, RING_CX, RING_CY, RING_R, fraction, timer.state, thickness=16)

    # Timer text inside ring
    mins    = timer.time_left // 60
    secs    = timer.time_left % 60
    time_str = f"{mins:02d}:{secs:02d}"

    # Centre the big time text
    bbox = draw.textbbox((0, 0), time_str, font=font_big)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((RING_CX - tw // 2, RING_CY - th // 2 - 6), time_str, font=font_big, fill=TEXT_MAIN)

    phase_short = {"focus": "remaining", "break": "break left", "idle": "paused"}[timer.state]
    bbox2 = draw.textbbox((0, 0), phase_short, font=font_xs)
    pw = bbox2[2] - bbox2[0]
    draw.text((RING_CX - pw // 2, RING_CY + th // 2 + 4), phase_short, font=font_xs, fill=TEXT_SUB)

    # ── Divider ──────────────────────────────────────────────────────────
    DIV_X = 270
    draw.rectangle([DIV_X, HEADER_H + 10, DIV_X + 1, CARD_H - 10], fill=GRID_COLOUR)

    # ── Members panel (right column) ─────────────────────────────────────
    PANEL_X   = DIV_X + 18
    PANEL_Y   = HEADER_H + 18
    AVATAR_SZ = 36
    ROW_H     = 50
    MAX_ROWS  = 6

    draw.text((PANEL_X, PANEL_Y), "STUDY PARTICIPANTS", font=font_xs, fill=ACCENT_GOLD)
    PANEL_Y += 20

    participants = timer.sorted_participants()[:MAX_ROWS]

    if not participants:
        draw.text((PANEL_X, PANEL_Y + 20), "No participants yet…", font=font_sm, fill=TEXT_SUB)
    else:
        for rank, (uid, secs) in enumerate(participants):
            row_y = PANEL_Y + rank * ROW_H

            # Avatar circle
            av_img = avatar_images.get(uid)
            if av_img:
                circ = _circle_crop(av_img, AVATAR_SZ)
                img.paste(circ, (PANEL_X, row_y), circ)
            else:
                # Placeholder circle
                draw.ellipse(
                    [PANEL_X, row_y, PANEL_X + AVATAR_SZ, row_y + AVATAR_SZ],
                    fill=(50, 55, 80)
                )

            # Member display name
            member = guild.get_member(uid)
            name_str = member.display_name if member else f"User {uid}"
            if len(name_str) > 20:
                name_str = name_str[:18] + "…"

            # Duration pill
            dur_str = _fmt_dur(secs)
            pill_colour = ACCENT_RED if timer.state == "focus" else ACCENT_GREEN

            TEXT_X = PANEL_X + AVATAR_SZ + 10
            draw.text((TEXT_X, row_y + 2), name_str, font=font_sm, fill=TEXT_MAIN)

            # Draw duration pill
            pill_text = dur_str
            pill_bbox = draw.textbbox((0, 0), pill_text, font=font_xs)
            pill_w    = (pill_bbox[2] - pill_bbox[0]) + 16
            pill_h    = 18
            pill_x    = TEXT_X
            pill_y    = row_y + 22
            draw.rounded_rectangle(
                [pill_x, pill_y, pill_x + pill_w, pill_y + pill_h],
                radius=9, fill=pill_colour
            )
            draw.text((pill_x + 8, pill_y + 2), pill_text, font=font_xs, fill=(10, 10, 10))

    # ── Footer bar ───────────────────────────────────────────────────────
    draw.rectangle([0, CARD_H - 28, CARD_W, CARD_H], fill=BG_HEADER)
    footer_text = "📌 Press Present to confirm you're active!  ·  Created by StudyBot"
    draw.text((16, CARD_H - 20), footer_text, font=font_xs, fill=TEXT_SUB)

    # ── Export ───────────────────────────────────────────────────────────
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


# ──────────────────────────────────────────────────────────────────────────────
#  Cog
# ──────────────────────────────────────────────────────────────────────────────

class PomodoroCog(commands.Cog):
    def __init__(self, bot):
        self.bot           = bot
        self.active_timers: dict[int, PomodoroTimer] = {}  # voice_channel_id -> timer

    # ── Voice state tracking ─────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        if member.bot:
            return

        # Check if member left a tracked channel
        if before.channel:
            timer = self.active_timers.get(before.channel.id)
            if timer and timer.state != "idle":
                timer.record_leave(member.id)

        # Check if member joined a tracked channel
        if after.channel:
            timer = self.active_timers.get(after.channel.id)
            if timer and timer.state != "idle":
                timer.record_join(member.id)

    # ── Avatar fetching ──────────────────────────────────────────────────
    async def _fetch_avatars(self, user_ids: list[int]) -> dict[int, "Image.Image"]:
        """Fetch and decode avatars from Discord CDN into PIL Images."""
        if not PILLOW_AVAILABLE:
            return {}
        result = {}
        for uid in user_ids:
            try:
                user = self.bot.get_user(uid)
                if not user:
                    user = await self.bot.fetch_user(uid)
                if not user:
                    continue
                url = str(user.display_avatar.replace(size=64, format="webp"))
                # aiohttp via discord.py's internal session
                async with self.bot.http._HTTPClient__session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        result[uid] = Image.open(io.BytesIO(data)).convert("RGBA")
            except Exception as e:
                logger.debug(f"Could not fetch avatar for {uid}: {e}")
        return result

    # ── Interactive View ──────────────────────────────────────────────────
    class PomodoroView(discord.ui.View):
        def __init__(self, timer: "PomodoroTimer", cog: "PomodoroCog"):
            super().__init__(timeout=None)
            self.timer = timer
            self.cog   = cog

        @discord.ui.button(label="Start", style=discord.ButtonStyle.success, emoji="▶")
        async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            if self.timer.state != "idle":
                await interaction.response.send_message("Timer is already running!", ephemeral=True)
                return

            self.timer.state     = "focus"
            self.timer.time_left = self.timer.focus_length * 60
            self.timer.present_members.clear()
            self.timer.snapshot_voice_members()
            self.timer.task = asyncio.create_task(self.cog.run_timer(self.timer))

            await interaction.response.defer()
            await self.cog.update_status_card(self.timer)

        @discord.ui.button(label="Present", style=discord.ButtonStyle.primary, emoji="✅")
        async def present_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user not in self.timer.voice_channel.members:
                await interaction.response.send_message(
                    "You must be in the Pomodoro voice channel to check in!", ephemeral=True
                )
                return
            uid = interaction.user.id
            self.timer.present_members.add(uid)
            self.timer.missed_cycles[uid] = 0
            await interaction.response.send_message(
                f"Thanks {interaction.user.display_name}, you are marked **Present**! 🎯", ephemeral=True
            )

        @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, emoji="⏹")
        async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            if self.timer.state == "idle":
                await interaction.response.send_message("Timer is not running!", ephemeral=True)
                return

            if self.timer.task:
                self.timer.task.cancel()
                self.timer.task = None

            # Flush durations before stopping
            self.timer.flush_current_members()

            self.timer.state     = "idle"
            self.timer.time_left = 0

            try:
                await self.timer.voice_channel.edit(name=self.timer.original_channel_name)
            except Exception:
                pass

            await interaction.response.defer()
            await self.cog.update_status_card(self.timer)

    # ── Timer loop ────────────────────────────────────────────────────────
    async def run_timer(self, timer: PomodoroTimer):
        try:
            if timer.voice_alerts:
                await self.play_alert(timer.voice_channel, "focus_start")
            await self.update_channel_title(timer)

            last_channel_update = 0

            while timer.state != "idle":
                await asyncio.sleep(1)
                timer.time_left -= 1

                if timer.time_left <= 0:
                    if timer.state == "focus":
                        await self.check_afk_and_kick(timer)
                        timer.flush_current_members()

                        timer.state     = "break"
                        timer.time_left = timer.break_length * 60
                        timer.present_members.clear()
                        timer.snapshot_voice_members()

                        if timer.voice_alerts:
                            await self.play_alert(timer.voice_channel, "break_start")
                    else:
                        timer.flush_current_members()

                        timer.state       = "focus"
                        timer.time_left   = timer.focus_length * 60
                        timer.current_cycle += 1
                        timer.present_members.clear()
                        timer.snapshot_voice_members()

                        if timer.voice_alerts:
                            await self.play_alert(timer.voice_channel, "focus_start")

                    await self.update_status_card(timer)
                    await self.update_channel_title(timer)

                # Periodic updates (every 30 seconds)
                if timer.time_left % 30 == 0:
                    await self.update_status_card(timer)
                    now = time.time()
                    if now - last_channel_update > 120:
                        await self.update_channel_title(timer)
                        last_channel_update = now

        except asyncio.CancelledError:
            pass

    # ── Status card update ────────────────────────────────────────────────
    async def update_status_card(self, timer: PomodoroTimer):
        embed = discord.Embed(
            title=f"⏱️ Pomodoro: {timer.name}",
            color=(
                discord.Color.red()        if timer.state == "focus" else
                discord.Color.green()      if timer.state == "break" else
                discord.Color.light_grey()
            )
        )

        mins    = timer.time_left // 60
        secs    = timer.time_left % 60
        time_str = f"{mins:02d}:{secs:02d}"

        if timer.state == "focus":
            embed.description = (
                f"🎯 **Focus Period (Cycle #{timer.current_cycle})**\n"
                f"Keep working, stay off distractions!\n\n"
                f"⏱️ Time remaining: **{time_str}**"
            )
            embed.set_footer(text="Click 'Present' to confirm you are active!")
        elif timer.state == "break":
            embed.description = (
                f"☕ **Break Period**\nRelax, stand up, stretch!\n\n"
                f"⏱️ Time remaining: **{time_str}**"
            )
            embed.set_footer(text="Next focus cycle starts automatically.")
        else:
            embed.description = (
                "💤 **Timer is currently IDLE.**\n"
                "Press 'Start' to begin the Pomodoro sessions."
            )
            embed.set_footer(text="Default cycle: 25m Focus / 5m Break")

        view  = self.PomodoroView(timer, self)
        file  = None

        # Generate image card if Pillow is available
        if PILLOW_AVAILABLE:
            try:
                guild         = self.bot.get_guild(timer.guild_id)
                participant_ids = [uid for uid, _ in timer.sorted_participants()]
                avatar_images = await self._fetch_avatars(participant_ids[:6])
                img_buf = await asyncio.get_event_loop().run_in_executor(
                    None, build_status_image, timer, avatar_images, guild
                )
                file = discord.File(img_buf, filename="pomodoro_card.png")
                embed.set_image(url="attachment://pomodoro_card.png")
            except Exception as e:
                logger.warning(f"Failed to generate status image: {e}")

        try:
            if file:
                if timer.status_message:
                    # Re-send because you can't edit file attachments in place
                    try:
                        await timer.status_message.delete()
                    except Exception:
                        pass
                    timer.status_message = await timer.text_channel.send(embed=embed, view=view, file=file)
                else:
                    timer.status_message = await timer.text_channel.send(embed=embed, view=view, file=file)
            else:
                if timer.status_message:
                    await timer.status_message.edit(embed=embed, view=view)
                else:
                    timer.status_message = await timer.text_channel.send(embed=embed, view=view)
        except Exception as e:
            logger.debug(f"update_status_card error: {e}")

    # ── Channel title ─────────────────────────────────────────────────────
    async def update_channel_title(self, timer: PomodoroTimer):
        try:
            if timer.state == "focus":
                mins = timer.time_left // 60
                await timer.voice_channel.edit(name=f"🎯 Focus | {mins}m left")
            elif timer.state == "break":
                mins = timer.time_left // 60
                await timer.voice_channel.edit(name=f"☕ Break | {mins}m left")
            else:
                await timer.voice_channel.edit(name=timer.original_channel_name)
        except Exception:
            pass

    # ── AFK check ────────────────────────────────────────────────────────
    async def check_afk_and_kick(self, timer: PomodoroTimer):
        for member in timer.voice_channel.members:
            if member.bot:
                continue
            uid = member.id
            if uid not in timer.present_members:
                timer.missed_cycles[uid] = timer.missed_cycles.get(uid, 0) + 1
                if timer.missed_cycles[uid] >= timer.inactivity_threshold:
                    try:
                        await member.move_to(None, reason="AFK during Pomodoro Focus cycle.")
                        embed = discord.Embed(
                            title="🛏️ Disconnected due to Inactivity",
                            description=(
                                "You were disconnected from the Pomodoro voice channel because you missed "
                                "multiple 'Present' checks. Remember to confirm you are active!"
                            ),
                            color=discord.Color.orange()
                        )
                        await member.send(embed=embed)
                    except Exception:
                        pass
            else:
                timer.missed_cycles[uid] = 0

    # ── Voice alerts ──────────────────────────────────────────────────────
    async def play_alert(self, voice_channel, alert_type: str):
        embed = discord.Embed(color=discord.Color.blurple())
        if alert_type == "focus_start":
            embed.title       = "🎯 Focus Time Has Started!"
            embed.description = "Quiet down and begin studying. Good luck!"
        elif alert_type == "break_start":
            embed.title       = "☕ Break Time Has Started!"
            embed.description = "Step away from your screen, stretch, and grab a drink!"
        try:
            await voice_channel.send(embed=embed, tts=True)
        except Exception:
            pass

    # ── Slash Commands ────────────────────────────────────────────────────
    @app_commands.command(name="pomodoro", description="Create a Pomodoro timer in your voice channel.")
    @app_commands.describe(
        focus_length="Length of focus period in minutes (default 25)",
        break_length="Length of break period in minutes (default 5)",
        name="Name of focus timer"
    )
    async def pomodoro(
        self,
        interaction: discord.Interaction,
        focus_length: int = 25,
        break_length: int = 5,
        name: str = "Focus Session"
    ):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server!", ephemeral=True)
            return

        voice_state = interaction.user.voice
        if not voice_state or not voice_state.channel:
            await interaction.response.send_message(
                "❌ You must join a voice channel first to attach the Pomodoro timer!", ephemeral=True
            )
            return

        vc = voice_state.channel
        if vc.id in self.active_timers:
            await interaction.response.send_message(
                "❌ A Pomodoro timer is already attached to this voice channel!", ephemeral=True
            )
            return

        timer = PomodoroTimer(
            guild_id=interaction.guild_id,
            voice_channel=vc,
            text_channel=interaction.channel,
            focus_length=focus_length,
            break_length=break_length,
            name=name
        )
        self.active_timers[vc.id] = timer

        embed = discord.Embed(
            title="⏱️ Pomodoro Timer Created",
            description=(
                f"Attached timer **{name}** ({focus_length}m Focus / {break_length}m Break) "
                f"to voice channel **{vc.name}**.\nClick **Start** below to begin."
            ),
            color=discord.Color.blurple()
        )
        await interaction.response.send_message(embed=embed)
        await self.update_status_card(timer)

    @app_commands.command(name="timers", description="List all active Pomodoro timers in the server.")
    async def timers(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server!", ephemeral=True)
            return

        embed = discord.Embed(title="⏱️ Active Pomodoro Timers", color=discord.Color.blue())
        lines = []
        for vc_id, timer in self.active_timers.items():
            if timer.guild_id == interaction.guild_id:
                status = "RUNNING" if timer.state != "idle" else "IDLE"
                lines.append(
                    f"• **{timer.name}** in <#{vc_id}> | Status: `{status}` "
                    f"({timer.focus_length}/{timer.break_length})"
                )

        embed.description = "\n".join(lines) if lines else "There are no active Pomodoro timers in this server."
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(PomodoroCog(bot))
