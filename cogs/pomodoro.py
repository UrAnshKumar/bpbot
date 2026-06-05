import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import time
import io
import math
import logging

logger = logging.getLogger("StudyBot")

# ── Pillow soft-import ─────────────────────────────────────────────────────────
try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False
    logger.warning("Pillow not installed. Visual image cards will be disabled.")

# ── Colour palette (matches the sample screenshot) ────────────────────────────
BG_MAIN        = (8,   18,  38)   # deep navy background
BG_HEADER      = (11,  24,  50)   # slightly lighter header strip
BG_CIRCLE      = (18,  26,  52)   # dark circle behind timer
BG_PILL        = (18,  28,  58)   # member pill background
GOLD           = (212, 175,  55)  # main gold accent
GOLD_DIM       = (140, 110,  30)  # dimmed gold for ring track
WHITE          = (240, 242, 255)  # main text
GREY           = (120, 130, 160)  # subtle text / footer
RED_FOCUS      = (220,  75,  75)  # focus ring colour
GREEN_BREAK    = ( 72, 200, 120)  # break ring colour
IDLE_RING      = ( 50,  60,  90)  # idle ring colour
SEPARATOR      = ( 25,  35,  65)  # thin divider lines

# ── Card dimensions ────────────────────────────────────────────────────────────
CARD_W = 1080
CARD_H = 600
CORNER = 40          # rounded corner radius
HEADER_H = 96        # header strip height
RING_R   = 185       # outer radius of the timer circle
RING_CX  = 800       # timer circle centre-x
RING_CY  = 340       # timer circle centre-y
RING_TRACK_W = 12    # ring stroke width


# ──────────────────────────────────────────────────────────────────────────────
#  PomodoroTimer  (data model)
# ──────────────────────────────────────────────────────────────────────────────
class PomodoroTimer:
    def __init__(self, guild_id, voice_channel, text_channel,
                 focus_length, break_length, name="Focus Session"):
        self.guild_id       = guild_id
        self.voice_channel  = voice_channel
        self.text_channel   = text_channel
        self.focus_length   = focus_length   # minutes
        self.break_length   = break_length   # minutes
        self.name           = name

        self.state          = "idle"   # idle | focus | break
        self.time_left      = 0        # seconds
        self.current_cycle  = 1
        self.task           = None
        self.status_message = None

        # Presence / AFK tracking
        self.present_members       = set()
        self.missed_cycles         = {}
        self.inactivity_threshold  = 3
        self.voice_alerts          = True
        self.original_channel_name = voice_channel.name

        # Study-duration tracking
        self.session_seconds: dict[int, float] = {}
        self._voice_join_time: dict[int, float] = {}

    def snapshot_voice_members(self):
        now = time.monotonic()
        for member in self.voice_channel.members:
            if not member.bot:
                self._voice_join_time[member.id] = now

    def record_join(self, user_id: int):
        self._voice_join_time[user_id] = time.monotonic()

    def record_leave(self, user_id: int):
        joined = self._voice_join_time.pop(user_id, None)
        if joined is not None:
            self.session_seconds[user_id] = (
                self.session_seconds.get(user_id, 0) + (time.monotonic() - joined)
            )

    def flush_current_members(self):
        now = time.monotonic()
        for uid, join_t in list(self._voice_join_time.items()):
            self.session_seconds[uid] = self.session_seconds.get(uid, 0) + (now - join_t)
            self._voice_join_time[uid] = now

    def sorted_participants(self) -> list[tuple[int, float]]:
        self.flush_current_members()
        return sorted(self.session_seconds.items(), key=lambda x: x[1], reverse=True)


# ──────────────────────────────────────────────────────────────────────────────
#  Image-card generation helpers
# ──────────────────────────────────────────────────────────────────────────────

def _fmt_dur(seconds: float) -> str:
    h = int(seconds) // 3600
    m = (int(seconds) % 3600) // 60
    s = int(seconds) % 60
    if h:
        return f"{h}h {m:02d}m"
    return f"{m:02d}:{s:02d}"


def _circle_crop(img: "Image.Image", size: int) -> "Image.Image":
    img = img.convert("RGBA").resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    result.paste(img, mask=mask)
    return result


def _try_font(size: int, bold: bool = False) -> "ImageFont.ImageFont":
    candidates_bold = [
        "c:/Windows/Fonts/segoeuib.ttf",
        "c:/Windows/Fonts/arialbd.ttf",
        "c:/Windows/Fonts/calibrib.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    candidates_reg = [
        "c:/Windows/Fonts/segoeui.ttf",
        "c:/Windows/Fonts/arial.ttf",
        "c:/Windows/Fonts/calibri.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in (candidates_bold if bold else candidates_reg):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _rounded_card_mask(size: tuple[int, int], radius: int) -> "Image.Image":
    """Return an L-mode mask with rounded corners for the card."""
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius=radius, fill=255)
    return mask


