import discord
from discord.ext import commands
from discord import app_commands
import io
import datetime

class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="setup", description="Admin: Set confession channel.")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)
        await self.bot.config_collection.update_one(
            {"guild_id": interaction.guild_id}, 
            {"$set": {"confession_channel_id": channel.id}}, 
            upsert=True
        )
        await interaction.followup.send(f"✅ Confessions set to {channel.mention}!")

    @app_commands.command(name="grudge", description="Admin: Banish a user.")
    @app_commands.checks.has_permissions(administrator=True)
    async def grudge(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        await self.bot.grudge_collection.update_one({"user_id": member.id}, {"$set": {"timestamp": datetime.datetime.utcnow()}}, upsert=True)
        await interaction.followup.send(f"💀 **Grudge added.** I now hate {member.display_name}.")

    @app_commands.command(name="ungrudge", description="Admin: Forgive a user.")
    @app_commands.checks.has_permissions(administrator=True)
    async def ungrudge(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        await self.bot.grudge_collection.delete_one({"user_id": member.id})
        await interaction.followup.send(f"✨ **Forgiven.**")

    @app_commands.command(name="wipe", description="Admin: Wipe user memory.")
    async def wipe(self, interaction: discord.Interaction, member: discord.Member):
        if str(interaction.user.id) != str(self.bot.owner_id):
            await interaction.response.send_message("❌ Owner only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self.bot.chat_collection.delete_many({"user_id": member.id})
        await interaction.followup.send(f"✅ Wiped memory for {member.display_name}.")

    @commands.command()
    @commands.is_owner()
    async def spy(self, ctx):
        users = await self.bot.chat_collection.distinct("user_id")
        await ctx.send(f"🕵️ I have data on **{len(users)}** users.")

    @commands.command()
    @commands.is_owner()
    async def spysee(self, ctx, user_id: int):
        cursor = self.bot.chat_collection.find({"user_id": user_id}).sort("timestamp", 1)
        log = ""
        async for doc in cursor:
            role = "YURI" if doc['role'] == "model" else "USER"
            log += f"[{doc['timestamp']}] {role}: {doc['parts'][0]}\n"
        
        if not log: return await ctx.send("No Data.")
        await ctx.send(file=discord.File(io.BytesIO(log.encode()), filename=f"log_{user_id}.txt"))

async def setup(bot):
    await bot.add_cog(Admin(bot))
