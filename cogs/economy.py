import discord
from discord.ext import commands
from discord import app_commands
import database

class EconomyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="coins", description="Check your current coin balance.")
    async def coins(self, interaction: discord.Interaction, member: discord.Member = None):
        target = member or interaction.user
        user_data = database.get_user(target.id)
        
        embed = discord.Embed(
            title="🪙 Coin Balance",
            description=f"{target.mention} has **{user_data['coins']} 🪙** coins.",
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="give", description="Transfer coins to another server member.")
    @app_commands.describe(member="The user to transfer coins to", amount="The quantity of coins to send")
    async def give(self, interaction: discord.Interaction, member: discord.Member, amount: int):
        if amount <= 0:
            await interaction.response.send_message("❌ Amount must be positive!", ephemeral=True)
            return
            
        if member.id == interaction.user.id:
            await interaction.response.send_message("❌ You cannot send coins to yourself!", ephemeral=True)
            return
            
        if member.bot:
            await interaction.response.send_message("❌ You cannot send coins to bots!", ephemeral=True)
            return
            
        success = database.deduct_coins(interaction.user.id, amount)
        if not success:
            await interaction.response.send_message("❌ Insufficient coins balance for this transfer!", ephemeral=True)
            return
            
        database.add_coins(member.id, amount)
        
        embed = discord.Embed(
            title="💸 Coin Transfer Completed",
            description=f"Successfully transferred **{amount} 🪙** to {member.mention}!",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="shop", description="Browse items in the virtual economy shop.")
    async def shop(self, interaction: discord.Interaction):
        items = database.get_shop_items()
        
        embed = discord.Embed(
            title="🛒 Study Bot Virtual Shop",
            description="Use `/buy [item_id]` to purchase items with your coins!",
            color=discord.Color.blue()
        )
        
        roles_list = []
        gotchi_items = []
        
        for item in items:
            line = f"`ID: {item['id']}` **{item['item_name']}** — {item['price']} 🪙"
            if item['item_type'] == 'color_role':
                roles_list.append(line)
            else:
                gotchi_items.append(line)
                
        if roles_list:
            embed.add_field(name="🌈 Custom Roles", value="\n".join(roles_list), inline=False)
        if gotchi_items:
            embed.add_field(name="🦁 Gotchi Supplies", value="\n".join(gotchi_items), inline=False)
            
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="buy", description="Purchase an item from the shop.")
    @app_commands.describe(item_id="The ID of the shop item to purchase (from /shop)")
    async def buy(self, interaction: discord.Interaction, item_id: int):
        success, result = database.buy_item(interaction.user.id, item_id)
        
        if not success:
            await interaction.response.send_message(f"❌ Purchase failed: {result}", ephemeral=True)
            return
            
        item = result
        embed = discord.Embed(
            title="🛍️ Purchase Successful!",
            description=f"You purchased **{item['item_name']}** for {item['price']} 🪙!",
            color=discord.Color.green()
        )
        
        # If the item is a role, try to assign it to the user in the server
        if item['item_type'] == 'color_role':
            role_name = item['item_name'].replace(" Role", "")
            guild = interaction.guild
            
            # Find or create role
            role = discord.utils.get(guild.roles, name=role_name)
            if not role:
                try:
                    # Create the role with a color
                    color = discord.Color.red() if "Red" in role_name else discord.Color.blue() if "Blue" in role_name else discord.Color.green()
                    role = await guild.create_role(name=role_name, color=color, reason="Economy shop item purchase.")
                except discord.Forbidden:
                    embed.add_field(
                        name="⚠️ Role Assignment Failed", 
                        value="I don't have permission to create or manage roles in this server. Please contact an Administrator to assign it manually.",
                        inline=False
                    )
                    
            if role:
                try:
                    await interaction.user.add_roles(role)
                    embed.add_field(name="🌈 Role Assigned", value=f"Added role **{role.name}** to your profile!", inline=False)
                except discord.Forbidden:
                    embed.add_field(
                        name="⚠️ Role Assignment Failed",
                        value=f"I don't have permission to assign the **{role.name}** role to you. (Ensure my bot role is higher than the purchased role!)",
                        inline=False
                    )
                    
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="inventory", description="Check your purchased shop inventory.")
    async def inventory(self, interaction: discord.Interaction):
        inv = database.get_inventory(interaction.user.id)
        
        embed = discord.Embed(
            title=f"🎒 Inventory: {interaction.user.display_name}",
            color=discord.Color.blue()
        )
        
        if not inv:
            embed.description = "Your inventory is currently empty! Buy things in `/shop`."
        else:
            lines = []
            for item in inv:
                lines.append(f"• **{item['item_name']}** (x{item['quantity']})")
            embed.description = "\n".join(lines)
            
        await interaction.response.send_message(embed=embed)

async def setup(bot):
    await bot.add_cog(EconomyCog(bot))