def _draw_arc_ring(draw: "ImageDraw.ImageDraw", cx: int, cy: int, r: int,
                   fraction: float, state: str, track_w: int):
    """Draw a circular progress ring (track + coloured arc)."""
    bbox = [cx - r, cy - r, cx + r, cy + r]
    # Track ring
    draw.arc(bbox, 0, 360, fill=GOLD_DIM, width=track_w)
    if fraction <= 0:
        return
    colour = RED_FOCUS if state == "focus" else GREEN_BREAK if state == "break" else IDLE_RING
    draw.arc(bbox, -90, -90 + 360 * fraction, fill=colour, width=track_w)


def _draw_bar_icon(draw: "ImageDraw.ImageDraw", x: int, y: int):
    """Draw the ≡≡≡ stacked-bars icon in gold (3 columns × 4 bars each)."""
    bar_w, bar_h, bar_gap = 18, 4, 6
    col_gap = 10
    for col in range(3):
        cx = x + col * (bar_w + col_gap)
        for row in range(4):
            by = y + row * (bar_h + bar_gap)
            draw.rounded_rectangle(
                [cx, by, cx + bar_w, by + bar_h],
                radius=2, fill=GOLD
            )


def _text_centre(draw, text, font, cx, y, colour):
    """Draw text horizontally centred on cx."""
    bb = draw.textbbox((0, 0), text, font=font)
    w = bb[2] - bb[0]
    draw.text((cx - w // 2, y), text, font=font, fill=colour)


# ──────────────────────────────────────────────────────────────────────────────
#  Main card builder
# ──────────────────────────────────────────────────────────────────────────────
def build_status_image(
    timer: "PomodoroTimer",
    avatar_images: dict[int, "Image.Image"],
    guild: discord.Guild,
) -> io.BytesIO:
    """
    Build a 1080×600 premium dark-navy Pomodoro card (matches the sample).
    Returns a PNG BytesIO ready for discord.File.
    """

    # ── Canvas ────────────────────────────────────────────────────────────
    canvas = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    card   = Image.new("RGBA", (CARD_W, CARD_H), (*BG_MAIN, 255))
    draw   = ImageDraw.Draw(card)

    # ── Fonts ─────────────────────────────────────────────────────────────
    font_name   = _try_font(54, bold=True)   # session name in header
    font_cycle  = _try_font(18)              # "Cycle #N" sub-label
    font_time   = _try_font(92, bold=True)   # big timer digits
    font_phase  = _try_font(26)              # FOCUS / BREAK / IDLE
    font_member = _try_font(22, bold=True)   # member name
    font_pill   = _try_font(18)             # time pill text
    font_footer = _try_font(18)              # footer hint

    # ── Header strip ──────────────────────────────────────────────────────
    draw.rectangle([0, 0, CARD_W, HEADER_H], fill=(*BG_HEADER, 255))
    # Bottom edge of header — thin gold line
    draw.rectangle([0, HEADER_H - 2, CARD_W, HEADER_H], fill=(*GOLD, 255))

    # Bar icons
    ICON_X, ICON_Y = 28, 22
    _draw_bar_icon(draw, ICON_X, ICON_Y)

    # Session name (right of the bars)
    name_text = timer.name if len(timer.name) <= 24 else timer.name[:22] + "…"
    ICON_END_X = ICON_X + 3 * (18 + 10)   # approx right edge of bars (3 cols)
    draw.text((ICON_END_X + 18, 16), name_text, font=font_name, fill=GOLD)

    # Cycle sub-label in header (far right)
    cycle_text = f"Cycle #{timer.current_cycle}"
    bb_c = draw.textbbox((0, 0), cycle_text, font=font_cycle)
    draw.text((CARD_W - (bb_c[2] - bb_c[0]) - 28, 38), cycle_text, font=font_cycle, fill=GREY)

    # ── Background subtle radial glow behind circle (cosmetic) ────────────
    # Draw several concentric low-opacity circles expanding outward
    glow_layer = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow_layer)
    for radius_delta, alpha in [(220, 12), (200, 20), (180, 30)]:
        r = RING_R + radius_delta
        gd.ellipse(
            [RING_CX - r, RING_CY - r, RING_CX + r, RING_CY + r],
            fill=(*BG_CIRCLE, alpha)
        )
    card = Image.alpha_composite(card, glow_layer)
    draw = ImageDraw.Draw(card)

    # ── Big dark circle (timer background) ────────────────────────────────
    CIRCLE_R = RING_R + 6
    draw.ellipse(
        [RING_CX - CIRCLE_R, RING_CY - CIRCLE_R,
         RING_CX + CIRCLE_R, RING_CY + CIRCLE_R],
        fill=(*BG_CIRCLE, 255)
    )

    # ── Progress ring ─────────────────────────────────────────────────────
    total_phase = (
        timer.focus_length * 60 if timer.state == "focus" else
        timer.break_length * 60 if timer.state == "break" else
        1
    )
    elapsed = max(0, total_phase - timer.time_left) if timer.state != "idle" else 0
    fraction = min(1.0, elapsed / total_phase) if total_phase else 0
    _draw_arc_ring(draw, RING_CX, RING_CY, RING_R, fraction, timer.state, RING_TRACK_W)

    # ── Gold dot indicator at 12 o'clock ──────────────────────────────────
    dot_r = 10
    dot_angle = math.radians(-90 + 360 * fraction)   # follows the arc tip
    dot_x = int(RING_CX + RING_R * math.cos(dot_angle))
    dot_y = int(RING_CY + RING_R * math.sin(dot_angle))
    draw.ellipse(
        [dot_x - dot_r, dot_y - dot_r, dot_x + dot_r, dot_y + dot_r],
        fill=GOLD
    )

    # ── Timer digits ──────────────────────────────────────────────────────
    mins = timer.time_left // 60
    secs = timer.time_left % 60
    time_str = f"{mins:02d}:{secs:02d}"

    bb_t = draw.textbbox((0, 0), time_str, font=font_time)
    tw, th = bb_t[2] - bb_t[0], bb_t[3] - bb_t[1]
    draw.text((RING_CX - tw // 2, RING_CY - th // 2 - 18), time_str, font=font_time, fill=GOLD)

    # ── Phase label below digits ──────────────────────────────────────────
    phase_icon = {"focus": "🎯", "break": "☕", "idle": "💤"}[timer.state]
    phase_text = {"focus": "FOCUS", "break": "BREAK", "idle": "IDLE"}[timer.state]
    phase_full = f"{phase_icon}  {phase_text}"
    _text_centre(draw, phase_full, font_phase, RING_CX, RING_CY + th // 2 + 8, WHITE)

    # ── Left panel: member list ────────────────────────────────────────────
    LEFT_X      = 36
    LEFT_Y_TOP  = HEADER_H + 30
    AVATAR_SZ   = 48
    PILL_PAD_X  = 10
    PILL_PAD_Y  = 6
    ROW_GAP     = 70
    MAX_MEMBERS = 6

    participants = timer.sorted_participants()[:MAX_MEMBERS]

    # Section title
    draw.text((LEFT_X, LEFT_Y_TOP), "PARTICIPANTS", font=_try_font(14), fill=GOLD)
    LEFT_Y = LEFT_Y_TOP + 28

    if not participants:
        draw.text((LEFT_X, LEFT_Y + 12), "No one studying yet…", font=font_pill, fill=GREY)
    else:
        for i, (uid, secs_val) in enumerate(participants):
            row_y = LEFT_Y + i * ROW_GAP

            # Avatar circle
            av_img = avatar_images.get(uid)
            if av_img:
                circ = _circle_crop(av_img, AVATAR_SZ)
                card.paste(circ, (LEFT_X, row_y), circ)
            else:
                draw.ellipse(
                    [LEFT_X, row_y, LEFT_X + AVATAR_SZ, row_y + AVATAR_SZ],
                    fill=(*BG_PILL, 255)
                )
                # Initials placeholder
                member_obj = guild.get_member(uid) if guild else None
                init = (member_obj.display_name[0].upper() if member_obj else "?")
                draw.text(
                    (LEFT_X + AVATAR_SZ // 2 - 7, row_y + AVATAR_SZ // 2 - 11),
                    init, font=_try_font(20, bold=True), fill=GOLD
                )

            # Duration pill (right of avatar)
            dur_str = _fmt_dur(secs_val)
            pill_font = _try_font(20, bold=True)
            bb_p = draw.textbbox((0, 0), dur_str, font=pill_font)
            pill_w = (bb_p[2] - bb_p[0]) + PILL_PAD_X * 2
            pill_h = AVATAR_SZ
            pill_x = LEFT_X + AVATAR_SZ + 10
            pill_y = row_y

            draw.rounded_rectangle(
                [pill_x, pill_y, pill_x + pill_w, pill_y + pill_h],
                radius=pill_h // 2,
                fill=(*BG_PILL, 255),
                outline=(*GOLD, 60),
                width=1
            )
            # Text centred inside pill
            draw.text(
                (pill_x + PILL_PAD_X, pill_y + (pill_h - (bb_p[3] - bb_p[1])) // 2),
                dur_str, font=pill_font, fill=GOLD
            )

    # ── Vertical divider between left panel and right circle ──────────────
    DIV_X = 580
    draw.rectangle([DIV_X, HEADER_H + 16, DIV_X + 1, CARD_H - 40], fill=(*SEPARATOR, 255))

    # ── Footer ─────────────────────────────────────────────────────────────
    FOOTER_Y = CARD_H - 36
    footer_msg = "Press ✅ Present to confirm you're active  ·  Use /timers to see all sessions"
    _text_centre(draw, footer_msg, font_footer, CARD_W // 2, FOOTER_Y, GREY)

    # ── Apply rounded-corner mask ──────────────────────────────────────────
    mask = _rounded_card_mask((CARD_W, CARD_H), CORNER)
    canvas.paste(card, (0, 0), mask)

    # ── Export as PNG ──────────────────────────────────────────────────────
    final = canvas.convert("RGB")
    buf = io.BytesIO()
    final.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf


# ──────────────────────────────────────────────────────────────────────────────
#  Cog
# ──────────────────────────────────────────────────────────────────────────────
class PomodoroCog(commands.Cog):
    def __init__(self, bot):
        self.bot           = bot
        self.active_timers: dict[int, PomodoroTimer] = {}

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
        if before.channel:
            timer = self.active_timers.get(before.channel.id)
            if timer and timer.state != "idle":
                timer.record_leave(member.id)
        if after.channel:
            timer = self.active_timers.get(after.channel.id)
            if timer and timer.state != "idle":
                timer.record_join(member.id)

    # ── Avatar fetching ──────────────────────────────────────────────────
    async def _fetch_avatars(self, user_ids: list[int]) -> dict[int, "Image.Image"]:
        if not PILLOW_AVAILABLE:
            return {}
        result = {}
        for uid in user_ids:
            try:
                user = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
                if not user:
                    continue
                url = str(user.display_avatar.replace(size=128, format="webp"))
                async with self.bot.http._HTTPClient__session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        result[uid] = Image.open(io.BytesIO(data)).convert("RGBA")
            except Exception as e:
                logger.debug(f"Avatar fetch failed for {uid}: {e}")
        return result

    # ── Interactive buttons ────────────────────────────────────────────────
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
                f"✅ {interaction.user.display_name}, you are marked **Present**! 🎯", ephemeral=True
            )

        @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, emoji="⏹")
        async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            if self.timer.state == "idle":
                await interaction.response.send_message("Timer is not running!", ephemeral=True)
                return
            if self.timer.task:
                self.timer.task.cancel()
                self.timer.task = None
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

            last_ch_update = 0

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
                        timer.state         = "focus"
                        timer.time_left     = timer.focus_length * 60
                        timer.current_cycle += 1
                        timer.present_members.clear()
                        timer.snapshot_voice_members()
                        if timer.voice_alerts:
                            await self.play_alert(timer.voice_channel, "focus_start")

                    await self.update_status_card(timer)
                    await self.update_channel_title(timer)

                if timer.time_left % 30 == 0:
                    await self.update_status_card(timer)
                    now = time.time()
                    if now - last_ch_update > 120:
                        await self.update_channel_title(timer)
                        last_ch_update = now

        except asyncio.CancelledError:
            pass

    # ── Status card ───────────────────────────────────────────────────────
    async def update_status_card(self, timer: PomodoroTimer):
        colour_map = {
            "focus": discord.Color.from_rgb(*RED_FOCUS),
            "break": discord.Color.from_rgb(*GREEN_BREAK),
            "idle":  discord.Color.light_grey(),
        }
        embed = discord.Embed(
            title=f"⏱️ {timer.name}",
            color=colour_map.get(timer.state, discord.Color.light_grey())
        )

        mins, secs = timer.time_left // 60, timer.time_left % 60
        time_str   = f"{mins:02d}:{secs:02d}"

        if timer.state == "focus":
            embed.description = (
                f"🎯 **Focus Period — Cycle #{timer.current_cycle}**\n"
                f"Stay focused and off distractions!\n\n"
                f"⏱️ **{time_str}** remaining"
            )
            embed.set_footer(text="Click ✅ Present to confirm you are active!")
        elif timer.state == "break":
            embed.description = (
                f"☕ **Break Period**\nStretch, hydrate, and relax!\n\n"
                f"⏱️ **{time_str}** until next focus"
            )
            embed.set_footer(text="Next focus cycle starts automatically.")
        else:
            embed.description = (
                "💤 **Timer is IDLE** — press **▶ Start** below to begin.\n\n"
                f"Session: **{timer.focus_length}m** focus / **{timer.break_length}m** break"
            )
            embed.set_footer(text="Pomodoro technique: focused work + regular breaks.")

        view = self.PomodoroView(timer, self)
        file = None

        if PILLOW_AVAILABLE:
            try:
                guild           = self.bot.get_guild(timer.guild_id)
                participant_ids = [uid for uid, _ in timer.sorted_participants()]
                avatar_images   = await self._fetch_avatars(participant_ids[:6])
                img_buf         = await asyncio.get_event_loop().run_in_executor(
                    None, build_status_image, timer, avatar_images, guild
                )
                file = discord.File(img_buf, filename="pomodoro_card.png")
                embed.set_image(url="attachment://pomodoro_card.png")
            except Exception as e:
                logger.warning(f"Image card generation failed: {e}")

        try:
            if file:
                # Must re-send to replace the attachment
                try:
                    if timer.status_message:
                        await timer.status_message.delete()
                except Exception:
                    pass
                timer.status_message = await timer.text_channel.send(
                    embed=embed, view=view, file=file
                )
            else:
                if timer.status_message:
                    await timer.status_message.edit(embed=embed, view=view)
                else:
                    timer.status_message = await timer.text_channel.send(embed=embed, view=view)
        except Exception as e:
            logger.debug(f"update_status_card send error: {e}")

    # ── Channel name ──────────────────────────────────────────────────────
    async def update_channel_title(self, timer: PomodoroTimer):
        try:
            if timer.state == "focus":
                await timer.voice_channel.edit(name=f"🎯 Focus | {timer.time_left // 60}m")
            elif timer.state == "break":
                await timer.voice_channel.edit(name=f"☕ Break | {timer.time_left // 60}m")
            else:
                await timer.voice_channel.edit(name=timer.original_channel_name)
        except Exception:
            pass

    # ── AFK kick ──────────────────────────────────────────────────────────
    async def check_afk_and_kick(self, timer: PomodoroTimer):
        for member in timer.voice_channel.members:
            if member.bot:
                continue
            uid = member.id
            if uid not in timer.present_members:
                timer.missed_cycles[uid] = timer.missed_cycles.get(uid, 0) + 1
                if timer.missed_cycles[uid] >= timer.inactivity_threshold:
                    try:
                        await member.move_to(None, reason="AFK during Pomodoro.")
                        embed = discord.Embed(
                            title="🛏️ Disconnected — Inactivity",
                            description=(
                                "You were removed from the Pomodoro voice channel for missing "
                                "3 consecutive **Present** checks. Rejoin and stay active!"
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
            embed.title       = "☕ Break Time!"
            embed.description = "Step away from your screen, hydrate, and stretch!"
        try:
            await voice_channel.send(embed=embed, tts=True)
        except Exception:
            pass

    # ── Slash commands ────────────────────────────────────────────────────
    @app_commands.command(name="pomodoro", description="Create a Pomodoro timer in your current voice channel.")
    @app_commands.describe(
        focus_length="Focus period in minutes (default 25)",
        break_length="Break period in minutes (default 5)",
        name="Name shown on the timer card"
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
                "❌ You must join a voice channel first!", ephemeral=True
            )
            return

        vc = voice_state.channel
        if vc.id in self.active_timers:
            await interaction.response.send_message(
                "❌ A Pomodoro timer is already running in this voice channel!", ephemeral=True
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
                f"Timer **{name}** (`{focus_length}m` focus / `{break_length}m` break) "
                f"attached to **{vc.name}**.\n\nPress **▶ Start** on the card below to begin!"
            ),
            color=discord.Color.blurple()
        )
        await interaction.response.send_message(embed=embed)
        await self.update_status_card(timer)

    @app_commands.command(name="timers", description="List all active Pomodoro timers in this server.")
    async def timers(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server!", ephemeral=True)
            return

        embed = discord.Embed(title="⏱️ Active Pomodoro Timers", color=discord.Color.blue())
        lines = [
            f"• **{t.name}** — <#{vc_id}> — `{'RUNNING' if t.state != 'idle' else 'IDLE'}` "
            f"({t.focus_length}/{t.break_length}m)"
            for vc_id, t in self.active_timers.items()
            if t.guild_id == interaction.guild_id
        ]
        embed.description = "\n".join(lines) or "No active Pomodoro timers in this server."
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(PomodoroCog(bot))
