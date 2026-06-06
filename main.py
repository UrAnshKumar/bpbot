import os
import logging
import discord
from discord.ext import commands
from dotenv import load_dotenv
import database

# Configure basic logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("main")

# Load environment variables
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# Initialize database
database.init_db()

class WelcomeBot(commands.Bot):
    def __init__(self):
        # We request all intents as welcomer depends on on_member_join which is part of the members intent.
        intents = discord.Intents.all()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # Load cogs
        logger.info("Loading Welcomer Cog...")
        await self.load_extension("cogs.welcomer")
        logger.info("Loading Moderation Cog...")
        await self.load_extension("cogs.moderation")
        logger.info("Loading Pomodoro Cog...")
        await self.load_extension("cogs.pomodoro")
        logger.info("Loading Leaderboard Cog...")
        await self.load_extension("cogs.leaderboard")
        logger.info("Loading Todo Cog...")
        await self.load_extension("cogs.todo")
        logger.info("Loading Economy Cog...")
        await self.load_extension("cogs.economy")

    async def on_ready(self):
        logger.info(f"Bot logged in as {self.user.name}#{self.user.discriminator} (ID: {self.user.id})")
        logger.info("Syncing slash commands globally...")
        try:
            synced = await self.tree.sync()
            logger.info(f"Successfully synced {len(synced)} slash command(s) globally.")
        except Exception as e:
            logger.error(f"Error syncing slash commands: {e}")

def main():
    if not TOKEN or TOKEN == "your_bot_token_here":
        logger.error("Error: DISCORD_TOKEN is not configured in .env file.")
        print("Please enter a valid DISCORD_TOKEN inside the .env file before starting the bot.")
        return

    bot = WelcomeBot()
    bot.run(TOKEN)

if __name__ == "__main__":
    main()
