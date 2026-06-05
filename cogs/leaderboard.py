import discord
from discord.ext import commands
from discord import app_commands
import database

class LeaderboardCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="leaderboard", description="View the server leaderboard rankings.")
    @app_commands.choices(metric=[
        app_commands.Choice(name="XP / Level", value="xp"),
        app_commands.Choice(name="Voice Study Time", value="voice"),
        app_commands.Choice(name="Coins (Wealth)", value="coins")
    ])
    async def leaderboard(self, interaction: discord.Interaction, metric: app_commands.Choice[str]):
        metric_value = metric.value
        metric_name = metric.name
        
        # Fetch leaderboard from DB
        leaders = database.get_leaderboard(stat_type=metric_value, limit=10)
        
        if not leaders:
            await interaction.response.send_message("No activity recorded on the leaderboard yet!", ephemeral=True)
            return
            
        embed = discord.Embed(
            title=f"🏆 Server Leaderboard: Top Members ({metric_name})",
            color=discord.Color.gold(),
            description="The most productive students on the server!"
        )
        
        description_lines = []
        for index, row in enumerate(leaders):
            user_id = row['discord_id']
            score = row['score']
            level = row['level']
            
            # Fetch user name
            user = self.bot.get_user(user_id)
            if not user:
                try:
                    user = await self.bot.fetch_user(user_id)
                except Exception:
                    user = None
                    
            username = user.display_name if user else f"User ID {user_id}"
            
            # Formatting scores
            if metric_value == "voice":
                hours = score // 60
                mins = score % 60
                score_str = f"{hours}h {mins}m"
            elif metric_value == "coins":
                score_str = f"{score} 🪙"
            else:
                score_str = f"{score} XP (Lvl {level})"
                
            medal = "🥇" if index == 0 else "🥈" if index == 1 else "🥉" if index == 2 else f"#{index + 1}"
            description_lines.append(f"{medal} **{username}** — {score_str}")
            
        embed.description = "\n".join(description_lines)
        await interaction.response.send_message(embed=embed)

async def setup(bot):
    await bot.add_cog(LeaderboardCog(bot))
