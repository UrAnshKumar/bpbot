import os
import asyncio
import logging
import discord
from discord.ext import commands
from dotenv import load_dotenv
import database

# Load environment variables
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = os.getenv("DEFAULT_PREFIX", "!")

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("StudyBot")

class StudyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.voice_states = True
        
        super().__init__(command_prefix=PREFIX, intents=intents)
        
    async def setup_hook(self):
        # Initialize Database
        database.init_db()
        logger.info("Database initialized.")
        
        # Load Cogs
        cog_folder = "./cogs"
        if not os.path.exists(cog_folder):
            os.makedirs(cog_folder)
            
        cogs_loaded = []
        for filename in os.listdir(cog_folder):
            if filename.endswith(".py") and not filename.startswith("__"):
                cog_name = f"cogs.{filename[:-3]}"
                try:
                    await self.load_extension(cog_name)
                    cogs_loaded.append(cog_name)
                except Exception as e:
                    logger.error(f"Failed to load extension {cog_name}: {e}")
                    
        logger.info(f"Loaded cogs: {', '.join(cogs_loaded) if cogs_loaded else 'None'}")
        
        # Synchronize slash commands
        logger.info("Synchronizing slash commands...")
        try:
            synced = await self.tree.sync()
            logger.info(f"Successfully synced {len(synced)} slash commands.")
        except Exception as e:
            logger.error(f"Failed to sync slash commands: {e}")

    async def on_ready(self):
        logger.info(f"Logged in as {self.user} (ID: {self.user.id})")
        logger.info("Bot is ready and active!")
        activity = discord.Activity(type=discord.ActivityType.watching, name="you study | /profile")
        await self.change_presence(activity=activity)

async def main():
    if not TOKEN:
        logger.error("No DISCORD_TOKEN found in environmental variables. Please configure your .env file.")
        return
        
    bot = StudyBot()
    
    # Overwrite the default close behavior to ensure clean exit
    async with bot:
        await bot.start(TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down bot...")
