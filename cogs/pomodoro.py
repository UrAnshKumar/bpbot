import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import time

class PomodoroTimer:
    def __init__(self, guild_id, voice_channel, text_channel, focus_length, break_length, name="Focus Session"):
        self.guild_id = guild_id
        self.voice_channel = voice_channel
        self.text_channel = text_channel
        self.focus_length = focus_length  # in minutes
        self.break_length = break_length  # in minutes
        self.name = name
        
        self.state = "idle"  # idle, focus, break
        self.time_left = 0   # in seconds
        self.current_cycle = 1
        self.task = None
        self.status_message = None
        
        # Presence check
        self.present_members = set()  # user IDs who marked present in current cycle
        self.missed_cycles = {}       # user ID -> count of missed present checks
        self.inactivity_threshold = 3 # kick after 3 missed checks
        self.voice_alerts = True
        self.original_channel_name = voice_channel.name

class PomodoroCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_timers = {} # voice_channel_id -> PomodoroTimer

    # --- Interactive Views ---
    class PomodoroView(discord.ui.View):
        def __init__(self, timer, cog):
            super().__init__(timeout=None)
            self.timer = timer
            self.cog = cog
            
        @discord.ui.button(label="Start", style=discord.ButtonStyle.success, emoji="▶")
        async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            # Check permissions (manager role or admin or owner)
            if self.timer.state != "idle":
                await interaction.response.send_message("Timer is already running!", ephemeral=True)
                return
                
            self.timer.state = "focus"
            self.timer.time_left = self.timer.focus_length * 60
            self.timer.present_members.clear()
            
            # Start background loop
            self.timer.task = asyncio.create_task(self.cog.run_timer(self.timer))
            
            await interaction.response.defer()
            await self.cog.update_status_card(self.timer)
            
        @discord.ui.button(label="Present", style=discord.ButtonStyle.primary, emoji="✅")
        async def present_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            # Check if user is in the voice channel
            if interaction.user not in self.timer.voice_channel.members:
                await interaction.response.send_message("You must be in the Pomodoro voice channel to check in!", ephemeral=True)
                return
                
            user_id = interaction.user.id
            self.timer.present_members.add(user_id)
            self.timer.missed_cycles[user_id] = 0 # reset missed count
            
            await interaction.response.send_message(f"Thanks {interaction.user.display_name}, you are marked **Present**! 🎯", ephemeral=True)

        @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, emoji="⏹")
        async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            if self.timer.state == "idle":
                await interaction.response.send_message("Timer is not running!", ephemeral=True)
                return
                
            # Cancel task
            if self.timer.task:
                self.timer.task.cancel()
                self.timer.task = None
                
            self.timer.state = "idle"
            self.timer.time_left = 0
            
            # Restore channel name
            try:
                await self.timer.voice_channel.edit(name=self.timer.original_channel_name)
            except Exception:
                pass
                
            await interaction.response.defer()
            await self.cog.update_status_card(self.timer)

    # --- Ticker Loop ---
    async def run_timer(self, timer):
        try:
            # Voice Alerts announce start
            if timer.voice_alerts:
                await self.play_alert(timer.voice_channel, "focus_start")
                
            # Initial name update
            await self.update_channel_title(timer)
            
            last_channel_update = 0
            
            while timer.state != "idle":
                await asyncio.sleep(1)
                timer.time_left -= 1
                
                # Check transitions
                if timer.time_left <= 0:
                    if timer.state == "focus":
                        # Kick AFK members who didn't press Present
                        await self.check_afk_and_kick(timer)
                        
                        # Transition to Break
                        timer.state = "break"
                        timer.time_left = timer.break_length * 60
                        timer.present_members.clear()
                        
                        if timer.voice_alerts:
                            await self.play_alert(timer.voice_channel, "break_start")
                    else:
                        # Transition to Focus
                        timer.state = "focus"
                        timer.time_left = timer.focus_length * 60
                        timer.current_cycle += 1
                        timer.present_members.clear()
                        
                        if timer.voice_alerts:
                            await self.play_alert(timer.voice_channel, "focus_start")
                            
                    await self.update_status_card(timer)
                    await self.update_channel_title(timer)
                    
                # Update channel name/card occasionally (every 30 seconds to respect rate limits)
                if timer.time_left % 30 == 0:
                    await self.update_status_card(timer)
                    # Limit channel edits to once every 2 minutes due to severe Discord rate limits
                    now = time.time()
                    if now - last_channel_update > 120:
                        await self.update_channel_title(timer)
                        last_channel_update = now
                        
        except asyncio.CancelledError:
            pass

    async def update_status_card(self, timer):
        embed = discord.Embed(
            title=f"⏱️ Pomodoro: {timer.name}",
            color=discord.Color.red() if timer.state == "focus" else discord.Color.green() if timer.state == "break" else discord.Color.light_grey()
        )
        
        minutes = timer.time_left // 60
        seconds = timer.time_left % 60
        time_str = f"{minutes:02d}:{seconds:02d}"
        
        if timer.state == "focus":
            embed.description = f"🎯 **Focus Period (Cycle #{timer.current_cycle})**\nKeep working, stay off distractions!\n\n⏱️ Time remaining: **{time_str}**"
            embed.set_footer(text="Be sure to click 'Present' to confirm you are active!")
        elif timer.state == "break":
            embed.description = f"☕ **Break Period**\nRelax, stand up, stretch!\n\n⏱️ Time remaining: **{time_str}**"
            embed.set_footer(text="Next focus cycle starts automatically.")
        else:
            embed.description = "💤 **Timer is currently IDLE.**\nPress 'Start' to begin the Pomodoro sessions."
            embed.set_footer(text="Default cycle: 25m Focus / 5m Break")
            
        view = self.PomodoroView(timer, self)
        
        try:
            if timer.status_message:
                await timer.status_message.edit(embed=embed, view=view)
            else:
                timer.status_message = await timer.text_channel.send(embed=embed, view=view)
        except Exception:
            pass

    async def update_channel_title(self, timer):
        try:
            if timer.state == "focus":
                mins = timer.time_left // 60
                await timer.voice_channel.edit(name=f"🎯 Focus | {mins}m remaining")
            elif timer.state == "break":
                mins = timer.time_left // 60
                await timer.voice_channel.edit(name=f"☕ Break | {mins}m remaining")
            else:
                await timer.voice_channel.edit(name=timer.original_channel_name)
        except Exception:
            pass # rate limit or permissions

    async def check_afk_and_kick(self, timer):
        # Examine users currently in voice
        for member in timer.voice_channel.members:
            if member.bot:
                continue
            user_id = member.id
            if user_id not in timer.present_members:
                # Increment missed check count
                timer.missed_cycles[user_id] = timer.missed_cycles.get(user_id, 0) + 1
                if timer.missed_cycles[user_id] >= timer.inactivity_threshold:
                    try:
                        # Kick from voice
                        await member.move_to(None, reason="AFK during Pomodoro Focus cycle.")
                        # Send DM
                        embed = discord.Embed(
                            title="🛏️ Disconnected due to Inactivity",
                            description="You were disconnected from the Pomodoro voice channel because you missed multiple 'Present' checks. Remember to confirm you are active!",
                            color=discord.Color.orange()
                        )
                        await member.send(embed=embed)
                    except Exception:
                        pass # forbidden / hierarchy
            else:
                # reset
                timer.missed_cycles[user_id] = 0

    async def play_alert(self, voice_channel, alert_type):
        # We announce alerts in the voice text channel (text-in-voice) as a robust mechanism.
        # This keeps the bot's operation clean even if FFmpeg is missing.
        embed = discord.Embed(color=discord.Color.blurple())
        if alert_type == "focus_start":
            embed.title = "🎯 Focus Time Has Started!"
            embed.description = "Quiet down and begin studying. Good luck!"
        elif alert_type == "break_start":
            embed.title = "☕ Break Time Has Started!"
            embed.description = "Step away from your screen, stretch, and grab a drink!"
            
        try:
            await voice_channel.send(embed=embed, tts=True) # uses TTS so users hear the alert read out loud!
        except Exception:
            pass

    # --- Slash Commands ---
    @app_commands.command(name="pomodoro", description="Create a Pomodoro timer in your voice channel.")
    @app_commands.describe(
        focus_length="Length of focus period in minutes (default 25)",
        break_length="Length of break period in minutes (default 5)",
        name="Name of focus timer"
    )
    async def pomodoro(self, interaction: discord.Interaction, focus_length: int = 25, break_length: int = 5, name: str = "Focus Session"):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server!", ephemeral=True)
            return
            
        member = interaction.user
        voice_state = member.voice
        
        if not voice_state or not voice_state.channel:
            await interaction.response.send_message("❌ You must join a voice channel first to attach the Pomodoro timer!", ephemeral=True)
            return
            
        vc = voice_state.channel
        
        if vc.id in self.active_timers:
            await interaction.response.send_message("❌ A Pomodoro timer is already attached to this voice channel!", ephemeral=True)
            return
            
        timer = PomodoroTimer(
            guild_id=interaction.guild_id,
            voice_channel=vc,
            text_channel=interaction.channel,
            focus_length=focus_length,
            break_length=break_length,
            name=name
        )
        
        self.active_timers[vc.id] = timer
        
        # Send creation embed
        embed = discord.Embed(
            title="⏱️ Pomodoro Timer Created",
            description=f"Attached timer **{name}** ({focus_length}m Focus / {break_length}m Break) to voice channel **{vc.name}**.\nClick **Start** below to begin.",
            color=discord.Color.blurple()
        )
        
        await interaction.response.send_message(embed=embed)
        # Post the actual live status card
        await self.update_status_card(timer)

    @app_commands.command(name="timers", description="List all active Pomodoro timers in the server.")
    async def timers(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server!", ephemeral=True)
            return
            
        embed = discord.Embed(title="⏱️ Active Pomodoro Timers", color=discord.Color.blue())
        
        lines = []
        for vc_id, timer in self.active_timers.items():
            if timer.guild_id == interaction.guild_id:
                status = "RUNNING" if timer.state != "idle" else "IDLE"
                lines.append(f"• **{timer.name}** in channel <#{vc_id}> | Status: `{status}` ({timer.focus_length}/{timer.break_length})")
                
        if not lines:
            embed.description = "There are no active Pomodoro timers in this server."
        else:
            embed.description = "\n".join(lines)
            
        await interaction.response.send_message(embed=embed)

async def setup(bot):
    await bot.add_cog(PomodoroCog(bot))
