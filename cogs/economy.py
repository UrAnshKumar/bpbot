import discord
from discord import app_commands
from discord.ext import commands
import logging
import io
import os
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
    GOLD
)

logger = logging.getLogger("economy")

class Economy(commands.Cog):
    """Economy, custom BP Coins, and Ranked Roles configuration cog."""

    # `/add coins [user] [amount]` group command
    add_group = app_commands.Group(name="add", description="Add rewards or currencies.")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.ranked_group = None
        self.rank_group = None

    async def is_moderator(self, interaction: discord.Interaction) -> bool:
        """Helper to verify admin status or registered moderator role."""
        if not interaction.guild:
            return False
        if interaction.user.guild_permissions.administrator:
            return True
        mod_roles = database.get_mod_roles(interaction.guild_id)
        user_role_ids = [role.id for role in interaction.user.roles]
        return any(r_id in mod_roles for r_id in user_role_ids)

    async def get_coin_emoji(self, guild: discord.Guild) -> str:
        """Returns the guild's custom bp_coin emoji or a standard fallback 🪙."""
        if not guild:
            return "🪙"
        
        # Check if already uploaded
        emoji = discord.utils.get(guild.emojis, name="bp_coin")
        if emoji:
            return str(emoji)
            
        # Attempt upload if permissions allow
        if guild.me.guild_permissions.manage_emojis:
            try:
                coin_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "bp_coin.png")
                if os.path.exists(coin_path):
                    with open(coin_path, "rb") as f:
                        image_data = f.read()
                    new_emoji = await guild.create_custom_emoji(name="bp_coin", image=image_data)
                    return str(new_emoji)
            except Exception as e:
                logger.warning(f"Could not auto-create custom bp_coin emoji: {e}")
                
        return "🪙"

    async def cog_load(self):
        """Register config commands dynamically under the Moderation Cog's /config command group."""
        mod_cog = self.bot.get_cog("Moderation")
        if not mod_cog:
            logger.warning("Moderation Cog not found. /config ranked/rank command group registration skipped.")
            return

        # Clean existing to prevent reload conflicts
        try:
            mod_cog.config_group.remove_command("ranked")
        except Exception:
            pass
        try:
            mod_cog.config_group.remove_command("rank")
        except Exception:
            pass

        # 1. Config Ranked Group (/config ranked)
        self.ranked_group = app_commands.Group(name="ranked", description="Configure ranked roles.")

        @self.ranked_group.command(name="roles", description="Configure hours required, role, and BP coin awards.")
        async def config_ranked_roles(interaction: discord.Interaction):
            if not interaction.guild:
                await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
                return
            if not await self.is_moderator(interaction):
                await interaction.response.send_message("❌ You do not have permission to run this command.", ephemeral=True)
                return
            
            await interaction.response.send_modal(RankedRoleModal(self))

        @self.ranked_group.command(name="list", description="List all configured ranked roles.")
        async def list_ranked_roles(interaction: discord.Interaction):
            if not interaction.guild:
                await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
                return
            
            roles = database.get_ranked_roles(interaction.guild_id)
            if not roles:
                await interaction.response.send_message("ℹ️ No ranked roles configured yet.", ephemeral=True)
                return
                
            coin_emoji = await self.get_coin_emoji(interaction.guild)
            lines = []
            for r in roles:
                role_obj = interaction.guild.get_role(r["role_id"])
                role_mention = role_obj.mention if role_obj else f"Unknown Role (ID: {r['role_id']})"
                lines.append(f"⏱️ **{r['hours_required']}h** → {role_mention} (Reward: {coin_emoji} {r['bp_coins_award']})")
                
            await interaction.response.send_message(
                f"🎓 **Configured Ranked Roles:**\n" + "\n".join(lines),
                ephemeral=True
            )

        @self.ranked_group.command(name="remove", description="Remove a ranked role configuration by study hours threshold.")
        @app_commands.describe(hours="The study hours threshold to remove.")
        async def remove_ranked_role(interaction: discord.Interaction, hours: int):
            if not interaction.guild:
                await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
                return
            if not await self.is_moderator(interaction):
                await interaction.response.send_message("❌ You do not have permission to run this command.", ephemeral=True)
                return
            
            deleted = database.remove_ranked_role(interaction.guild_id, hours)
            if deleted:
                await interaction.response.send_message(f"✅ Removed ranked role configuration for **{hours} hours**.", ephemeral=True)
            else:
                await interaction.response.send_message(f"❌ No ranked role configured for **{hours} hours**.", ephemeral=True)

        # 2. Config Rank Announcement Group (/config rank)
        self.rank_group = app_commands.Group(name="rank", description="Configure rank notifications.")

        @self.rank_group.command(name="channel", description="Set the channel where rank announcements are broadcast.")
        @app_commands.describe(channel="The target text channel.")
        async def config_rank_channel(interaction: discord.Interaction, channel: discord.TextChannel):
            if not interaction.guild:
                await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
                return
            if not await self.is_moderator(interaction):
                await interaction.response.send_message("❌ You do not have permission to run this command.", ephemeral=True)
                return
            
            database.set_rank_channel(interaction.guild_id, channel.id)
            await interaction.response.send_message(f"✅ Rank announcement channel set to {channel.mention}.", ephemeral=True)

        # Attach command groups to existing config command group
        mod_cog.config_group.add_command(self.ranked_group)
        mod_cog.config_group.add_command(self.rank_group)
        logger.info("Successfully added /config ranked and /config rank subgroups dynamically.")

    # ─── Economy Slash Commands ───────────────────────────────────────────────

    @app_commands.command(name="economy", description="Check coin balance and study hours.")
    @app_commands.describe(user="The user whose balance to view (optional).")
    async def economy(self, interaction: discord.Interaction, user: discord.Member = None):
        if not interaction.guild:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return

        await interaction.response.defer()
        
        target_user = user or interaction.user
        guild_id = interaction.guild_id
        
        try:
            coins = database.get_user_coins(guild_id, target_user.id)
            xp = database.get_user_xp(guild_id, target_user.id)
            
            card_file = await self.generate_economy_card(target_user, coins, xp)
            
            coin_emoji = await self.get_coin_emoji(interaction.guild)
            hours = xp // 60
            minutes = xp % 60
            
            content = (
                f"💳 **Economy Profile** - {target_user.mention}\n"
                f"⏱️ Study Time: `{hours:02d}h {minutes:02d}m`\n"
                f"🪙 Balance: **{coin_emoji} {coins}** BP Coins"
            )
            
            await interaction.followup.send(content=content, file=card_file)
            
        except Exception as e:
            logger.error(f"Error checking economy: {e}")
            await interaction.followup.send(f"❌ Failed to load economy card: {e}")

    @add_group.command(name="coins", description="Add BP coins to a user's wallet (Admin/Mod only).")
    @app_commands.describe(user="The member receiving coins.", amount="Number of coins to add.")
    async def add_coins_cmd(self, interaction: discord.Interaction, user: discord.Member, amount: int):
        if not interaction.guild:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return
        if not await self.is_moderator(interaction):
            await interaction.response.send_message("❌ You do not have permission to run this command.", ephemeral=True)
            return
        if amount <= 0:
            await interaction.response.send_message("❌ Amount must be positive.", ephemeral=True)
            return
            
        new_balance = database.add_user_coins(interaction.guild_id, user.id, amount)
        coin_emoji = await self.get_coin_emoji(interaction.guild)
        
        await interaction.response.send_message(
            f"✅ Successfully added **{coin_emoji} {amount}** BP Coins to {user.mention}.\n"
            f"New Balance: **{coin_emoji} {new_balance}** BP Coins"
        )

    # ─── Event Listener (Auto-award on Study Done) ───────────────────────────

    @commands.Cog.listener("on_study_xp_added")
    async def on_study_xp_added(self, member: discord.Member, xp_earned: int):
        guild = member.guild
        guild_id = guild.id
        
        # Calculate updated study hours
        total_xp = database.get_user_xp(guild_id, member.id)
        current_hours = total_xp / 60.0
        
        # Fetch ranked roles
        ranked_roles = database.get_ranked_roles(guild_id)
        if not ranked_roles:
            return
            
        for rank in ranked_roles:
            hours_req = rank["hours_required"]
            role_id = rank["role_id"]
            coins_award = rank["bp_coins_award"]
            
            # Check if user reached threshold
            if current_hours >= hours_req:
                # Award if not already granted in DB
                if not database.is_rank_awarded(guild_id, member.id, role_id):
                    role_obj = guild.get_role(role_id)
                    
                    # Grant role on Discord
                    if role_obj:
                        try:
                            await member.add_roles(role_obj, reason=f"Rank threshold reached: {hours_req} hours")
                        except Exception as e:
                            logger.error(f"Failed to add role {role_obj.name} to {member.name}: {e}")
                            
                    # Grant coins in DB
                    database.add_user_coins(guild_id, member.id, coins_award)
                    
                    # Mark rank as claimed
                    database.mark_rank_awarded(guild_id, member.id, role_id)
                    
                    # Send notifications
                    await self.send_rank_up_alerts(member, hours_req, role_obj, coins_award)

    async def send_rank_up_alerts(self, member: discord.Member, hours: int, role: discord.Role, coins_award: int):
        guild = member.guild
        coin_emoji = await self.get_coin_emoji(guild)
        
        role_mention = role.mention if role else "Rank Role"
        role_name = role.name if role else "Rank Role"
        
        coin_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "bp_coin.png")
        
        # Public broadcast text
        public_msg = (
            f"🎉 **RANK UP!** {member.mention} has reached **{hours} hours** of study time!\n"
            f"🎓 Role Awarded: {role_mention}\n"
            f"🪙 Reward: **{coin_emoji} {coins_award}** BP Coins!"
        )
        
        # Private DM text
        private_msg = (
            f"🎉 **Congratulations!** You reached **{hours} hours** of study in **{guild.name}**!\n"
            f"🎓 Role Granted: **{role_name}**\n"
            f"🪙 Reward: **{coin_emoji} {coins_award}** BP Coins added to your wallet!"
        )
        
        # Send to rank channel
        channel_id = database.get_rank_channel(guild.id)
        if channel_id:
            channel = guild.get_channel(channel_id)
            if channel:
                try:
                    file = discord.File(coin_path, filename="bp_coin.png")
                    await channel.send(content=public_msg, file=file)
                except Exception as e:
                    logger.error(f"Failed to send rank announcement to channel: {e}")
                    
        # Send to DM
        try:
            file = discord.File(coin_path, filename="bp_coin.png")
            await member.send(content=private_msg, file=file)
        except Exception as e:
            logger.warning(f"Could not deliver rank-up DM to {member.name}: {e}")

    # ─── Card Generation (1080x1080 Premium Image Card) ──────────────────────

    async def generate_economy_card(self, user: discord.Member, coins: int, xp: int) -> discord.File:
        W, H = 1080, 1080
        
        # 1. Base Gradient Canvas
        canvas = make_gradient_bg(W, H)
        
        # 2. Main Glass Panel
        PX1, PY1, PX2, PY2 = 60, 60, W - 60, H - 60
        canvas = draw_glass_panel(canvas, PX1, PY1, PX2, PY2, radius=24)
        
        draw = ImageDraw.Draw(canvas)
        
        # 3. Fonts
        f_title = bold(54)
        f_sub = regular(24)
        f_label = bold(28)
        f_value = bold(36)
        f_username = bold(42)
        
        # 4. Title Header
        title_text = "ECONOMY PROFILE"
        title_bb = draw.textbbox((0, 0), title_text, font=f_title)
        title_w = title_bb[2] - title_bb[0]
        draw.text((W // 2 - title_w // 2, PY1 + 50), title_text, font=f_title, fill=TEXT_W)
        
        draw.line(
            [(W // 2 - 250, PY1 + 120), (W // 2 + 250, PY1 + 120)],
            fill=(*GOLD, 160), width=3
        )
        
        # 5. Avatar Composition
        avatar = await fetch_avatar(user)
        avatar_circle = crop_circle(avatar, 200)
        pfp_x = W // 2 - 100
        pfp_y = PY1 + 160
        canvas.alpha_composite(avatar_circle, (pfp_x, pfp_y))
        
        # Avatar Golden Ring
        draw.ellipse(
            [pfp_x - 6, pfp_y - 6, pfp_x + 206, pfp_y + 206],
            outline=(*GOLD, 220), width=6
        )
        
        # 6. Names
        username_text = user.display_name
        username_bb = draw.textbbox((0, 0), username_text, font=f_username)
        username_w = username_bb[2] - username_bb[0]
        draw.text((W // 2 - username_w // 2, pfp_y + 230), username_text, font=f_username, fill=TEXT_W)
        
        tag_text = user.name
        tag_bb = draw.textbbox((0, 0), tag_text, font=f_sub)
        tag_w = tag_bb[2] - tag_bb[0]
        draw.text((W // 2 - tag_w // 2, pfp_y + 285), tag_text, font=f_sub, fill=TEXT_M)
        
        # 7. Statistics Panels
        box_y1 = 620
        box_y2 = 820
        box_w = 400
        
        # Study Time Box
        left_x1 = W // 2 - box_w - 20
        left_x2 = W // 2 - 20
        canvas = draw_glass_panel(canvas, left_x1, box_y1, left_x2, box_y2, radius=18)
        
        # Coins Box
        right_x1 = W // 2 + 20
        right_x2 = W // 2 + box_w + 20
        canvas = draw_glass_panel(canvas, right_x1, box_y1, right_x2, box_y2, radius=18)
        
        draw = ImageDraw.Draw(canvas)
        
        # Left Box (Hours) Content
        left_center_x = (left_x1 + left_x2) // 2
        lbl_time = "STUDY TIME"
        lbl_time_bb = draw.textbbox((0, 0), lbl_time, font=f_label)
        lbl_time_w = lbl_time_bb[2] - lbl_time_bb[0]
        draw.text((left_center_x - lbl_time_w // 2, box_y1 + 40), lbl_time, font=f_label, fill=TEXT_M)
        
        hours = xp // 60
        minutes = xp % 60
        time_str = f"{hours}h {minutes}m"
        time_bb = draw.textbbox((0, 0), time_str, font=f_value)
        time_w = time_bb[2] - time_bb[0]
        draw.text((left_center_x - time_w // 2, box_y1 + 110), time_str, font=f_value, fill=TEXT_W)
        
        # Right Box (Coins) Content
        right_center_x = (right_x1 + right_x2) // 2
        lbl_coins = "COIN BALANCE"
        lbl_coins_bb = draw.textbbox((0, 0), lbl_coins, font=f_label)
        lbl_coins_w = lbl_coins_bb[2] - lbl_coins_bb[0]
        draw.text((right_center_x - lbl_coins_w // 2, box_y1 + 40), lbl_coins, font=f_label, fill=TEXT_M)
        
        # BP Coin Image overlay
        coin_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "bp_coin.png")
        coin_size = 54
        coin_img = None
        if os.path.exists(coin_path):
            try:
                coin_img = Image.open(coin_path).convert("RGBA")
                coin_img = coin_img.resize((coin_size, coin_size), Image.Resampling.LANCZOS)
            except Exception as e:
                logger.warning(f"Failed to load coin asset for card: {e}")
                
        val_str = f"{coins}"
        val_bb = draw.textbbox((0, 0), val_str, font=f_value)
        val_w = val_bb[2] - val_bb[0]
        
        if coin_img:
            padding = 12
            total_w = coin_size + padding + val_w
            start_x = right_center_x - total_w // 2
            
            coin_x = start_x
            coin_y = box_y1 + 110 - 6
            canvas.alpha_composite(coin_img, (coin_x, coin_y))
            
            text_x = start_x + coin_size + padding
            draw = ImageDraw.Draw(canvas)
            draw.text((text_x, box_y1 + 110), val_str, font=f_value, fill=GOLD)
        else:
            val_str_fallback = f"🪙 {coins}"
            val_bb_f = draw.textbbox((0, 0), val_str_fallback, font=f_value)
            val_w_f = val_bb_f[2] - val_bb_f[0]
            draw.text((right_center_x - val_w_f // 2, box_y1 + 110), val_str_fallback, font=f_value, fill=GOLD)
            
        # 8. Bottom Footer
        footer_text = "BOOTSTRAP PARADOX STUDY BOT"
        footer_bb = draw.textbbox((0, 0), footer_text, font=f_sub)
        footer_w = footer_bb[2] - footer_bb[0]
        draw.text((W // 2 - footer_w // 2, PY2 - 60), footer_text, font=f_sub, fill=TEXT_M)
        
        # Save to PNG file-like buffer
        fp = io.BytesIO()
        canvas.save(fp, format="PNG")
        fp.seek(0)
        return discord.File(fp, filename="economy_card.png")


# ─── Modals ──────────────────────────────────────────────────────────────────

class RankedRoleModal(discord.ui.Modal, title="Configure Ranked Role"):
    hours_input = discord.ui.TextInput(
        label="Study Hours Required",
        placeholder="e.g. 10",
        required=True
    )
    role_input = discord.ui.TextInput(
        label="Role Name or ID",
        placeholder="e.g. Lv1 Beginner or 123456789",
        required=True
    )
    award_input = discord.ui.TextInput(
        label="BP Coins Reward",
        placeholder="e.g. 500",
        required=True
    )

    def __init__(self, cog: Economy):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        # 1. Parse study hours
        try:
            hours = int(self.hours_input.value.strip())
            if hours < 0:
                raise ValueError()
        except ValueError:
            await interaction.response.send_message("❌ Study hours must be a non-negative integer.", ephemeral=True)
            return

        # 2. Parse reward coins
        try:
            award = int(self.award_input.value.strip())
            if award < 0:
                raise ValueError()
        except ValueError:
            await interaction.response.send_message("❌ Coin reward must be a non-negative integer.", ephemeral=True)
            return

        # 3. Find target Role
        role_name_or_id = self.role_input.value.strip()
        guild = interaction.guild
        role = None
        
        if role_name_or_id.isdigit():
            role = guild.get_role(int(role_name_or_id))
            
        if not role:
            role = discord.utils.find(lambda r: r.name.lower() == role_name_or_id.lower(), guild.roles)
            
        if not role:
            await interaction.response.send_message(
                f"❌ Could not find role '{role_name_or_id}' in the server. Please check the spelling or specify the exact role ID.",
                ephemeral=True
            )
            return

        # Save configuration
        database.add_ranked_role(interaction.guild_id, hours, role.id, award)
        
        coin_emoji = await self.cog.get_coin_emoji(guild)
        await interaction.response.send_message(
            f"✅ **Ranked role configuration saved!**\n"
            f"⏱️ Hours Required: **{hours} hours**\n"
            f"🎓 Role Granted: {role.mention}\n"
            f"🪙 Reward: **{coin_emoji} {award}** BP Coins",
            ephemeral=True
        )


async def setup(bot: commands.Bot):
    cog = Economy(bot)
    await bot.add_cog(cog)
