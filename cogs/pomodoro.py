import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import time
import io
import math
import logging

logger = logging.getLogger("StudyBot")

# ── Pillow soft-import ──────────────────────────────────────────────────────
try:
    from PIL import Image, ImageDraw, ImageFont
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False
    logger.warning("Pillow not installed – visual cards disabled.")

# ── Colour palette ──────────────────────────────────────────────────────────
BG_MAIN       = (8,   18,  38)
BG_HEADER     = (11,  24,  50)
BG_CIRCLE     = (14,  22,  48)
BG_PILL       = (18,  28,  60)
GOLD          = (212, 175,  55)
GOLD_DIM      = (80,  65,  20)
WHITE         = (245, 248, 255)
GREY          = (120, 130, 160)
RED_FOCUS     = (220,  70,  70)
GREEN_BREAK   = ( 72, 200, 120)
SEPARATOR     = ( 22,  34,  68)

# ── Card dimensions ──────────────────────────────────────────────────────────
CARD_W    = 1120
CARD_H    = 620
CORNER    = 44
HEADER_H  = 104
RING_R    = 190
RING_CX   = 830
RING_CY   = 370
RING_W    = 18
AVATAR_SZ = 62
MAX_MEMBERS = 6


# ════════════════════════════════════════════════════════════════════════════
#  PomodoroTimer  (data model)
# ════════════════════════════════════════════════════════════════════════════
class PomodoroTimer:
    def __init__(
        self,
        guild_id,
        voice_channel,
        notification_channel,    # text channel where the card lives
        focus_length: int,
        break_length: int,
        name: str | None = None,
        video_required: bool = False,
        inactive_threshold: int = 5,   # minutes before warn/kick action
    ):
        self.guild_id             = guild_id
        self.voice_channel        = voice_channel
        self.notification_channel = notification_channel
        self.focus_length         = focus_length
        self.break_length         = break_length
        self.name                 = name or voice_channel.name
        self.video_required       = video_required
        self.inactive_threshold   = inactive_threshold  # minutes

        self.state          = "idle"   # idle | focus | break
        self.time_left      = 0        # seconds
        self.current_cycle  = 1
        self.task           = None
        self.status_message = None
        self.voice_alerts   = True
        self.original_name  = voice_channel.name

        # Present check (cycle-based)
        self.present_members = set()
        self.last_present: dict[int, float] = {}   # uid → monotonic time of last present

        # Study-duration tracking
        self.session_seconds: dict[int, float] = {}
        self._join_time: dict[int, float] = {}

        # Camera / video tracking  (only used when video_required=True)
        self.cam_off_since: dict[int, float] = {}   # uid → monotonic when cam turned off
        self.cam_warned: set[int] = set()            # uids already DM-warned

    # ── Duration tracking ────────────────────────────────────────────────
    def snapshot(self):
        now = time.monotonic()
        for m in self.voice_channel.members:
            if not m.bot:
                self._join_time[m.id] = now

    def record_join(self, uid: int):
        self._join_time[uid] = time.monotonic()

    def record_leave(self, uid: int):
        t = self._join_time.pop(uid, None)
        if t:
            self.session_seconds[uid] = self.session_seconds.get(uid, 0) + (time.monotonic() - t)

    def flush(self):
        now = time.monotonic()
        for uid, t in list(self._join_time.items()):
            self.session_seconds[uid] = self.session_seconds.get(uid, 0) + (now - t)
            self._join_time[uid] = now

    def sorted_participants(self):
        self.flush()
        return sorted(self.session_seconds.items(), key=lambda x: x[1], reverse=True)


# ════════════════════════════════════════════════════════════════════════════
#  Card image generation
# ════════════════════════════════════════════════════════════════════════════
def _fmt(seconds: float) -> str:
    h = int(seconds) // 3600
    m = (int(seconds) % 3600) // 60
    s = int(seconds) % 60
    return f"{h}h {m:02d}m" if h else f"{m:02d}:{s:02d}"


def _circle_crop(img, size):
    img = img.convert("RGBA").resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, mask=mask)
    return out


