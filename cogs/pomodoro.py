import discord
from discord import app_commands
from discord.ext import commands
import logging
import asyncio
import io
import random
import datetime
from datetime import timedelta
import aiohttp
import os
import tempfile
from gtts import gTTS
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageFilter
import database

logger = logging.getLogger("pomodoro")

def get_ffmpeg_path() -> str:
    """Helper to locate the FFmpeg executable dynamically, prioritizing WinGet installation directories."""
    localappdata = os.getenv("LOCALAPPDATA", "")
    candidates = [
        "ffmpeg",
        os.path.join(localappdata, "Microsoft", "WinGet", "Packages", "BtbN.FFmpeg.GPL_Microsoft.Winget.Source_8wekyb3d8bbwe", "ffmpeg-N-124767-ge8031e5b9a-win64-gpl", "bin", "ffmpeg.exe"),
        os.path.join(localappdata, "Microsoft", "WinGet", "Packages", "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe", "ffmpeg-8.1.1-full_build", "bin", "ffmpeg.exe"),
        os.path.join(localappdata, "Microsoft", "WinGet", "Links", "ffmpeg.exe")
    ]
    for candidate in candidates:
        if candidate == "ffmpeg":
            import shutil
            if shutil.which("ffmpeg"):
                return "ffmpeg"
        elif os.path.exists(candidate):
            return candidate
    return "ffmpeg"

# ─── Color Palette ────────────────────────────────────────────────────────────
_BG_TOP    = (  6,   8,  22)          # Deep midnight navy
_BG_BOT    = ( 12,  20,  52)          # Midnight blue
FOCUS_CLR  = (255, 107, 107)          # Soft coral / red
BREAK_CLR  = (107, 203, 119)          # Mint green
GOLD       = (229, 169,  60)          # Warm gold accent
TEXT_W     = (255, 255, 255, 255)     # Pure white
TEXT_M     = (155, 178, 210, 255)     # Muted steel blue
RING_TRACK = ( 22,  28,  55, 255)     # Dark ring base track

# ─── Cross-Platform Font Loader ───────────────────────────────────────────────
_BOLD_PATHS = [
    "C:/Windows/Fonts/segoeuib.ttf",          # Windows – Segoe UI Bold
    "C:/Windows/Fonts/calibrib.ttf",           # Windows – Calibri Bold
    "C:/Windows/Fonts/arialbd.ttf",            # Windows – Arial Bold
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",          # Linux
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",  # Linux alt
]
_REG_PATHS = [
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/calibri.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]

def _font(candidates: list, size: int) -> ImageFont.FreeTypeFont:
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()

def bold(size: int)    -> ImageFont.FreeTypeFont: return _font(_BOLD_PATHS, size)
def regular(size: int) -> ImageFont.FreeTypeFont: return _font(_REG_PATHS, size)


# ─── Background Helpers ───────────────────────────────────────────────────────

def make_gradient_bg(W: int, H: int) -> Image.Image:
    """Deep-space gradient canvas with soft glow orbs and micro star dots."""
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    draw   = ImageDraw.Draw(canvas)

    # Vertical gradient top → bottom
    for y in range(H):
        t = y / H
        r = int(_BG_TOP[0] + (_BG_BOT[0] - _BG_TOP[0]) * t)
        g = int(_BG_TOP[1] + (_BG_BOT[1] - _BG_TOP[1]) * t)
        b = int(_BG_TOP[2] + (_BG_BOT[2] - _BG_TOP[2]) * t)
        draw.line([(0, y), (W, y)], fill=(r, g, b, 255))

    # Soft purple glow orb — top-left
    g1 = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(g1).ellipse((-110, -110, 460, 400), fill=(90, 38, 195, 60))
    g1 = g1.filter(ImageFilter.GaussianBlur(radius=95))
    canvas = Image.alpha_composite(canvas, g1)

    # Soft teal glow orb — top-right
    g2 = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(g2).ellipse((W - 400, -90, W + 80, 380), fill=(18, 88, 170, 52))
    g2 = g2.filter(ImageFilter.GaussianBlur(radius=95))
    canvas = Image.alpha_composite(canvas, g2)

    # Warm amber micro-glow bottom-center (adds depth)
    g3 = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(g3).ellipse((W // 2 - 200, H - 200, W // 2 + 200, H + 100), fill=(180, 100, 30, 35))
    g3 = g3.filter(ImageFilter.GaussianBlur(radius=70))
    canvas = Image.alpha_composite(canvas, g3)

    # Micro star dots (fixed seed = stable pattern across refreshes)
    stars = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd    = ImageDraw.Draw(stars)
    rng   = random.Random(7331)
    for _ in range(110):
        x = rng.randint(0, W)
        y = rng.randint(0, H)
        r = rng.choice([1, 1, 1, 2])
        a = rng.randint(35, 130)
        sd.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, a))
    canvas = Image.alpha_composite(canvas, stars)

    return canvas


