import discord
from discord import app_commands
from discord.ext import commands
import logging
import io
import datetime
from PIL import Image, ImageDraw

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
    _BG_TOP,
    _BG_BOT
)

logger = logging.getLogger("leaderboard")

class Leaderboard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="leaderboard", description="Display the server's XP leaderboard.")
    async def leaderboard(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return

        await interaction.response.defer()

        try:
            # Fetch all top users from DB
            top_users = database.get_top_users(interaction.guild_id, limit=100)
            
            if not top_users:
                await interaction.followup.send("No users are on the leaderboard yet!")
                return
            
            total_pages = max(1, math.ceil(len(top_users) / 10))
            
            file = await self.generate_leaderboard_card(interaction.guild, top_users, 1)
            
            content = f"🏆 **{interaction.guild.name} Leaderboard** - Top members by study XP"
            view = LeaderboardMainView(interaction.guild, top_users, 1, total_pages, self)
            await interaction.followup.send(content=content, file=file, view=view)
            
        except Exception as e:
            logger.error(f"Error generating leaderboard: {e}")
            await interaction.followup.send(f"❌ Failed to generate leaderboard: {e}")

    async def generate_leaderboard_card(self, guild: discord.Guild, all_users: list, page: int = 1) -> discord.File:
        W, H = 1080, 1080
        
        total_pages = max(1, math.ceil(len(all_users) / 10))
        page = max(1, min(page, total_pages))
        start_idx = (page - 1) * 10
        top_users = all_users[start_idx:start_idx + 10]
        
        # ── Background ────────────────────────────────────────────────────────
        canvas = make_gradient_bg(W, H)
        
        # ── Frosted-Glass Panel ───────────────────────────────────────────────
        PX1, PY1, PX2, PY2 = 40, 40, W - 40, H - 40
        canvas = draw_glass_panel(canvas, PX1, PY1, PX2, PY2, radius=24)
        
        draw = ImageDraw.Draw(canvas)
        
        # ── Fonts ─────────────────────────────────────────────────────────────
        f_title = bold(54)
        f_sub   = regular(24)
        f_rank  = bold(32)
        f_name  = bold(28)
        f_xp    = regular(24)
        
        # ── Title ─────────────────────────────────────────────────────────────
        title_text = "LEADERBOARD"
        title_bb = draw.textbbox((0, 0), title_text, font=f_title)
        title_w = title_bb[2] - title_bb[0]
        draw.text((W // 2 - title_w // 2, PY1 + 30), title_text, font=f_title, fill=TEXT_W)
        
        server_text = f"{guild.name.upper()}"
        server_bb = draw.textbbox((0, 0), server_text, font=f_sub)
        server_w = server_bb[2] - server_bb[0]
        draw.text((W // 2 - server_w // 2, PY1 + 90), server_text, font=f_sub, fill=TEXT_M)
        
        draw.line(
            [(W // 2 - 300, PY1 + 140), (W // 2 + 300, PY1 + 140)],
            fill=(*GOLD, 160), width=3
        )
        
        # ── Top 3 (Side by Side) ──────────────────────────────────────────────
        top_3 = top_users[:3]
        
        # Positions: Rank 2 (Left), Rank 1 (Center), Rank 3 (Right)
        positions = []
        if len(top_3) >= 1:
            positions.append((1, top_3[0], W // 2, 220)) # Rank 1 (Center)
        if len(top_3) >= 2:
            positions.append((2, top_3[1], W // 2 - 300, 160)) # Rank 2 (Left)
        if len(top_3) >= 3:
            positions.append((3, top_3[2], W // 2 + 300, 160)) # Rank 3 (Right)
            
        y_top3 = PY1 + 190
        
        for rank, user_data, x_center, avatar_size in positions:
            user_id = user_data["user_id"]
            xp = user_data["xp"]
            member = guild.get_member(user_id)
            if not member:
                try:
                    member = await guild.fetch_member(user_id)
                except:
                    member = None
            
            display_name = member.display_name if member else f"User {user_id}"
            
            # Avatar
            pfp = await fetch_avatar(member) if member else Image.new("RGBA", (200, 200), (14, 22, 48, 255))
            pfp_circle = crop_circle(pfp, avatar_size)
            
            pfp_x = x_center - avatar_size // 2
            pfp_y = y_top3 + (40 if rank != 1 else 0)
            
            canvas.alpha_composite(pfp_circle, (pfp_x, pfp_y))
            
            # Ring
            draw = ImageDraw.Draw(canvas)
            ring_color = (255, 215, 0) if rank == 1 else (220, 224, 230) if rank == 2 else (205, 127, 50)
            draw.ellipse(
                [pfp_x - 6, pfp_y - 6, pfp_x + avatar_size + 6, pfp_y + avatar_size + 6],
                outline=(*ring_color, 220), width=6
            )
            
            # Crown for Rank 1
            if rank == 1:
                cx, cy = x_center, pfp_y - 25
                draw.polygon(
                    [(cx - 30, cy), (cx - 40, cy - 30), (cx - 15, cy - 10), (cx, cy - 40), (cx + 15, cy - 10), (cx + 40, cy - 30), (cx + 30, cy)],
                    fill=(*ring_color, 255)
                )
            
            # Rank Badge
            badge_r = 26
            badge_x = pfp_x + avatar_size // 2
            badge_y = pfp_y + avatar_size + 4
            draw.ellipse(
                [badge_x - badge_r, badge_y - badge_r, badge_x + badge_r, badge_y + badge_r],
                fill=(12, 20, 52, 255), outline=(*ring_color, 255), width=3
            )
            draw.text((badge_x - 10, badge_y - 18), str(rank), font=bold(28), fill=(*ring_color, 255))
            
            # Name
            name_bb = draw.textbbox((0,0), display_name, font=f_name)
            name_w = name_bb[2] - name_bb[0]
            draw.text((x_center - name_w // 2, badge_y + 24), display_name, font=f_name, fill=TEXT_W)
            
            # Time Format (HH:MM)
            hours = xp // 60
            minutes = xp % 60
            xp_str = f"{hours:02d}:{minutes:02d}"
            xp_bb = draw.textbbox((0,0), xp_str, font=f_xp)
            xp_w = xp_bb[2] - xp_bb[0]
            
            xp_bg_w = xp_w + 30
            xp_bg_h = 36
            xp_bg_x = x_center - xp_bg_w // 2
            xp_bg_y = badge_y + 60
            
            draw.rounded_rectangle(
                [xp_bg_x, xp_bg_y, xp_bg_x + xp_bg_w, xp_bg_y + xp_bg_h],
                radius=18, fill=(*ring_color, 40)
            )
            draw.text((x_center - xp_w // 2, xp_bg_y + 4), xp_str, font=f_xp, fill=(*ring_color, 255))
            
        # ── Rank 4-10 (List Below) ────────────────────────────────────────────
        rest = top_users[3:]
        if rest:
            list_start_y = y_top3 + 360
            
            row_y = list_start_y
            row_height = 60
            row_width = 800
            start_x = W // 2 - row_width // 2
            
            for i, user_data in enumerate(rest):
                rank = i + 4
                user_id = user_data["user_id"]
                xp = user_data["xp"]
                
                member = guild.get_member(user_id)
                display_name = member.display_name if member else f"User {user_id}"
                
                # Draw translucent row background
                draw.rounded_rectangle(
                    [start_x, row_y, start_x + row_width, row_y + row_height],
                    radius=12, fill=(255, 255, 255, 10)
                )
                
                # Rank
                draw.text((start_x + 24, row_y + 12), f"#{rank}", font=f_rank, fill=(*GOLD, 200))
                
                # Name
                draw.text((start_x + 100, row_y + 14), display_name, font=f_name, fill=TEXT_W)
                
                # Time Badge on the right
                hours = xp // 60
                minutes = xp % 60
                xp_str = f"{hours:02d}:{minutes:02d}"
                xp_bb = draw.textbbox((0,0), xp_str, font=f_xp)
                xp_w = xp_bb[2] - xp_bb[0]
                
                xp_bg_w = xp_w + 40
                xp_bg_h = 40
                xp_bg_x = start_x + row_width - xp_bg_w - 10
                xp_bg_y = row_y + 10
                
                draw.rounded_rectangle(
                    [xp_bg_x, xp_bg_y, xp_bg_x + xp_bg_w, xp_bg_y + xp_bg_h],
                    radius=20, fill=(*GOLD, 30), outline=(*GOLD, 100), width=1
                )
                draw.text((xp_bg_x + 20, xp_bg_y + 6), xp_str, font=f_xp, fill=(*GOLD, 255))
                
                row_y += row_height + 14

        # ── Save ──────────────────────────────────────────────────────────────
        fp = io.BytesIO()
        canvas.save(fp, format="PNG")
        fp.seek(0)
        return discord.File(fp, filename="leaderboard.png")

class LeaderboardMainView(discord.ui.View):
    def __init__(self, guild: discord.Guild, all_users: list, current_page: int, total_pages: int, cog):
        super().__init__(timeout=180)
        self.guild = guild
        self.all_users = all_users
        self.current_page = current_page
        self.total_pages = total_pages
        self.cog = cog
        
        self.prev_btn.disabled = self.current_page <= 1
        self.next_btn.disabled = self.current_page >= self.total_pages

    async def update_message(self, interaction: discord.Interaction):
        self.prev_btn.disabled = self.current_page <= 1
        self.next_btn.disabled = self.current_page >= self.total_pages
        
        file = await self.cog.generate_leaderboard_card(self.guild, self.all_users, self.current_page)
        
        content = f"🏆 **{self.guild.name} Leaderboard** - Top members by study XP (Page {self.current_page}/{self.total_pages})"
        
        await interaction.response.edit_message(content=content, attachments=[file], view=self, embed=None)

    @discord.ui.button(label="◀️ Prev", style=discord.ButtonStyle.secondary, custom_id="lb_prev")
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        await self.update_message(interaction)

    @discord.ui.button(label="▶️ Next", style=discord.ButtonStyle.secondary, custom_id="lb_next")
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        await self.update_message(interaction)

async def setup(bot: commands.Bot):
    await bot.add_cog(Leaderboard(bot))