def _font(size, bold=False):
    paths_bold = [
        "c:/Windows/Fonts/segoeuib.ttf",
        "c:/Windows/Fonts/arialbd.ttf",
        "c:/Windows/Fonts/calibrib.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    paths_reg = [
        "c:/Windows/Fonts/segoeui.ttf",
        "c:/Windows/Fonts/arial.ttf",
        "c:/Windows/Fonts/calibri.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in (paths_bold if bold else paths_reg):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _cx(draw, text, font, cx, y, fill):
    """Draw text horizontally centred on cx."""
    bb = draw.textbbox((0, 0), text, font=font)
    draw.text((cx - (bb[2] - bb[0]) // 2, y), text, font=font, fill=fill)


def _round_mask(w, h, r):
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=255)
    return m


def build_card(timer: PomodoroTimer, avatars: dict, guild) -> io.BytesIO:
    """Render the 1120×620 premium Pomodoro card."""

    # ── Base canvas ─────────────────────────────────────────────────────
    card = Image.new("RGBA", (CARD_W, CARD_H), (*BG_MAIN, 255))
    d    = ImageDraw.Draw(card)

    # ── Fonts  (BIG & BOLD everywhere, NO emoji in any string) ───────────
    f_vc_name   = _font(58, bold=True)   # VC name in header
    f_subtitle  = _font(20)              # session subtitle / state badge
    f_cycle     = _font(19)              # Cycle #N
    f_timer     = _font(88, bold=True)   # big clock digits — sized to fit inside ring
    f_phase     = _font(36, bold=True)   # FOCUS / BREAK / IDLE inside ring
    f_section   = _font(18, bold=True)   # PARTICIPANTS label
    f_pill      = _font(25, bold=True)   # time inside pill
    f_name_pill = _font(21, bold=True)   # member name
    f_cam       = _font(17)              # camera badge text
    f_footer    = _font(18)              # footer hint

    # ─────────────────────────────────────────────────────────────────────
    # HEADER STRIP
    # ─────────────────────────────────────────────────────────────────────
    d.rectangle([0, 0, CARD_W, HEADER_H], fill=(*BG_HEADER, 255))
    d.rectangle([0, HEADER_H - 3, CARD_W, HEADER_H], fill=(*GOLD, 255))

    # VC name — large bold gold on the left
    vc_name = timer.voice_channel.name
    bb = d.textbbox((0, 0), vc_name, font=f_vc_name)
    name_h = bb[3] - bb[1]
    d.text((36, (HEADER_H - name_h) // 2 - 4), vc_name, font=f_vc_name, fill=GOLD)

    # Session sub-label right of VC name (if different)
    if timer.name != vc_name:
        sub = "-- " + timer.name
        vc_w = bb[2] - bb[0]
        d.text((44 + vc_w, (HEADER_H - name_h) // 2 + 8), sub, font=f_subtitle, fill=GREY)

    # Right side of header: state badge + cycle + optional camera pill
    state_labels = {"focus": "FOCUS", "break": "BREAK", "idle":  "IDLE"}
    badge_cols   = {"focus": RED_FOCUS, "break": GREEN_BREAK, "idle": GREY}
    state_txt    = state_labels[timer.state]
    badge_col    = badge_cols[timer.state]

    bb_s = d.textbbox((0, 0), state_txt, font=f_subtitle)
    s_w  = bb_s[2] - bb_s[0]
    dot_margin = 12
    total_badge_w = 14 + dot_margin + s_w
    badge_x = CARD_W - total_badge_w - 32
    d.ellipse([badge_x, 18, badge_x + 14, 32], fill=badge_col)
    d.text((badge_x + 14 + dot_margin, 12), state_txt, font=f_subtitle, fill=badge_col)

    cycle_txt = "Cycle #" + str(timer.current_cycle)
    bb_c = d.textbbox((0, 0), cycle_txt, font=f_cycle)
    d.text((CARD_W - (bb_c[2] - bb_c[0]) - 32, 42), cycle_txt, font=f_cycle, fill=GREY)

    # Camera-required pill in header (plain text, no emoji)
    if timer.video_required:
        cam_label = "CAM ON"
        bb_cam = d.textbbox((0, 0), cam_label, font=f_cam)
        cw = (bb_cam[2] - bb_cam[0]) + 20
        ch = 22
        cx0 = CARD_W - cw - 32
        cy0 = 66
        d.rounded_rectangle([cx0, cy0, cx0 + cw, cy0 + ch],
                             radius=ch // 2, fill=(*RED_FOCUS, 180))
        d.text((cx0 + 10, cy0 + 2), cam_label, font=f_cam, fill=WHITE)

    # ─────────────────────────────────────────────────────────────────────
    # GLOW + TIMER CIRCLE (right panel)
    # ─────────────────────────────────────────────────────────────────────
    glow = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    gd   = ImageDraw.Draw(glow)
    for delta, alpha in [(250, 6), (215, 14), (192, 24)]:
        gd.ellipse([RING_CX - delta, RING_CY - delta,
                    RING_CX + delta, RING_CY + delta],
                   fill=(*BG_CIRCLE, alpha))
    card = Image.alpha_composite(card, glow)
    d    = ImageDraw.Draw(card)

    # Dark filled circle
    d.ellipse([RING_CX - RING_R - 6, RING_CY - RING_R - 6,
               RING_CX + RING_R + 6, RING_CY + RING_R + 6],
              fill=(*BG_CIRCLE, 255))

    # Progress ring
    TRACK_COL = (35, 42, 72)
    ring_col  = RED_FOCUS if timer.state == "focus" else GREEN_BREAK if timer.state == "break" else GOLD
    bbox_r    = [RING_CX - RING_R, RING_CY - RING_R, RING_CX + RING_R, RING_CY + RING_R]
    d.arc(bbox_r, 0, 360, fill=TRACK_COL, width=RING_W)

    total    = (timer.focus_length if timer.state == "focus" else timer.break_length) * 60
    total    = total if timer.state != "idle" else 1
    elapsed  = max(0, total - timer.time_left) if timer.state != "idle" else 0
    fraction = min(1.0, elapsed / total) if total else 0

    if fraction > 0:
        d.arc(bbox_r, -90, -90 + 360 * fraction, fill=ring_col, width=RING_W)

    # Gold dot follows the arc tip
    dot_a = math.radians(-90 + 360 * fraction)
    dot_x = int(RING_CX + RING_R * math.cos(dot_a))
    dot_y = int(RING_CY + RING_R * math.sin(dot_a))
    d.ellipse([dot_x - 11, dot_y - 11, dot_x + 11, dot_y + 11], fill=GOLD)

    # ── Timer digits + phase label, jointly centred inside the circle ─────
    mins, secs = timer.time_left // 60, timer.time_left % 60
    time_str   = f"{mins:02d}:{secs:02d}"

    bb_t  = d.textbbox((0, 0), time_str, font=f_timer)
    t_w, t_h = bb_t[2] - bb_t[0], bb_t[3] - bb_t[1]

    phase_str = state_labels[timer.state]
    bb_ph = d.textbbox((0, 0), phase_str, font=f_phase)
    p_w, p_h = bb_ph[2] - bb_ph[0], bb_ph[3] - bb_ph[1]

    GAP       = 14
    block_h   = t_h + GAP + p_h
    block_top = RING_CY - block_h // 2

    d.text((RING_CX - t_w // 2, block_top), time_str, font=f_timer, fill=GOLD)
    d.text((RING_CX - p_w // 2, block_top + t_h + GAP), phase_str, font=f_phase, fill=badge_col)

    # ─────────────────────────────────────────────────────────────────────
    # VERTICAL DIVIDER
    # ─────────────────────────────────────────────────────────────────────
    DIV_X = 540
    d.rectangle([DIV_X, HEADER_H + 20, DIV_X + 2, CARD_H - 44], fill=(*SEPARATOR, 255))

    # ─────────────────────────────────────────────────────────────────────
    # LEFT PANEL — PARTICIPANTS
    # ─────────────────────────────────────────────────────────────────────
    PX = 36
    PY = HEADER_H + 24

    d.text((PX, PY), "PARTICIPANTS", font=f_section, fill=GOLD)
    PY += 34

    participants = timer.sorted_participants()[:MAX_MEMBERS]

    if not participants:
        d.text((PX, PY + 12), "No one studying yet...", font=f_name_pill, fill=GREY)
    else:
        available_h = CARD_H - PY - 50
        ROW_H = min(available_h // max(len(participants), 1), 80)

        for i, (uid, secs_val) in enumerate(participants):
            row_y = PY + i * ROW_H

            av = avatars.get(uid)
            if av:
                circ = _circle_crop(av, AVATAR_SZ)
                card.paste(circ, (PX, row_y), circ)
            else:
            # Duration pill  (avatar right edge + gap)
            dur   = _fmt(secs_val)
            bb_p  = d.textbbox((0, 0), dur, font=f_pill)
            pill_w = (bb_p[2] - bb_p[0]) + 32
            pill_h = AVATAR_SZ
            pill_x = PX + AVATAR_SZ + 14
            pill_y = row_y

            d.rounded_rectangle(
                [pill_x, pill_y, pill_x + pill_w, pill_y + pill_h],
                radius=pill_h // 2,
                fill=(*BG_PILL, 255),
                outline=(*GOLD, 80),
                width=2
            )
            d.text(
                (pill_x + 16, pill_y + (pill_h - (bb_p[3] - bb_p[1])) // 2),
                dur, font=f_pill, fill=GOLD
            )

            # Member name to the right of the pill
            m_obj = guild.get_member(uid) if guild else None
            mname = m_obj.display_name if m_obj else f"User {uid}"
            if len(mname) > 16:
                mname = mname[:14] + "…"
            d.text(
                (pill_x + pill_w + 14, row_y + (AVATAR_SZ - 26) // 2),
                mname, font=f_name_pill, fill=WHITE
            )

    # ── Footer ───────────────────────────────────────────────────────────
    FOOT_Y = CARD_H - 36
    foot = "✅ Press Present to confirm you're active  ·  Auto-refreshes every 30 s"
    _cx(d, foot, f_footer, CARD_W // 2, FOOT_Y, GREY)

    # ── Rounded corners mask ─────────────────────────────────────────────
    final = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    final.paste(card, mask=_round_mask(CARD_W, CARD_H, CORNER))

    buf = io.BytesIO()
    final.convert("RGB").save(buf, "PNG", optimize=True)
    buf.seek(0)
    return buf


# ════════════════════════════════════════════════════════════════════════════
#  Cog
# ════════════════════════════════════════════════════════════════════════════
class PomodoroCog(commands.Cog):
    def __init__(self, bot):
        self.bot           = bot
        self.timers: dict[int, PomodoroTimer] = {}   # vc_id → timer

    # ── Voice state listener ─────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        if member.bot:
            return

        # Duration tracking — leave old channel
        if before.channel:
            t = self.timers.get(before.channel.id)
            if t and t.state != "idle":
                t.record_leave(member.id)

        # Duration tracking — join new channel
        if after.channel:
            t = self.timers.get(after.channel.id)
            if t and t.state != "idle":
                t.record_join(member.id)

        # Camera tracking (video_required mode)
        if after.channel:
            t = self.timers.get(after.channel.id)
            if t and t.video_required and t.state == "focus":
                if not after.self_video:
                    # Camera just turned off (or joined without camera)
                    if member.id not in t.cam_off_since:
                        t.cam_off_since[member.id] = time.monotonic()
                else:
                    # Camera on — clear any warning state
                    t.cam_off_since.pop(member.id, None)
                    t.cam_warned.discard(member.id)

    # ── Avatar fetching ──────────────────────────────────────────────────
    async def _fetch_avatars(self, uids: list[int]) -> dict:
        if not PILLOW_AVAILABLE:
            return {}
        out = {}
        for uid in uids:
            try:
                user = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
                if not user:
                    continue
                url = str(user.display_avatar.replace(size=128, format="webp"))
                async with self.bot.http._HTTPClient__session.get(url) as r:
                    if r.status == 200:
                        out[uid] = Image.open(io.BytesIO(await r.read())).convert("RGBA")
            except Exception as e:
                logger.debug(f"Avatar fetch {uid}: {e}")
        return out

    # ── Interactive buttons ───────────────────────────────────────────────
    class PomodoroView(discord.ui.View):
        def __init__(self, timer, cog):
            super().__init__(timeout=None)
            self.timer = timer
            self.cog   = cog

        @discord.ui.button(label="▶  Start", style=discord.ButtonStyle.success)
        async def start(self, interaction: discord.Interaction, _):
            if self.timer.state != "idle":
                return await interaction.response.send_message("Already running!", ephemeral=True)
            self.timer.state     = "focus"
            self.timer.time_left = self.timer.focus_length * 60
            self.timer.present_members.clear()
            self.timer.snapshot()
            self.timer.task = asyncio.create_task(self.cog._run(self.timer))
            await interaction.response.defer()
            await self.cog._push_card(self.timer)

        @discord.ui.button(label="✅  Present", style=discord.ButtonStyle.primary)
        async def present(self, interaction: discord.Interaction, _):
            if interaction.user not in self.timer.voice_channel.members:
                return await interaction.response.send_message(
                    "You must be in the Pomodoro voice channel!", ephemeral=True)
            uid = interaction.user.id
            self.timer.present_members.add(uid)
            self.timer.last_present[uid] = time.monotonic()
            await interaction.response.send_message(
                f"✅ **{interaction.user.display_name}** marked as Present!", ephemeral=True)

        @discord.ui.button(label="⏹  Stop", style=discord.ButtonStyle.danger)
        async def stop(self, interaction: discord.Interaction, _):
            if self.timer.state == "idle":
                return await interaction.response.send_message("Timer isn't running!", ephemeral=True)
            if self.timer.task:
                self.timer.task.cancel()
                self.timer.task = None
            self.timer.flush()
            self.timer.state     = "idle"
            self.timer.time_left = 0
            try:
                await self.timer.voice_channel.edit(name=self.timer.original_name)
            except Exception:
                pass
            await interaction.response.defer()
            await self.cog._push_card(self.timer)

    # ── Timer loop ────────────────────────────────────────────────────────
    async def _run(self, t: PomodoroTimer):
        try:
            await self._alert(t, "focus_start")
            await self._update_vc_name(t)
            last_ch = 0

            while t.state != "idle":
                await asyncio.sleep(1)
                t.time_left -= 1

                # Video check every 60 s during focus
                if t.video_required and t.state == "focus" and t.time_left % 60 == 0:
                    await self._check_video(t)

                if t.time_left <= 0:
                    # Phase transition
                    if t.state == "focus":
                        await self._check_inactivity(t)
                        t.flush()
                        t.state     = "break"
                        t.time_left = t.break_length * 60
                        t.present_members.clear()
                        t.cam_warned.clear()
                        t.cam_off_since.clear()
                        t.snapshot()
                        await self._alert(t, "break_start")
                    else:
                        t.flush()
                        t.state         = "focus"
                        t.time_left     = t.focus_length * 60
                        t.current_cycle += 1
                        t.present_members.clear()
                        t.snapshot()
                        await self._alert(t, "focus_start")

                    await self._push_card(t)
                    await self._update_vc_name(t)

                # Refresh card every 30 s
                if t.time_left % 30 == 0:
                    await self._push_card(t)
                    now = time.time()
                    if now - last_ch > 120:
                        await self._update_vc_name(t)
                        last_ch = now

        except asyncio.CancelledError:
            pass

    # ── Video enforcement ─────────────────────────────────────────────────
    async def _check_video(self, t: PomodoroTimer):
        now = time.monotonic()
        thresh = t.inactive_threshold * 60
        for member in t.voice_channel.members:
            if member.bot:
                continue
            uid = member.id
            # If camera has been off longer than threshold
            off_since = t.cam_off_since.get(uid)
            if off_since and (now - off_since) >= thresh:
                if uid not in t.cam_warned:
                    # First offence — warn via DM
                    t.cam_warned.add(uid)
                    try:
                        embed = discord.Embed(
                            title="📷  Camera Warning",
                            description=(
                                f"Your camera has been off for over **{t.inactive_threshold} minute(s)** "
                                f"in the Pomodoro session **{t.name}**.\n\n"
                                "Please turn your camera on or you will be removed from the voice channel!"
                            ),
                            color=discord.Color.orange()
                        )
                        await member.send(embed=embed)
                        logger.info(f"Camera warning DM sent to {member}")
                    except Exception:
                        pass
                elif (now - off_since) >= thresh * 2:
                    # Second offence — kick from VC
                    try:
                        await member.move_to(None, reason="Camera off during Pomodoro (video required).")
                        embed = discord.Embed(
                            title="🚫  Removed — Camera Off",
                            description=(
                                f"You were removed from **{t.voice_channel.name}** because your camera "
                                f"was off for over **{t.inactive_threshold * 2} minutes** in a "
                                "camera-required Pomodoro session."
                            ),
                            color=discord.Color.red()
                        )
                        await member.send(embed=embed)
                        t.cam_off_since.pop(uid, None)
                        t.cam_warned.discard(uid)
                        logger.info(f"Kicked {member} for camera off in Pomodoro")
                    except Exception:
                        pass

    # ── Present / inactivity check ────────────────────────────────────────
    async def _check_inactivity(self, t: PomodoroTimer):
        now     = time.monotonic()
        thresh  = t.inactive_threshold * 60
        for member in t.voice_channel.members:
            if member.bot:
                continue
            uid = member.id
            last = t.last_present.get(uid, 0)
            if uid not in t.present_members and (now - last) > thresh:
                try:
                    await member.move_to(None, reason="Inactive in Pomodoro session.")
                    embed = discord.Embed(
                        title="🛏️  Removed — Inactivity",
                        description=(
                            f"You were removed from **{t.voice_channel.name}** because you "
                            f"didn't press **Present** within {t.inactive_threshold} minute(s).\n"
                            "Rejoin and stay active! 💪"
                        ),
                        color=discord.Color.orange()
                    )
                    await member.send(embed=embed)
                except Exception:
                    pass
            else:
                t.last_present[uid] = now

    # ── Push / refresh card ───────────────────────────────────────────────
    async def _push_card(self, t: PomodoroTimer):
        colour_map = {
            "focus": discord.Color.from_rgb(*RED_FOCUS),
            "break": discord.Color.from_rgb(*GREEN_BREAK),
            "idle":  discord.Color.light_grey(),
        }
        embed = discord.Embed(
            title=f"⏱️  {t.voice_channel.name}  —  {t.name}",
            color=colour_map[t.state]
        )
        mins, secs = t.time_left // 60, t.time_left % 60
        ts = f"{mins:02d}:{secs:02d}"

        state_msgs = {
            "focus": f"🎯 **Focus — Cycle #{t.current_cycle}**\nWork hard! Time remaining: **{ts}**",
            "break": f"☕ **Break Time!**\nRelax and recharge! Time remaining: **{ts}**",
            "idle":  f"💤 **Idle** — Press **▶ Start** to begin\n`{t.focus_length}m` focus / `{t.break_length}m` break",
        }
        embed.description = state_msgs[t.state]

        hints = {
            "focus": "Click ✅ Present to confirm you're active!",
            "break": "Next focus cycle starts automatically.",
            "idle":  "Auto-refresh every 30 s · Camera required: " + ("Yes 📷" if t.video_required else "No"),
        }
        embed.set_footer(text=hints[t.state])

        view = self.PomodoroView(t, self)
        file = None

        if PILLOW_AVAILABLE:
            try:
                guild   = self.bot.get_guild(t.guild_id)
                uids    = [uid for uid, _ in t.sorted_participants()]
                avatars = await self._fetch_avatars(uids[:MAX_MEMBERS])
                buf     = await asyncio.get_event_loop().run_in_executor(
                    None, build_card, t, avatars, guild
                )
                file = discord.File(buf, filename="pomodoro.png")
                embed.set_image(url="attachment://pomodoro.png")
            except Exception as e:
                logger.warning(f"Card render error: {e}")

        ch = t.notification_channel
        try:
            if file:
                try:
                    if t.status_message:
                        await t.status_message.delete()
                except Exception:
                    pass
                t.status_message = await ch.send(embed=embed, view=view, file=file)
            else:
                if t.status_message:
                    await t.status_message.edit(embed=embed, view=view)
                else:
                    t.status_message = await ch.send(embed=embed, view=view)
        except Exception as e:
            logger.debug(f"Card send error: {e}")

    # ── VC name updater ───────────────────────────────────────────────────
    async def _update_vc_name(self, t: PomodoroTimer):
        try:
            names = {
                "focus": f"🎯 Focus | {t.time_left // 60}m",
                "break": f"☕ Break | {t.time_left // 60}m",
                "idle":  t.original_name,
            }
            await t.voice_channel.edit(name=names[t.state])
        except Exception:
            pass

    # ── TTS alerts ────────────────────────────────────────────────────────
    async def _alert(self, t: PomodoroTimer, kind: str):
        if not t.voice_alerts:
            return
        titles = {
            "focus_start": ("🎯 Focus Time Has Started!", "Quiet down and get to work!"),
            "break_start":  ("☕  Break Time!", "Step away, stretch, hydrate!"),
        }
        title, desc = titles.get(kind, ("", ""))
        if not title:
            return
        embed = discord.Embed(title=title, description=desc, color=discord.Color.blurple())
        try:
            await t.voice_channel.send(embed=embed, tts=True)
        except Exception:
            pass

    # ════════════════════════════════════════════════════════════════════
    #  Slash command
    # ════════════════════════════════════════════════════════════════════
    @app_commands.command(
        name="pomodoro",
        description="Create a Pomodoro timer in your voice channel."
    )
    @app_commands.describe(
        focus_length     = "Focus period in minutes (default 25)",
        break_length     = "Break period in minutes (default 5)",
        name             = "Session label shown on the card (default: VC name)",
        notification_channel = "Text or voice channel where the live card is posted",
        video_required   = "Require camera on — kick if off beyond threshold (default False)",
        inactive_threshold = "Minutes before inactivity / camera-off action (default 5)",
    )
    async def pomodoro(
        self,
        interaction: discord.Interaction,
        focus_length:          app_commands.Range[int, 1, 120] = 25,
        break_length:          app_commands.Range[int, 1, 60]  = 5,
        name:                  str  = None,
        notification_channel:  discord.TextChannel = None,
        video_required:        bool = False,
        inactive_threshold:    app_commands.Range[int, 1, 60] = 5,
    ):
        if not interaction.guild:
            return await interaction.response.send_message(
                "This command can only be used in a server!", ephemeral=True)

        # Must be in a voice channel
        vs = interaction.user.voice
        if not vs or not vs.channel:
            return await interaction.response.send_message(
                "❌ **You must join a voice channel first!** "
                "No Pomodoro can be created without a voice channel.",
                ephemeral=True
            )

        vc = vs.channel

        # One timer per VC
        if vc.id in self.timers:
            return await interaction.response.send_message(
                f"❌ A Pomodoro timer is already running in **{vc.name}**!", ephemeral=True)

        # Resolve notification channel
        notif_ch = notification_channel or interaction.channel

        timer = PomodoroTimer(
            guild_id             = interaction.guild_id,
            voice_channel        = vc,
            notification_channel = notif_ch,
            focus_length         = focus_length,
            break_length         = break_length,
            name                 = name,
            video_required       = video_required,
            inactive_threshold   = inactive_threshold,
        )
        self.timers[vc.id] = timer

        # Confirmation embed
        vid_txt  = "📷 **Camera required** — users off-camera will be warned then removed." \
                   if video_required else "🎥 Camera not required."
        conf = discord.Embed(
            title="⏱️  Pomodoro Created!",
            description=(
                f"**Voice Channel:** {vc.mention}\n"
                f"**Session Name:** {timer.name}\n"
                f"**Focus / Break:** `{focus_length}m` / `{break_length}m`\n"
                f"**Notifications:** {notif_ch.mention}\n"
                f"**Inactivity Threshold:** `{inactive_threshold}` minutes\n"
                f"{vid_txt}\n\n"
                f"The live card has been posted in {notif_ch.mention}. Press **▶ Start** to begin!"
            ),
            color=discord.Color.blurple()
        )
        await interaction.response.send_message(embed=conf, ephemeral=True)

        # Post the live card
        await self._push_card(timer)

    @app_commands.command(name="timers", description="List all active Pomodoro timers in this server.")
    async def timers(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message(
                "This command can only be used in a server!", ephemeral=True)

        embed = discord.Embed(title="⏱️  Active Pomodoro Timers", color=discord.Color.blue())
        lines = [
            f"• **{t.name}** — {t.voice_channel.mention} — "
            f"`{'RUNNING' if t.state != 'idle' else 'IDLE'}` "
            f"({t.focus_length}/{t.break_length}m)"
            f"{'  📷' if t.video_required else ''}"
            for vc_id, t in self.timers.items()
            if t.guild_id == interaction.guild_id
        ]
        embed.description = "\n".join(lines) if lines else "No active Pomodoro timers in this server."
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(PomodoroCog(bot))
