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
            # Fetch top users from DB
            top_users = database.get_top_users(interaction.guild_id, limit=10)
            
            if not top_users:
                await interaction.followup.send("No users are on the leaderboard yet!")
                return

            file = await self.generate_leaderboard_card(interaction.guild, top_users)
            
            embed = discord.Embed(
                title=f"🏆 {interaction.guild.name} Leaderboard",
                description="Top members by study XP",
                color=discord.Color.gold()
            )
            embed.set_image(url="attachment://leaderboard.png")
            
            await interaction.followup.send(embed=embed, file=file)
            
        except Exception as e:
            logger.error(f"Error generating leaderboard: {e}")
            await interaction.followup.send(f"❌ Failed to generate leaderboard: {e}")

    async def generate_leaderboard_card(self, guild: discord.Guild, top_users: list) -> discord.File:
        W, H = 1120, 640
        
        # ── Background ────────────────────────────────────────────────────────
        canvas = make_gradient_bg(W, H)
        
        # ── Frosted-Glass Panel ───────────────────────────────────────────────
        PX1, PY1, PX2, PY2 = 40, 40, W - 40, H - 40
        canvas = draw_glass_panel(canvas, PX1, PY1, PX2, PY2)
        
        draw = ImageDraw.Draw(canvas)
        
        # ── Fonts ─────────────────────────────────────────────────────────────
        f_title = bold(36)
        f_sub   = regular(18)
        f_rank  = bold(28)
        f_name  = bold(22)
        f_xp    = regular(18)
        
        # ── Title ─────────────────────────────────────────────────────────────
        title_text = f"{guild.name.upper()} LEADERBOARD"
        title_bb = draw.textbbox((0, 0), title_text, font=f_title)
        title_w = title_bb[2] - title_bb[0]
        draw.text((W // 2 - title_w // 2, PY1 + 30), title_text, font=f_title, fill=TEXT_W)
        
        draw.line(
            [(W // 2 - 200, PY1 + 80), (W // 2 + 200, PY1 + 80)],
            fill=(*GOLD, 140), width=2
        )
        
        # ── Top 3 (Side by Side) ──────────────────────────────────────────────
        top_3 = top_users[:3]
        
        # Positions: Rank 2 (Left), Rank 1 (Center), Rank 3 (Right)
        positions = []
        if len(top_3) >= 1:
            positions.append((1, top_3[0], W // 2)) # Rank 1 (Center)
        if len(top_3) >= 2:
            positions.append((2, top_3[1], W // 2 - 250)) # Rank 2 (Left)
        if len(top_3) >= 3:
            positions.append((3, top_3[2], W // 2 + 250)) # Rank 3 (Right)
            
        y_top3 = PY1 + 130
        
        for rank, user_data, x_center in positions:
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
            avatar_size = 120 if rank == 1 else 90
            pfp = await fetch_avatar(member) if member else Image.new("RGBA", (100, 100), (14, 22, 48, 255))
            pfp_circle = crop_circle(pfp, avatar_size)
            
            pfp_x = x_center - avatar_size // 2
            pfp_y = y_top3 + (15 if rank != 1 else 0)
            
            canvas.alpha_composite(pfp_circle, (pfp_x, pfp_y))
            
            # Ring
            draw = ImageDraw.Draw(canvas)
            ring_color = (255, 215, 0) if rank == 1 else (192, 192, 192) if rank == 2 else (205, 127, 50)
            draw.ellipse(
                [pfp_x - 3, pfp_y - 3, pfp_x + avatar_size + 3, pfp_y + avatar_size + 3],
                outline=(*ring_color, 200), width=4
            )
            
            # Rank Badge
            badge_r = 18
            badge_x = pfp_x + avatar_size // 2
            badge_y = pfp_y + avatar_size
            draw.ellipse(
                [badge_x - badge_r, badge_y - badge_r, badge_x + badge_r, badge_y + badge_r],
                fill=(*ring_color, 255)
            )
            draw.text((badge_x - 6, badge_y - 12), str(rank), font=bold(18), fill=(0,0,0,255))
            
            # Name
            name_bb = draw.textbbox((0,0), display_name, font=f_name)
            name_w = name_bb[2] - name_bb[0]
            draw.text((x_center - name_w // 2, badge_y + 15), display_name, font=f_name, fill=TEXT_W)
            
            # XP
            xp_str = f"{xp:,} XP"
            xp_bb = draw.textbbox((0,0), xp_str, font=f_xp)
            xp_w = xp_bb[2] - xp_bb[0]
            draw.text((x_center - xp_w // 2, badge_y + 45), xp_str, font=f_xp, fill=TEXT_M)
            
        # ── Rank 4-10 (List Below) ────────────────────────────────────────────
        rest = top_users[3:]
        if rest:
            list_start_y = y_top3 + 200
            
            # Create a 2-column layout for remaining users
            col1_x = PX1 + 80
            col2_x = W // 2 + 40
            
            row_y = list_start_y
            
            for i, user_data in enumerate(rest):
                rank = i + 4
                user_id = user_data["user_id"]
                xp = user_data["xp"]
                
                member = guild.get_member(user_id)
                display_name = member.display_name if member else f"User {user_id}"
                
                x_pos = col1_x if i % 2 == 0 else col2_x
                if i > 0 and i % 2 == 0:
                    row_y += 50
                    
                # Rank
                draw.text((x_pos, row_y), f"#{rank}", font=f_rank, fill=(*GOLD, 200))
                
                # Name
                draw.text((x_pos + 60, row_y + 4), display_name, font=f_name, fill=TEXT_W)
                
                # XP
                xp_str = f"{xp:,} XP"
                draw.text((x_pos + 300, row_y + 6), xp_str, font=f_xp, fill=TEXT_M)
                
                # Divider line
                draw.line(
                    [(x_pos, row_y + 40), (x_pos + 380, row_y + 40)],
                    fill=(255, 255, 255, 30), width=1
                )

        # ── Save ──────────────────────────────────────────────────────────────
        fp = io.BytesIO()
        canvas.save(fp, format="PNG")
        fp.seek(0)
        return discord.File(fp, filename="leaderboard.png")

async def setup(bot: commands.Bot):
    await bot.add_cog(Leaderboard(bot))
