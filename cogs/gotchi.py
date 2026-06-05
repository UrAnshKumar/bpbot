import discord
from discord.ext import commands
from discord import app_commands
import database

class GotchiCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # --- Interactive Gotchi Status view ---
    class GotchiView(discord.ui.View):
        def __init__(self, owner_id, cog):
            super().__init__(timeout=None)
            self.owner_id = owner_id
            self.cog = cog

        async def check_user(self, interaction: discord.Interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message("❌ This is not your pet!", ephemeral=True)
                return False
            return True

        async def perform_action(self, interaction: discord.Interaction, item_name, coin_cost, hunger_change, happiness_change, health_change, action_verb):
            user_id = interaction.user.id
            
            # Check inventory for the item
            inventory = database.get_inventory(user_id)
            item_found = None
            for item in inventory:
                if item['item_name'] == item_name and item['quantity'] > 0:
                    item_found = item
                    break
                    
            conn = database.get_db_connection()
            
            if item_found:
                # Use item
                conn.execute(
                    "UPDATE inventory SET quantity = quantity - 1 WHERE discord_id = ? AND item_id = ?", 
                    (user_id, item_found['id'])
                )
                conn.commit()
                conn.close()
                msg = f"🎒 Used **{item_name}** from your inventory to {action_verb} your pet!"
            else:
                conn.close()
                # Try charging coins
                success = database.deduct_coins(user_id, coin_cost)
                if not success:
                    await interaction.response.send_message(
                        f"❌ You don't have any **{item_name}** in your inventory, and you don't have **{coin_cost} 🪙** to pay directly!", 
                        ephemeral=True
                    )
                    return
                msg = f"🪙 Charged **{coin_cost} 🪙** directly to {action_verb} your pet!"
                
            # Apply changes to pet
            status = database.update_gotchi_status(
                user_id, 
                hunger_delta=hunger_change, 
                happiness_delta=happiness_change, 
                health_delta=health_change, 
                xp_gain=50
            )
            
            # Formulate response
            status_text = f"✨ Simba level progress: +50 XP!"
            if status['level_up']:
                status_text += f"\n🎉 **Your pet leveled up to Level {status['level']}!** Earning multiplier boosted!"
                
            embed = discord.Embed(
                title="🦁 Pet Interaction Success",
                description=f"{msg}\n\n{status_text}",
                color=discord.Color.green()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
            # Re-render gotchi board
            await self.cog.send_gotchi_panel(interaction, edit=True)

        @discord.ui.button(label="Feed", style=discord.ButtonStyle.success, emoji="🍖")
        async def feed_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            if not await self.check_user(interaction): return
            await self.perform_action(
                interaction, 
                item_name="Organic Meat (Gotchi Food)", 
                coin_cost=25, 
                hunger_change=25, 
                happiness_change=5, 
                health_change=0, 
                action_verb="feed"
            )

        @discord.ui.button(label="Bathe", style=discord.ButtonStyle.primary, emoji="🧼")
        async def bathe_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            if not await self.check_user(interaction): return
            await self.perform_action(
                interaction, 
                item_name="Premium Shampoo (Gotchi Bath)", 
                coin_cost=25, 
                hunger_change=0, 
                happiness_change=5, 
                health_change=25, 
                action_verb="bathe"
            )

        @discord.ui.button(label="Play", style=discord.ButtonStyle.warning, emoji="🎾")
        async def play_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            if not await self.check_user(interaction): return
            await self.perform_action(
                interaction, 
                item_name="Energy Drink (Gotchi Play)", 
                coin_cost=25, 
                hunger_change=-5, 
                happiness_change=25, 
                health_change=0, 
                action_verb="play with"
            )

    async def send_gotchi_panel(self, interaction: discord.Interaction, edit=False):
        user_id = interaction.user.id if hasattr(interaction, "user") else interaction.author.id
        gotchi = database.get_gotchi(user_id)
        
        if not gotchi:
            msg = "❌ You don't have a LionGotchi pet yet! Adopt one with `/gotchi-adopt [name]`."
            if edit:
                await interaction.message.edit(content=msg, embed=None, view=None)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
            return
            
        embed = discord.Embed(
            title=f"🦁 {gotchi['name']} Status (Level {gotchi['level']})",
            color=discord.Color.orange(),
            description=f"Level progress: {gotchi['xp'] % 200}/200 XP"
        )
        
        # Stats gauges
        def build_gauge(value):
            blocks = int(value // 10)
            return "🟩" * blocks + "⬜" * (10 - blocks) + f" {value}%"
            
        embed.add_field(name="🍔 Hunger (Fullness)", value=build_gauge(gotchi['hunger']), inline=False)
        embed.add_field(name="❤️ Health & Hygiene", value=build_gauge(gotchi['health']), inline=False)
        embed.add_field(name="🎾 Happiness", value=build_gauge(gotchi['happiness']), inline=False)
        
        view = self.GotchiView(user_id, self)
        
        if edit:
            await interaction.message.edit(embed=embed, view=view)
        else:
            await interaction.response.send_message(embed=embed, view=view)

    # --- Commands ---
    @app_commands.command(name="gotchi-adopt", description="Adopt a new virtual pet LionGotchi.")
    @app_commands.describe(name="The name you want to give to your pet")
    async def gotchi_adopt(self, interaction: discord.Interaction, name: str):
        user_id = interaction.user.id
        gotchi = database.get_gotchi(user_id)
        
        if gotchi:
            await interaction.response.send_message(f"❌ You already have a pet named **{gotchi['name']}**!", ephemeral=True)
            return
            
        database.adopt_gotchi(user_id, name)
        
        embed = discord.Embed(
            title="🦁 Adoption Successful!",
            description=f"Congratulations! You adopted **{name}**, your virtual LionGotchi! Care for it using `/gotchi`.",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="gotchi", description="View status and care panel for your virtual pet.")
    async def gotchi_status(self, interaction: discord.Interaction):
        await self.send_gotchi_panel(interaction)

async def setup(bot):
    await bot.add_cog(GotchiCog(bot))