def draw_glass_panel(canvas: Image.Image, x1, y1, x2, y2, radius: int = 14) -> Image.Image:
    """Frosted-glass panel with a thin luminous border."""
    ov = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d  = ImageDraw.Draw(ov)
    d.rounded_rectangle([x1, y1, x2, y2], radius=radius, fill=(255, 255, 255, 8))
    d.rounded_rectangle([x1, y1, x2, y2], radius=radius, outline=(255, 255, 255, 32), width=1)
    return Image.alpha_composite(canvas, ov)


def draw_glowing_arc(canvas: Image.Image, box, start, end, color) -> Image.Image:
    """Draws an arc with a layered soft outer glow."""
    W, H = canvas.size
    for width, alpha in [(44, 14), (30, 28), (18, 255)]:
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(layer).arc(box, start=start, end=end,
                                   fill=(*color, alpha), width=width)
        canvas = Image.alpha_composite(canvas, layer)
    return canvas


# ─── Avatar Helpers ───────────────────────────────────────────────────────────

async def fetch_avatar(user: discord.Member) -> Image.Image:
    """Fetches member avatar as PIL image. Falls back to a gold circle."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(str(user.display_avatar.url)) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    return Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception as e:
        logger.warning(f"Avatar fetch failed for {user}: {e}")
    img = Image.new("RGBA", (100, 100), (14, 22, 48, 255))
    ImageDraw.Draw(img).ellipse([(5, 5), (95, 95)], fill=(*GOLD, 255))
    return img


def crop_circle(img: Image.Image, size: int) -> Image.Image:
    """Returns the image cropped into a perfect circle with transparency."""
    img  = img.resize((size, size), Image.Resampling.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    out  = ImageOps.fit(img, (size, size), centering=(0.5, 0.5))
    out.putalpha(mask)
    return out


async def is_moderator(interaction: discord.Interaction) -> bool:
    """Checks if the interaction user is an administrator or has a registered moderator role."""
    if not interaction.guild:
        return False
    if interaction.user.guild_permissions.administrator:
        return True
    mod_roles = database.get_mod_roles(interaction.guild_id)
    user_role_ids = [role.id for role in interaction.user.roles]
    return any(r_id in mod_roles for r_id in user_role_ids)


# ─── Session Class ────────────────────────────────────────────────────────────

class PomodoroSession:
    """Represents an active Pomodoro study session running inside a voice channel."""

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
        inactive_threshold: int,
        voice_alert: bool,
    ):
        self.cog                  = cog
        self.voice_channel        = voice_channel
        self.timer_channel        = timer_channel
        self.notification_channel = notification_channel

        self.focus_length      = focus_length * 60
        self.break_length      = break_length * 60
        self.name              = name
        self.video_required    = video_required
        self.inactive_threshold = inactive_threshold * 60
        self.voice_alert        = voice_alert

        self.current_phase  = "FOCUS"
        self.phase_start    = datetime.datetime.now()
        self.phase_end      = self.phase_start + timedelta(seconds=self.focus_length)

        self.message        = None   # Discord message showing the timer card
        self.active         = True

        # Tracking dictionaries
        self.join_times     = {}     # user_id → join datetime
        self.inactive_times = {}     # user_id → camera-off datetime
        self.warned_users   = set()  # user_ids that received a warning DM

        now = datetime.datetime.now()
        for member in voice_channel.members:
            if not member.bot:
                self.join_times[member.id] = now
                if self.video_required and not member.voice.self_video:
                    self.inactive_times[member.id] = now

        self.task = asyncio.create_task(self.update_loop())

    async def play_voice_alert(self, text: str):
        if not self.voice_alert:
            return

        guild = self.voice_channel.guild
        try:
            voice_client = discord.utils.get(self.cog.bot.voice_clients, guild=guild)
            if not voice_client:
                voice_client = await self.voice_channel.connect()
            elif voice_client.channel != self.voice_channel:
                await voice_client.move_to(self.voice_channel)

            if voice_client.is_playing():
                voice_client.stop()

            temp_dir = tempfile.gettempdir()
            filename = os.path.join(temp_dir, f"pomodoro_tts_{guild.id}.mp3")

            def save_tts():
                tts = gTTS(text=text, lang='en')
                tts.save(filename)

            await asyncio.to_thread(save_tts)

            ffmpeg_path = get_ffmpeg_path()
            audio_source = discord.FFmpegPCMAudio(filename, executable=ffmpeg_path)

            def after_playing(error):
                if error:
                    logger.error(f"Error playing voice alert: {error}")
                try:
                    if os.path.exists(filename):
                        os.remove(filename)
                except Exception as ex:
                    logger.warning(f"Failed to delete temp TTS file: {ex}")

            voice_client.play(audio_source, after=after_playing)
        except Exception as e:
            logger.error(f"Failed to play voice alert: {e}")

    # ── Update Loop ───────────────────────────────────────────────────────────

    async def update_loop(self):
        """Tick every 30 s: refresh the card and enforce camera rules."""
        await asyncio.sleep(5)

        while self.active:
            try:
                now       = datetime.datetime.now()
                remaining = int((self.phase_end - now).total_seconds())

                # Phase transition
                if remaining <= 0:
                    members_to_mention = [m.mention for m in self.voice_channel.members if not m.bot]
                    mentions_str = " ".join(members_to_mention)
                    mention_part = f"\n{mentions_str}" if members_to_mention else ""

                    if self.current_phase == "FOCUS":
                        self.current_phase = "BREAK"
                        self.phase_end     = now + timedelta(seconds=self.break_length)
                        try:
                            await self.notification_channel.send(
                                f"🔔 **{self.name}** — Focus done! "
                                f"Enjoy your **{self.break_length // 60}m** break. 🟢{mention_part}"
                            )
                        except Exception:
                            pass
                        if self.voice_alert:
                            await self.play_voice_alert("Times up, streach and rehydrate see in a few")
                    else:
                        self.current_phase = "FOCUS"
                        self.phase_end     = now + timedelta(seconds=self.focus_length)
                        try:
                            await self.notification_channel.send(
                                f"🔴 **{self.name}** — Break over! "
                                f"Back to focus for **{self.focus_length // 60}m**. 🚀{mention_part}"
                            )
                        except Exception:
                            pass
                        if self.voice_alert:
                            await self.play_voice_alert("Now focus lets concentrate to our work")
                    self.phase_start = now
                    remaining        = int((self.phase_end - now).total_seconds())

                # Clean up members who left VC
                current_ids = {m.id for m in self.voice_channel.members if not m.bot}
                for uid in list(self.join_times):
                    if uid not in current_ids:
                        self.join_times.pop(uid, None)
                        self.inactive_times.pop(uid, None)
                        self.warned_users.discard(uid)

                # Camera enforcement
                for member in self.voice_channel.members:
                    if member.bot:
                        continue
                    if member.id not in self.join_times:
                        self.join_times[member.id] = now
                    if self.video_required:
                        has_video = member.voice and member.voice.self_video
                        if not has_video:
                            if member.id not in self.inactive_times:
                                self.inactive_times[member.id] = now
                            else:
                                idle = int((now - self.inactive_times[member.id]).total_seconds())
                                if idle >= self.inactive_threshold:
                                    try:
                                        await member.move_to(None, reason="Pomodoro camera enforcement")
                                        self.inactive_times.pop(member.id, None)
                                        self.join_times.pop(member.id, None)
                                        self.warned_users.discard(member.id)
                                        embed = discord.Embed(
                                            title="❌ Disconnected from Voice",
                                            description=(
                                                f"You were removed from **{self.voice_channel.name}** "
                                                f"because your camera was off for more than "
                                                f"{self.inactive_threshold // 60} minutes."
                                            ),
                                            color=discord.Color.red(),
                                        )
                                        await member.send(embed=embed)
                                    except Exception as e:
                                        logger.error(f"Kick failed for {member}: {e}")
                                elif idle >= (self.inactive_threshold / 2):
                                    if member.id not in self.warned_users:
                                        try:
                                            mins_left = (self.inactive_threshold - idle) // 60
                                            embed = discord.Embed(
                                                title="⚠️ Camera Off Warning",
                                                description=(
                                                    f"Your camera is off in **{self.voice_channel.name}**. "
                                                    f"Turn it on within **{mins_left}m** to avoid being kicked."
                                                ),
                                                color=discord.Color.gold(),
                                            )
                                            await member.send(embed=embed)
                                            self.warned_users.add(member.id)
                                        except Exception:
                                            pass
                        else:
                            self.inactive_times.pop(member.id, None)
                            self.warned_users.discard(member.id)

                # Render and post the updated card
                file  = await self.generate_card_file(remaining)
                embed = discord.Embed(
                    title=f"⏳ Active Study Timer: {self.name}",
                    description=(
                        f"Active in: {self.voice_channel.mention} | "
                        f"Focus: {self.focus_length // 60}m | Break: {self.break_length // 60}m"
                    ),
                    color=discord.Color.gold() if self.current_phase == "FOCUS" else discord.Color.green(),
                )
                embed.set_image(url="attachment://timer.png")

                if self.message:
                    try:
                        await self.message.edit(embed=embed, attachments=[file])
                    except Exception as e:
                        logger.error(f"Failed to refresh timer card: {e}")

            except Exception as e:
                logger.error(f"Error in Pomodoro update loop: {e}")

            await asyncio.sleep(30)

    # ── Card Renderer ─────────────────────────────────────────────────────────

    async def generate_card_file(self, remaining_seconds: int) -> discord.File:
        """Renders the aesthetic deep-space Pomodoro study card as a PNG attachment."""
        W, H = 1120, 560

        # ── Background ────────────────────────────────────────────────────────
        canvas = make_gradient_bg(W, H)

        # ── Left Frosted-Glass Panel (study group) ────────────────────────────
        PX1, PY1, PX2, PY2 = 24, 24, 480, H - 24
        canvas = draw_glass_panel(canvas, PX1, PY1, PX2, PY2)

        draw = ImageDraw.Draw(canvas)

        # ── Fonts ─────────────────────────────────────────────────────────────
        f_sess   = bold(26)
        f_glabel = bold(11)
        f_user   = bold(19)
        f_sub    = regular(13)
        f_cam    = regular(12)
        f_timer  = bold(70)
        f_remain = regular(14)
        f_badge  = bold(12)
        f_stats  = regular(15)

        # ── Session Title ─────────────────────────────────────────────────────
        draw.text((PX1 + 22, PY1 + 20), self.name.upper(), font=f_sess, fill=TEXT_W)

        # Thin gold separator under title
        draw.line(
            [(PX1 + 22, PY1 + 66), (PX2 - 22, PY1 + 66)],
            fill=(*GOLD, 140), width=1
        )

        # "● STUDY GROUP" section label
        draw.text((PX1 + 22, PY1 + 80), "● STUDY GROUP", font=f_glabel, fill=(*GOLD, 215))

        # ── Members ───────────────────────────────────────────────────────────
        now     = datetime.datetime.now()
        members = [m for m in self.voice_channel.members if not m.bot]
        members.sort(key=lambda m: self.join_times.get(m.id, now))

        y_cur = PY1 + 114
        for member in members[:5]:
            # Avatar
            pfp        = await fetch_avatar(member)
            pfp_circle = crop_circle(pfp, 50)
            canvas.alpha_composite(pfp_circle, (PX1 + 22, y_cur))

            # Gold ring around avatar
            draw = ImageDraw.Draw(canvas)
            draw.ellipse(
                [PX1 + 21, y_cur - 1, PX1 + 72, y_cur + 51],
                outline=(*GOLD, 110), width=2
            )

            # Username
            draw.text((PX1 + 86, y_cur + 5), member.display_name, font=f_user, fill=TEXT_W)

            # Duration since joined
            join_dt  = self.join_times.get(member.id, now)
            mins_in  = int((now - join_dt).total_seconds() // 60)
            dur_str  = f"Studying for {mins_in}m" if mins_in > 0 else "Just joined"
            draw.text((PX1 + 86, y_cur + 29), dur_str, font=f_sub, fill=TEXT_M)

            # Camera status badge
            if self.video_required:
                cam_on  = member.voice and member.voice.self_video
                cam_clr = BREAK_CLR if cam_on else FOCUS_CLR
                cam_lbl = "CAM ON" if cam_on else "CAM OFF"
                cb      = draw.textbbox((0, 0), cam_lbl, font=f_cam)
                c_w     = cb[2] - cb[0] + 16
                c_x     = PX2 - c_w - 16
                c_y     = y_cur + 27
                draw.rounded_rectangle(
                    [c_x, c_y, c_x + c_w, c_y + 18],
                    radius=4, fill=(*cam_clr, 30)
                )
                draw.rounded_rectangle(
                    [c_x, c_y, c_x + c_w, c_y + 18],
                    radius=4, outline=(*cam_clr, 110), width=1
                )
                draw.text((c_x + 8, c_y + 2), cam_lbl, font=f_cam, fill=(*cam_clr, 255))

            y_cur += 80

        if not members:
            draw.text((PX1 + 22, y_cur + 6), "No members in voice channel", font=f_sub, fill=TEXT_M)
        elif len(members) > 5:
            draw.text(
                (PX1 + 22, y_cur + 6),
                f"+ {len(members) - 5} more participants",
                font=f_sub, fill=(*GOLD, 170)
            )

        # ── Right Side: Phase Badge ───────────────────────────────────────────
        RING_CX, RING_CY, RING_R = 822, 286, 150
        arc_clr = FOCUS_CLR if self.current_phase == "FOCUS" else BREAK_CLR

        badge_txt = "● FOCUS PHASE" if self.current_phase == "FOCUS" else "● BREAK TIME"
        b_bb  = draw.textbbox((0, 0), badge_txt, font=f_badge)
        b_w   = b_bb[2] - b_bb[0] + 32
        b_h   = 28
        b_x   = RING_CX - b_w // 2
        b_y   = 26
        draw.rounded_rectangle([b_x, b_y, b_x + b_w, b_y + b_h], radius=14, fill=(*arc_clr, 25))
        draw.rounded_rectangle([b_x, b_y, b_x + b_w, b_y + b_h], radius=14, outline=(*arc_clr, 105), width=1)
        draw.text((b_x + 16, b_y + 6), badge_txt, font=f_badge, fill=(*arc_clr, 255))

        # ── Ring Track ────────────────────────────────────────────────────────
        ring_box = [
            (RING_CX - RING_R, RING_CY - RING_R),
            (RING_CX + RING_R, RING_CY + RING_R),
        ]
        draw.arc(ring_box, start=0, end=360, fill=RING_TRACK, width=16)

        # Subtle inner shadow ring
        inner_box = [
            (RING_CX - RING_R + 8, RING_CY - RING_R + 8),
            (RING_CX + RING_R - 8, RING_CY + RING_R - 8),
        ]
        draw.arc(inner_box, start=0, end=360, fill=(255, 255, 255, 5), width=1)

        # ── Progress Arc with Glow ────────────────────────────────────────────
        total     = self.focus_length if self.current_phase == "FOCUS" else self.break_length
        ratio     = max(0.02, min(1.0, remaining_seconds / total))
        arc_start = -90
        arc_end   = arc_start + int(360 * ratio)
        canvas    = draw_glowing_arc(canvas, ring_box, arc_start, arc_end, arc_clr)
        draw      = ImageDraw.Draw(canvas)   # refresh draw after alpha compositing

        # ── Timer Digits (anti-overlap math) ─────────────────────────────────
        rem_min  = max(0, remaining_seconds // 60)
        rem_sec  = max(0, remaining_seconds % 60)
        time_str = f"{rem_min:02}:{rem_sec:02}"

        # Measure the true visual bounding box of the timer text
        t_bb    = draw.textbbox((0, 0), time_str, font=f_timer)
        t_w     = t_bb[2] - t_bb[0]   # visual width
        t_h     = t_bb[3] - t_bb[1]   # visual height

        # Desired visual center of the time digits: 16px ABOVE ring center
        v_cx = RING_CX
        v_cy = RING_CY - 18

        # draw.text anchor point: shift by bbox offsets so the VISUAL center hits (v_cx, v_cy)
        t_x = v_cx - t_bb[0] - t_w // 2
        t_y = v_cy - t_bb[1] - t_h // 2
        draw.text((t_x, t_y), time_str, font=f_timer, fill=TEXT_W)

        # Visual bottom of the drawn text
        timer_visual_bottom = t_y + t_bb[1] + t_h

        # ── "REMAINING" Label — always below, never overlapping ───────────────
        r_bb  = draw.textbbox((0, 0), "REMAINING", font=f_remain)
        r_w   = r_bb[2] - r_bb[0]
        r_x   = RING_CX - r_bb[0] - r_w // 2
        r_y   = timer_visual_bottom + 10   # 10px clear gap below time digits
        draw.text((r_x, r_y), "REMAINING", font=f_remain, fill=TEXT_M)

        # ── Stats Strip (bottom-right) ────────────────────────────────────────
        strip_y = H - 52
        draw.line([(512, strip_y - 10), (W - 28, strip_y - 10)], fill=(255, 255, 255, 16), width=1)

        stats = [
            f"⏱  Focus: {self.focus_length  // 60}m",
            f"☕  Break: {self.break_length  // 60}m",
            f"👥  {len(members)} member{'s' if len(members) != 1 else ''}",
        ]
        sx = 516
        for stat in stats:
            s_bb = draw.textbbox((0, 0), stat, font=f_stats)
            draw.text((sx, strip_y), stat, font=f_stats, fill=TEXT_M)
            sx += (s_bb[2] - s_bb[0]) + 44

        # ── Save ──────────────────────────────────────────────────────────────
        fp = io.BytesIO()
        canvas.save(fp, format="PNG")
        fp.seek(0)
        return discord.File(fp, filename="timer.png")

    async def stop(self):
        """Cancels the update loop and marks the session as inactive."""
        self.active = False
        self.task.cancel()
        guild = self.voice_channel.guild
        voice_client = discord.utils.get(self.cog.bot.voice_clients, guild=guild)
        if voice_client and voice_client.channel == self.voice_channel:
            try:
                await voice_client.disconnect()
            except Exception as e:
                logger.warning(f"Failed to disconnect from voice channel: {e}")


# ─── Pomodoro Cog ─────────────────────────────────────────────────────────────

class Pomodoro(commands.Cog):
    """Cog running pomodoro timers and camera requirements per voice channel."""

    destroy_group = app_commands.Group(name="destroy", description="Destroy active sessions.")

    def __init__(self, bot: commands.Bot):
        self.bot      = bot
        self.sessions = {}   # voice_channel_id → PomodoroSession

    @app_commands.command(name="pomodoro", description="Start a Pomodoro study session in your voice channel.")
    @app_commands.describe(
        focus_length="Focus duration in minutes (e.g. 25).",
        break_length="Break duration in minutes (e.g. 5).",
        name="Name of this study session.",
        timer_channel="The text channel where the timer status card will be refreshed.",
        notification_channel="The channel where phase start alerts will be posted.",
        video_required="Enforce camera sharing (True/False).",
        inactive_threshold="Allowed camera-off time in minutes before kick (e.g. 2).",
        voice_alert="Whether to play custom TTS voice alerts in the voice channel (True/False)."
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
        inactive_threshold: int,
        voice_alert: bool = False
    ):
        if not interaction.guild:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return

        if not await is_moderator(interaction):
            await interaction.response.send_message(
                "❌ You do not have permission to run this command. (Moderator role or Administrator permission required)",
                ephemeral=True
            )
            return

        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(
                "❌ You must be connected to a voice channel to start a Pomodoro session!", ephemeral=True
            )
            return

        voice_channel = interaction.user.voice.channel

        if not isinstance(timer_channel, (discord.TextChannel, discord.VoiceChannel)) or \
           not isinstance(notification_channel, (discord.TextChannel, discord.VoiceChannel)):
            await interaction.response.send_message(
                "❌ The selected timer and notification channels must support message posting.", ephemeral=True
            )
            return

        if voice_channel.id in self.sessions:
            await interaction.response.send_message(
                f"❌ A Pomodoro session is already active in **{voice_channel.name}**.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=False)

        session = PomodoroSession(
            cog=self,
            voice_channel=voice_channel,
            timer_channel=timer_channel,
            notification_channel=notification_channel,
            focus_length=focus_length,
            break_length=break_length,
            name=name,
            video_required=video_required,
            inactive_threshold=inactive_threshold,
            voice_alert=voice_alert,
        )
        self.sessions[voice_channel.id] = session

        file  = await session.generate_card_file(focus_length * 60)
        embed = discord.Embed(
            title=f"⏳ Active Study Timer: {session.name}",
            description=f"Active in: {voice_channel.mention} | Focus: {focus_length}m | Break: {break_length}m",
            color=discord.Color.gold(),
        )
        embed.set_image(url="attachment://timer.png")

        try:
            msg = await timer_channel.send(embed=embed, file=file)
            session.message = msg

            members_to_mention = [m.mention for m in voice_channel.members if not m.bot]
            mentions_str = " ".join(members_to_mention)
            mention_part = f"\n{mentions_str}" if members_to_mention else ""

            await notification_channel.send(
                f"🔴 **Focus session started for {voice_channel.mention}!** "
                f"Back to focus for **{focus_length} minutes**. 🚀{mention_part}"
            )
            await interaction.followup.send(
                f"✅ **Pomodoro session successfully started!**\n"
                f"• **Voice Room:** {voice_channel.mention}\n"
                f"• **Timer updates in:** {timer_channel.mention}\n"
                f"• **Camera Check:** {'Enabled' if video_required else 'Disabled'} "
                f"(Threshold: {inactive_threshold}m)"
            )
            if voice_alert:
                await session.play_voice_alert("Now focus lets concentrate to our work")
        except Exception as e:
            await session.stop()
            self.sessions.pop(voice_channel.id, None)
            await interaction.followup.send(f"❌ Failed to start session: {e}")

    @app_commands.command(name="timer", description="Display details of the Pomodoro session in your voice channel.")
    async def timer(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return

        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(
                "❌ You must be connected to a voice channel to check the timer.", ephemeral=True
            )
            return

        vc_id   = interaction.user.voice.channel.id
        session = self.sessions.get(vc_id)

        if not session:
            await interaction.response.send_message(
                "❌ No active Pomodoro session was found in your voice channel.", ephemeral=True
            )
            return

        now     = datetime.datetime.now()
        remaining = int((session.phase_end - now).total_seconds())
        rem_min = max(0, remaining // 60)
        rem_sec = max(0, remaining % 60)

        embed = discord.Embed(
            title=f"⏱️ Study Status — {session.name}",
            description=f"**VC Room:** {interaction.user.voice.channel.mention}",
            color=discord.Color.gold() if session.current_phase == "FOCUS" else discord.Color.green(),
        )
        embed.add_field(name="Current Phase",  value=f"🔴 **{session.current_phase}**",     inline=True)
        embed.add_field(name="Remaining Time", value=f"⏳ **{rem_min:02}:{rem_sec:02}**",   inline=True)
        embed.add_field(name="Camera Check",   value="✅ Yes" if session.video_required else "❌ No", inline=True)

        if session.message:
            embed.add_field(
                name="Timer Dashboard",
                value=f"[Jump to Dashboard]({session.message.jump_url})",
                inline=False,
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @destroy_group.command(name="pomodoro", description="Destroy the active Pomodoro session in your voice channel.")
    async def destroy_pomodoro(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return

        if not await is_moderator(interaction):
            await interaction.response.send_message(
                "❌ You do not have permission to run this command. (Moderator role or Administrator permission required)",
                ephemeral=True
            )
            return

        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(
                "❌ You must join a voice channel first to destroy its Pomodoro session!", ephemeral=True
            )
            return

        voice_channel = interaction.user.voice.channel
        session = self.sessions.get(voice_channel.id)

        if not session:
            await interaction.response.send_message(
                f"❌ No active Pomodoro session was found in your voice channel **{voice_channel.name}**.",
                ephemeral=True
            )
            return

        await session.stop()
        self.sessions.pop(voice_channel.id, None)

        try:
            await session.notification_channel.send(
                f"ℹ️ Pomodoro session in **{voice_channel.name}** was destroyed by {interaction.user.mention}."
            )
        except Exception:
            pass

        await interaction.response.send_message(
            f"✅ **Pomodoro session '{session.name}' in {voice_channel.mention} has been destroyed.**",
            ephemeral=False
        )

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        """Keeps session tracking in sync when members join, leave, or toggle camera."""
        if member.bot:
            return

        now = datetime.datetime.now()

        if before.channel != after.channel:
            # Left a channel
            if before.channel and before.channel.id in self.sessions:
                session = self.sessions[before.channel.id]
                session.join_times.pop(member.id, None)
                session.inactive_times.pop(member.id, None)
                session.warned_users.discard(member.id)

                non_bots = [m for m in before.channel.members if not m.bot]
                if not non_bots:
                    await session.stop()
                    self.sessions.pop(before.channel.id, None)
                    try:
                        await session.notification_channel.send(
                            f"ℹ️ Pomodoro session in **{before.channel.name}** ended — channel is now empty."
                        )
                    except Exception:
                        pass

            # Joined a channel
            if after.channel and after.channel.id in self.sessions:
                session = self.sessions[after.channel.id]
                session.join_times[member.id] = now
                if session.video_required and not after.self_video:
                    session.inactive_times[member.id] = now

        # Camera toggled in the same channel
        elif (
            before.channel == after.channel
            and after.channel
            and after.channel.id in self.sessions
        ):
            session = self.sessions[after.channel.id]
            if session.video_required and before.self_video != after.self_video:
                if not after.self_video:
                    session.inactive_times[member.id] = now
                else:
                    session.inactive_times.pop(member.id, None)
                    session.warned_users.discard(member.id)


async def setup(bot: commands.Bot):
    await bot.add_cog(Pomodoro(bot))
