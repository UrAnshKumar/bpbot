import discord
from discord.ext import commands
from discord import app_commands
import database
from datetime import datetime
import time

class ProfileCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Keeps track of user voice sessions: {user_id: {"join_time": float, "camera": bool, "screen": bool}}
        self.voice_sessions = {}
        
    @commands.Cog.listener()
    async def on_message(self, message):
        # Ignore bots and DMs
        if message.author.bot or not message.guild:
            return
            
        # Add 5 XP and 1 coin per message (with cooldown if necessary, here we do simple)
        user_id = message.author.id
        level_up, new_level = database.update_user_activity(
            user_id, 
            xp_gain=5, 
            msg_count=1, 
            coin_gain=1
        )
        
        if level_up:
            try:
                # Notify in the text channel
                embed = discord.Embed(
                    title="🎉 Level Up!",
                    description=f"Congratulations {message.author.mention}, you reached **Level {new_level}**!",
                    color=discord.Color.gold()
                )
                await message.channel.send(embed=embed)
            except discord.Forbidden:
                pass # Can't send message

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return
            
        user_id = member.id
        now = time.time()
        
        # Scenario 1: User joined voice channel
        if before.channel is None and after.channel is not None:
            self.voice_sessions[user_id] = {
                "join_time": now,
                "camera": after.self_video,
                "screen": after.self_stream
            }
            
        # Scenario 2: User left voice channel
        elif before.channel is not None and after.channel is None:
            session = self.voice_sessions.pop(user_id, None)
            if session:
                duration_seconds = now - session["join_time"]
                duration_minutes = int(duration_seconds // 60)
                
                if duration_minutes > 0:
                    # Earning rules:
                    # 1 coin per minute standard
                    # 2 coins per minute if camera or screen share is active
                    is_camera = session["camera"] or before.self_video
                    is_screen = session["screen"] or before.self_stream
                    
                    coin_rate = 2 if (is_camera or is_screen) else 1
                    coins_earned = duration_minutes * coin_rate
                    xp_earned = duration_minutes * 10 # 10 XP per minute
                    
                    camera_mins = duration_minutes if is_camera else 0
                    
                    level_up, new_level = database.update_user_activity(
                        user_id,
                        xp_gain=xp_earned,
                        voice_min=duration_minutes,
                        camera_min=camera_mins,
                        coin_gain=coins_earned
                    )
                    
                    # DM user about earnings
                    try:
                        embed = discord.Embed(
                            title="🎙️ Study Session Summary",
                            color=discord.Color.blue()
                        )
                        embed.add_field(name="Duration", value=f"{duration_minutes} minutes", inline=True)
                        embed.add_field(name="Coins Earned", value=f"{coins_earned} 🪙", inline=True)
                        embed.add_field(name="XP Gained", value=f"{xp_earned} XP", inline=True)
                        if is_camera or is_screen:
                            embed.set_footer(text="Bonus applied for Camera/Screen share! 🌟")
                        
                        await member.send(embed=embed)
                        
                        if level_up:
                            lvl_embed = discord.Embed(
                                title="🎉 Level Up!",
                                description=f"You reached **Level {new_level}**!",
                                color=discord.Color.gold()
                            )
                            await member.send(embed=lvl_embed)
                    except discord.Forbidden:
                        pass # DMs disabled
                        
        # Scenario 3: User changed state within the channel (e.g. toggled camera)
        elif before.channel is not None and after.channel is not None:
            session = self.voice_sessions.get(user_id)
            if session:
                # Update camera/screen state if they turned them on
                if after.self_video:
                    session["camera"] = True
                if after.self_stream:
                    session["screen"] = True

    @app_commands.command(name="profile", description="View your study profile card and stats.")
    async def profile(self, interaction: discord.Interaction, member: discord.Member = None):
        target = member or interaction.user
        user_id = target.id
        
        user_data = database.get_user(user_id)
        gotchi_data = database.get_gotchi(user_id)
        
        embed = discord.Embed(
            title=f"🎓 {target.display_name}'s Study Profile",
            color=discord.Color.purple()
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        
        embed.add_field(name="Level", value=f"Level {user_data['level']}", inline=True)
        embed.add_field(name="XP", value=f"{user_data['xp']} XP", inline=True)
        embed.add_field(name="Coins", value=f"{user_data['coins']} 🪙", inline=True)
        
        # Calculate hours and minutes
        v_hours = user_data['voice_minutes'] // 60
        v_mins = user_data['voice_minutes'] % 60
        embed.add_field(name="Total Study Time", value=f"{v_hours}h {v_mins}m", inline=True)
        embed.add_field(name="Messages Sent", value=str(user_data['message_count']), inline=True)
        
        if gotchi_data:
            embed.add_field(name="Pet LionGotchi", value=f"🦁 {gotchi_data['name']} (Lvl {gotchi_data['level']})", inline=True)
        else:
            embed.add_field(name="Pet LionGotchi", value="None (Adopt with `/gotchi adopt`)", inline=True)
            
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="stats", description="Detailed breakdowns of study activity.")
    async def stats(self, interaction: discord.Interaction, member: discord.Member = None):
        target = member or interaction.user
        user_id = target.id
        user_data = database.get_user(user_id)
        
        embed = discord.Embed(
            title=f"📊 Study Statistics for {target.display_name}",
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        
        embed.add_field(name="Voice Time", value=f"{user_data['voice_minutes']} minutes", inline=False)
        embed.add_field(name="Camera-On Time", value=f"{user_data['camera_minutes']} minutes", inline=False)
        embed.add_field(name="Text Messages", value=f"{user_data['message_count']} messages", inline=False)
        embed.add_field(name="Net Worth", value=f"{user_data['coins']} 🪙", inline=False)
        
        await interaction.response.send_message(embed=embed)

async def setup(bot):
    await bot.add_cog(ProfileCog(bot))
