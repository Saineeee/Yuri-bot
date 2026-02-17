import discord
from discord.ext import commands
from discord import app_commands
import io
import datetime

class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def health(self, ctx):
        start = datetime.datetime.now()
        try:
            await self.bot.mongo.admin.command('ping')
            db_status = "✅ Connected"
        except Exception as e:
            db_status = f"❌ Failed: {e}"

        latency = (datetime.datetime.now() - start).total_seconds() * 1000

        ai_cog = self.bot.get_cog("AI")
        groq_status = "✅ Active" if ai_cog and ai_cog.groq_client else "❌ Inactive"

        msg = (
            f"**🏥 SYSTEM HEALTH**\n"
            f"- **Ping:** {round(self.bot.latency * 1000)}ms\n"
            f"- **Database:** {db_status} ({int(latency)}ms)\n"
            f"- **AI (Groq):** {groq_status}\n"
        )
        await ctx.send(msg)

    @app_commands.command(name="setup", description="Admin: Set confession channel.")
    async def setup(self, interaction: discord.Interaction, channel: discord.TextChannel):
        # 1. Manual Permission Check (Replaces the decorator)
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You have to be the server owner or admin to use this command", ephemeral=True)
            return

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

    @commands.command(name="wipeall")
    @commands.is_owner()
    async def wipe_all(self, ctx):
        await self.bot.chat_collection.delete_many({})
        await ctx.send("⚠️ **SYSTEM PURGE:** I have forgotten EVERYONE. Database cleared.")

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

    @commands.command()
    @commands.is_owner()
    async def spyrecent(self, ctx):
        now = datetime.datetime.utcnow()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        cursor = self.bot.chat_collection.find({"timestamp": {"$gte": start}}).sort("timestamp", 1)
        
        log = f"DAILY LOG: {start.date()}\n" + "="*40 + "\n"
        count = 0
        async for doc in cursor:
            name = doc['user_id'] # Simplified for speed
            msg = str(doc['parts'][0]).replace('\n', ' ')
            log += f"[{doc['timestamp'].strftime('%H:%M')}] {name}: {msg[:50]}\n"
            count += 1
            
        if count == 0: return await ctx.send("❌ No logs today.")
        await ctx.send(f"Found {count} messages.", file=discord.File(io.BytesIO(log.encode()), filename="daily_log.txt"))

    @commands.command()
    @commands.is_owner()
    async def inbox(self, ctx):
        cursor = self.bot.feedback_collection.find({}).sort("timestamp", -1)
        # 2. Updated Format: Includes User ID for copying
        log = "INBOX (Format: [CATEGORY] Name (ID): Message)\n" + "="*50 + "\n"
        count = 0
        async for doc in cursor:
            log += f"[{doc['category'].upper()}] {doc['username']} ({doc['user_id']}): {doc['message']}\n"
            count += 1
        
        if count == 0: return await ctx.send("📭 Empty.")
        await ctx.send(f"📬 {count} items.", file=discord.File(io.BytesIO(log.encode()), filename="inbox.txt"))

    @commands.command(name="reply")
    @commands.is_owner()
    async def reply(self, ctx, user_id: int, *, message: str):
        """Reply to a user's feedback via DM."""
        # 3. New Reply Command
        try:
            target = await self.bot.fetch_user(user_id)
            if not target:
                await ctx.send("❌ User not found.")
                return
            
            embed = discord.Embed(
                title="📬 Response from Developer",
                description=message,
                color=discord.Color.from_rgb(255, 105, 180) # Yuri Pink
            )
            embed.set_footer(text=f"Don't reply to this message, if there's anything else then pls use /feedback once again")
            
            await target.send(embed=embed)
            await ctx.send(f"✅ Reply sent to **{target.name}**!")
            
        except discord.Forbidden:
            await ctx.send("❌ **Failed:** User has DMs disabled.")
        except Exception as e:
            await ctx.send(f"❌ **Error:** {e}")

async def setup(bot):
    await bot.add_cog(Admin(bot))
