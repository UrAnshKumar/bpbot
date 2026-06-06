import discord
from discord import app_commands
from discord.ext import commands
import logging
import io
import os
from PIL import Image, ImageDraw, ImageFilter

import database
from cogs.pomodoro import (
    make_gradient_bg,
    draw_glass_panel,
    fetch_avatar,
    crop_circle,
    bold,
    regular,
    TEXT_W,
    TEXT_M,
    GOLD,
    FOCUS_CLR,
    BREAK_CLR,
)

logger = logging.getLogger("profile")

STREAK_CLR   = (255, 140,  20)   # warm orange for fire/streak
STREAK_HOT   = (255, 210,  60)   # gold-orange for long streaks
TAG_CLR      = ( 90,  50, 180)   # purple tag pill fill
TAG_BORDER   = (150, 100, 255)   # pill border


class Profile(commands.Cog):
    """User profile cog — renders premium profile cards with stats, streaks, and custom tags."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ─── Slash Command ────────────────────────────────────────────────────────

    @app_commands.command(name="profile", description="View your profile or another user's profile.")
    @app_commands.describe(user="The user whose profile to view (optional, defaults to you).")
    async def profile(self, interaction: discord.Interaction, user: discord.Member = None):
        if not interaction.guild:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return

        await interaction.response.defer()

        target = user or interaction.user
        is_own = target.id == interaction.user.id

        try:
            card_file = await self.generate_profile_card(target, interaction.guild)

            view = ProfileView(target, interaction.user, self) if is_own else None

            content = (
                f"📋 **{target.display_name}'s Profile**"
                if not is_own
                else f"📋 **Your Profile**"
            )
            await interaction.followup.send(content=content, file=card_file, view=view)

        except Exception as e:
            logger.error(f"Error generating profile card: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Failed to generate profile card: {e}")

    # ─── Card Generation ──────────────────────────────────────────────────────

    async def generate_profile_card(self, member: discord.Member, guild: discord.Guild) -> discord.File:
        W, H = 1080, 1080
        guild_id = guild.id
        user_id  = member.id

        # ── Fetch all data ────────────────────────────────────────────────────
        xp         = database.get_user_xp(guild_id, user_id)
        coins      = database.get_user_coins(guild_id, user_id)
        streak     = database.get_streak(guild_id, user_id)
        profile    = database.get_profile(guild_id, user_id)
        warnings   = database.get_warnings(guild_id, user_id)
        ranked_roles = database.get_ranked_roles(guild_id)
        awarded    = []
        for rr in ranked_roles:
            if database.is_rank_awarded(guild_id, user_id, rr["role_id"]):
                awarded.append(rr)

        hours   = xp // 60
        minutes = xp % 60
        current_streak  = streak["current_streak"]
        longest_streak  = streak["longest_streak"]
        daily_minutes   = streak["daily_minutes"]
        tags_raw = profile.get("tags", "") or ""
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []
        warn_count = len(warnings)

        # Current highest rank
        top_rank_name = "N/A"
        if awarded:
            top_rr = awarded[-1]
            role_obj = guild.get_role(top_rr["role_id"])
            top_rank_name = role_obj.name if role_obj else "Unknown"

        # ── Canvas ────────────────────────────────────────────────────────────
        canvas = make_gradient_bg(W, H)

        # Outer glass panel
        PX1, PY1, PX2, PY2 = 50, 50, W - 50, H - 50
        canvas = draw_glass_panel(canvas, PX1, PY1, PX2, PY2, radius=28)

        draw = ImageDraw.Draw(canvas)

        # ── Title Row ─────────────────────────────────────────────────────────
        f_title    = bold(46)
        f_sub      = regular(22)
        f_label    = bold(22)
        f_value    = bold(32)
        f_username = bold(38)
        f_tag      = bold(20)
        f_stat_lbl = regular(20)

        title_text = "USER PROFILE"
        title_bb   = draw.textbbox((0, 0), title_text, font=f_title)
        title_w    = title_bb[2] - title_bb[0]
        draw.text((W // 2 - title_w // 2, PY1 + 36), title_text, font=f_title, fill=TEXT_W)

        server_text = guild.name.upper()
        srv_bb = draw.textbbox((0, 0), server_text, font=f_sub)
        srv_w  = srv_bb[2] - srv_bb[0]
        draw.text((W // 2 - srv_w // 2, PY1 + 92), server_text, font=f_sub, fill=TEXT_M)

        draw.line([(W // 2 - 300, PY1 + 130), (W // 2 + 300, PY1 + 130)], fill=(*GOLD, 140), width=2)

        # ── Avatar ────────────────────────────────────────────────────────────
        avatar_raw    = await fetch_avatar(member)
        AVATAR_SIZE   = 170
        avatar_circle = crop_circle(avatar_raw, AVATAR_SIZE)

        pfp_x = W // 2 - AVATAR_SIZE // 2
        pfp_y = PY1 + 155
        canvas.alpha_composite(avatar_circle, (pfp_x, pfp_y))

        # Gold ring around avatar
        ring_color = (*GOLD, 220) if current_streak < 7 else (*STREAK_HOT, 255)
        draw.ellipse([pfp_x - 5, pfp_y - 5, pfp_x + AVATAR_SIZE + 5, pfp_y + AVATAR_SIZE + 5],
                     outline=ring_color, width=5)

        # ── Streak Badge (top-right of avatar) ────────────────────────────────
        _draw_streak_badge(canvas, draw,
                           bx=pfp_x + AVATAR_SIZE - 10,
                           by=pfp_y - 10,
                           streak=current_streak)

        # ── Display name / username ───────────────────────────────────────────
        name_y = pfp_y + AVATAR_SIZE + 20
        name_text = member.display_name
        name_bb   = draw.textbbox((0, 0), name_text, font=f_username)
        name_w    = name_bb[2] - name_bb[0]
        draw.text((W // 2 - name_w // 2, name_y), name_text, font=f_username, fill=TEXT_W)

        tag_text = f"@{member.name}"
        tag_bb   = draw.textbbox((0, 0), tag_text, font=f_sub)
        tag_w    = tag_bb[2] - tag_bb[0]
        draw.text((W // 2 - tag_w // 2, name_y + 48), tag_text, font=f_sub, fill=TEXT_M)

        # ── Custom Tag Pills ──────────────────────────────────────────────────
        tags_y = name_y + 100
        if tags:
            _draw_tag_pills(canvas, draw, tags, W, tags_y, f_tag)
        else:
            no_tag = "No tags yet"
            ntb = draw.textbbox((0, 0), no_tag, font=f_sub)
            ntw = ntb[2] - ntb[0]
            draw.text((W // 2 - ntw // 2, tags_y + 8), no_tag, font=f_sub, fill=TEXT_M)

        # ── Divider ───────────────────────────────────────────────────────────
        div_y = tags_y + 58
        draw.line([(PX1 + 50, div_y), (PX2 - 50, div_y)], fill=(255, 255, 255, 30), width=1)

        # ── Stat Row 1: Study Time | Coin Balance | Rank ──────────────────────
        row1_y = div_y + 18
        _draw_stat_row(
            canvas, draw, W, row1_y, height=130,
            stats=[
                ("⏱  STUDY TIME", f"{hours}h {minutes:02d}m" if xp > 0 else "N/A",   GOLD),
                ("🪙  BP COINS",   _coins_str(coins),                                  GOLD),
                ("🎓  RANK",       top_rank_name,                                       BREAK_CLR),
            ]
        )

        # ── Divider ───────────────────────────────────────────────────────────
        row1_bot = row1_y + 140
        draw.line([(PX1 + 50, row1_bot), (PX2 - 50, row1_bot)], fill=(255, 255, 255, 30), width=1)

        # ── Stat Row 2: Streak | Longest | Daily Progress | Warnings ─────────
        row2_y = row1_bot + 18
        _draw_stat_row(
            canvas, draw, W, row2_y, height=130,
            stats=[
                ("🔥  STREAK",    f"{current_streak}d",  STREAK_CLR),
                ("🏆  LONGEST",   f"{longest_streak}d",  GOLD),
                ("📅  TODAY",     f"{daily_minutes}m",   BREAK_CLR),
                ("⚠️  WARNS",     str(warn_count),       FOCUS_CLR),
            ]
        )

        # ── Footer ────────────────────────────────────────────────────────────
        foot_text = "BOOTSTRAP PARADOX STUDY BOT"
        ft_bb = draw.textbbox((0, 0), foot_text, font=f_sub)
        ft_w  = ft_bb[2] - ft_bb[0]
        draw.text((W // 2 - ft_w // 2, PY2 - 50), foot_text, font=f_sub, fill=TEXT_M)

        # ── Save ──────────────────────────────────────────────────────────────
        fp = io.BytesIO()
        canvas.save(fp, format="PNG")
        fp.seek(0)
        return discord.File(fp, filename="profile.png")


# ─── Helper Drawing Functions ─────────────────────────────────────────────────

def _draw_streak_badge(canvas: Image.Image, draw: ImageDraw.ImageDraw,
                        bx: int, by: int, streak: int):
    """Draw a circular streak badge with a fire emoji look."""
    R = 34
    # Glow if streak is hot
    if streak >= 7:
        glow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse([bx - R - 8, by - R - 8, bx + R + 8, by + R + 8],
                                      fill=(255, 160, 20, 50))
        glow = glow.filter(ImageFilter.GaussianBlur(radius=8))
        canvas.alpha_composite(glow)

    # Badge background
    draw.ellipse([bx - R, by - R, bx + R, by + R],
                 fill=(20, 12, 40, 220), outline=(255, 160, 20, 200), width=3)

    # Flame icon (emoji text drawn small)
    fire = "🔥"
    try:
        from cogs.pomodoro import bold as pbold
        f_fire = pbold(18)
    except Exception:
        f_fire = None
    fire_bb = draw.textbbox((0, 0), fire, font=f_fire) if f_fire else (0, 0, 20, 20)
    fire_w  = fire_bb[2] - fire_bb[0]
    draw.text((bx - fire_w // 2, by - R + 4), fire, font=f_fire, fill=(255, 180, 30, 255))

    # Streak number
    streak_txt = str(streak)
    try:
        f_num = pbold(16)
    except Exception:
        f_num = None
    num_bb = draw.textbbox((0, 0), streak_txt, font=f_num)
    num_w  = num_bb[2] - num_bb[0]
    draw.text((bx - num_w // 2, by + 4), streak_txt, font=f_num, fill=(255, 255, 255, 255))


def _draw_tag_pills(canvas: Image.Image, draw: ImageDraw.ImageDraw,
                     tags: list, W: int, y: int, font):
    """Render frosted glass pill badges for each tag, centred horizontally."""
    PAD_X, PAD_Y, GAP = 20, 10, 14
    pill_sizes = []
    for tag in tags:
        bb = draw.textbbox((0, 0), tag, font=font)
        tw = bb[2] - bb[0]
        th = bb[3] - bb[1]
        pill_sizes.append((tw, th))

    pill_widths = [tw + PAD_X * 2 for tw, _ in pill_sizes]
    total_w = sum(pill_widths) + GAP * (len(tags) - 1)
    start_x = W // 2 - total_w // 2

    pill_h = (pill_sizes[0][1] if pill_sizes else 20) + PAD_Y * 2

    x = start_x
    for i, (tag, (tw, th)) in enumerate(zip(tags, pill_sizes)):
        pw = pill_widths[i]
        # Frosted pill background
        ov = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        d  = ImageDraw.Draw(ov)
        d.rounded_rectangle([x, y, x + pw, y + pill_h], radius=pill_h // 2,
                             fill=(*TAG_CLR, 80))
        d.rounded_rectangle([x, y, x + pw, y + pill_h], radius=pill_h // 2,
                             outline=(*TAG_BORDER, 160), width=2)
        canvas.alpha_composite(ov)
        # Text
        draw.text((x + PAD_X, y + PAD_Y), tag, font=font, fill=(220, 200, 255, 255))
        x += pw + GAP


def _draw_stat_row(canvas: Image.Image, draw: ImageDraw.ImageDraw,
                    W: int, y: int, height: int, stats: list):
    """
    Render a horizontal row of stat boxes.
    stats: list of (label, value, accent_color_rgb)
    """
    n      = len(stats)
    PX1, PX2 = 100, W - 100
    total_w = PX2 - PX1
    cell_w  = (total_w - (n - 1) * 12) // n

    f_lbl = regular(20)
    f_val = bold(30)

    for i, (label, value, accent) in enumerate(stats):
        bx1 = PX1 + i * (cell_w + 12)
        bx2 = bx1 + cell_w
        by1 = y
        by2 = y + height

        canvas = draw_glass_panel(canvas, bx1, by1, bx2, by2, radius=16)
        draw   = ImageDraw.Draw(canvas)

        cx = (bx1 + bx2) // 2

        # Label
        lb = draw.textbbox((0, 0), label, font=f_lbl)
        lw = lb[2] - lb[0]
        draw.text((cx - lw // 2, by1 + 18), label, font=f_lbl, fill=TEXT_M)

        # Value
        vb = draw.textbbox((0, 0), value, font=f_val)
        vw = vb[2] - vb[0]
        draw.text((cx - vw // 2, by1 + 58), value, font=f_val, fill=(*accent, 255))

    # return updated canvas
    return canvas


def _coins_str(coins: int) -> str:
    if coins >= 1_000_000:
        return f"{coins / 1_000_000:.1f}M"
    if coins >= 1_000:
        return f"{coins / 1_000:.1f}K"
    return str(coins)


# ─── Interactive View & Modals ────────────────────────────────────────────────

class ProfileView(discord.ui.View):
    def __init__(self, profile_owner: discord.Member, viewer: discord.Member, cog: Profile):
        super().__init__(timeout=300)
        self.owner  = profile_owner
        self.viewer = viewer
        self.cog    = cog

    @discord.ui.button(label="✏️ Edit Tags", style=discord.ButtonStyle.primary, custom_id="profile_edit_tags")
    async def edit_tags_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message("❌ Only the profile owner can edit tags.", ephemeral=True)
            return
        # Pre-fill current tags
        profile = database.get_profile(interaction.guild_id, self.owner.id)
        current_tags = profile.get("tags", "") or ""
        await interaction.response.send_modal(TagEditModal(self.cog, self.owner, current_tags))

    @discord.ui.button(label="📝 Edit Bio", style=discord.ButtonStyle.secondary, custom_id="profile_edit_bio")
    async def edit_bio_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message("❌ Only the profile owner can edit their bio.", ephemeral=True)
            return
        profile = database.get_profile(interaction.guild_id, self.owner.id)
        current_bio = profile.get("bio", "") or ""
        await interaction.response.send_modal(BioEditModal(self.cog, self.owner, current_bio))

    @discord.ui.button(label="🔄 Refresh", style=discord.ButtonStyle.secondary, custom_id="profile_refresh")
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        card_file = await self.cog.generate_profile_card(self.owner, interaction.guild)
        await interaction.edit_original_response(attachments=[card_file])


class TagEditModal(discord.ui.Modal, title="Edit Profile Tags"):
    tags_input = discord.ui.TextInput(
        label="Tags (comma-separated, max 5 tags)",
        placeholder="e.g. 📚 Studious, 🎮 Gamer, 🧠 Math Nerd",
        max_length=200,
        required=False,
        style=discord.TextStyle.short,
    )

    def __init__(self, cog: Profile, owner: discord.Member, current_tags: str):
        super().__init__()
        self.cog   = cog
        self.owner = owner
        self.tags_input.default = current_tags

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.tags_input.value or ""
        # Parse and sanitise
        parts = [t.strip()[:20] for t in raw.split(",") if t.strip()][:5]
        clean = ", ".join(parts)
        database.set_profile_tags(interaction.guild_id, self.owner.id, clean)

        # Regenerate card
        await interaction.response.defer()
        card_file = await self.cog.generate_profile_card(self.owner, interaction.guild)
        await interaction.edit_original_response(
            content=f"✅ Tags updated! **{clean or 'None'}**",
            attachments=[card_file]
        )


class BioEditModal(discord.ui.Modal, title="Edit Profile Bio"):
    bio_input = discord.ui.TextInput(
        label="Bio",
        placeholder="Tell everyone about yourself...",
        max_length=150,
        required=False,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self, cog: Profile, owner: discord.Member, current_bio: str):
        super().__init__()
        self.cog   = cog
        self.owner = owner
        self.bio_input.default = current_bio

    async def on_submit(self, interaction: discord.Interaction):
        bio = (self.bio_input.value or "").strip()[:150]
        database.set_profile_bio(interaction.guild_id, self.owner.id, bio)

        await interaction.response.defer()
        card_file = await self.cog.generate_profile_card(self.owner, interaction.guild)
        await interaction.edit_original_response(
            content="✅ Bio updated!",
            attachments=[card_file]
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Profile(bot))
