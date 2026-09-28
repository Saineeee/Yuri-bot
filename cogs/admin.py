import datetime
import io
import json
import logging

import discord
from discord import app_commands
from discord.ext import commands

import utils

log = logging.getLogger(__name__)


class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # Owner only prefix commands

    @commands.command()
    @commands.is_owner()
    async def health(self, ctx):
        """Owner-only: detailed system health with all internals."""
        start = datetime.datetime.now(datetime.UTC)
        try:
            await self.bot.mongo.admin.command("ping")
            db_status = "✅ Connected"
        except Exception as e:
            db_status = f"❌ Failed: {e}"

        db_latency = (datetime.datetime.now(datetime.UTC) - start).total_seconds() * 1000

        ai_cog = self.bot.get_cog("AI")
        groq_status = "✅ Active" if ai_cog and ai_cog.groq_client else "❌ Inactive"
        gemini_status = "✅ Active" if ai_cog and ai_cog.gemini_client else "❌ Inactive"
        together_status = (
            "✅ Active"
            if ai_cog and getattr(ai_cog, "together_client", None)
            else "➖ Not configured"
        )

        loaded_cogs = [name for name, cog in self.bot.cogs.items() if cog is not None]

        msg = (
            f"**ℹ️ SYSTEM HEALTH (owner-only)**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"**Discord**\n"
            f"- WebSocket ping: **{round(self.bot.latency * 1000)}ms**\n"
            f"- Guilds: **{len(self.bot.guilds)}**\n"
            f"- Loaded cogs ({len(loaded_cogs)}): {', '.join(loaded_cogs)}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"**Database (MongoDB):** {db_status} ({int(db_latency)}ms)\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"**AI Providers**\n"
            f"- Gemini: {gemini_status}\n"
            f"- Groq: {groq_status}\n"
            f"- Together (fine-tuned): {together_status}\n"
        )
        await ctx.send(msg)

    # Public slash command

    @app_commands.command(
        name="status",
        description="Check if Yuri is online and operational.",
    )
    async def status_slash(self, interaction: discord.Interaction) -> None:
        """Public slash command: minimal status check."""
        db_ok = True
        try:
            await self.bot.mongo.admin.command("ping")
        except Exception:
            db_ok = False

        gateway_ok = self.bot.latency != float("inf")

        if db_ok and gateway_ok:
            embed = discord.Embed(
                title="✅ All Systems Operational",
                color=discord.Color.green(),
                timestamp=utils.utcnow(),
            )
            embed.set_footer(text="Yuri Bot • /status")
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            issues = []
            if not gateway_ok:
                issues.append("gateway connection")
            if not db_ok:
                issues.append("database")

            embed = discord.Embed(
                title="⚠️ Degraded Performance",
                description=(
                    "some things aren't working rn" "the dev has been notified, try again in a bit."
                ),
                color=discord.Color.orange(),
                timestamp=utils.utcnow(),
            )
            embed.set_footer(text="Yuri Bot • /status")
            await interaction.response.send_message(embed=embed, ephemeral=True)

            log.warning("status check failed: issues=%s", issues)

    @app_commands.command(name="setup", description="Admin: Set confession channel.")
    async def setup(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "You have to be the server owner or admin to use this command", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        await self.bot.config_collection.update_one(
            {"guild_id": interaction.guild_id},
            {"$set": {"confession_channel_id": channel.id}},
            upsert=True,
        )
        await interaction.followup.send(f"✅ Confessions set to {channel.mention}!")

    @app_commands.command(name="grudge", description="Admin: Banish a user.")
    @app_commands.checks.has_permissions(administrator=True)
    async def grudge(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        await self.bot.grudge_collection.update_one(
            {"user_id": member.id},
            {"$set": {"timestamp": utils.utcnow()}},
            upsert=True,
        )
        await interaction.followup.send(f"✊ **Grudge added.** I now hate {member.display_name}.")

    @app_commands.command(name="ungrudge", description="Admin: Forgive a user.")
    @app_commands.checks.has_permissions(administrator=True)
    async def ungrudge(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        await self.bot.grudge_collection.delete_one({"user_id": member.id})
        await interaction.followup.send("⭐️ **Forgiven.**")

    @app_commands.command(name="wipe", description="Admin: Wipe user memory.")
    async def wipe(self, interaction: discord.Interaction, member: discord.Member):
        if str(interaction.user.id) != str(self.bot.owner_id):
            await interaction.response.send_message("❌ Owner only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self.bot.chat_collection.delete_many({"user_id": member.id})
        await interaction.followup.send(f"✅ Wiped memory for {member.display_name}.")

    @app_commands.command(name="clearhistory", description="Wipe your own chat history with Yuri.")
    async def clearhistory(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        result = await self.bot.chat_collection.delete_many({"user_id": interaction.user.id})
        if result.deleted_count == 0:
            await interaction.followup.send(
                "i literally don't remember anything about you already 💀"
            )
        else:
            await interaction.followup.send("✅ done. who are you again? i forgor 🫠🙏")

    @app_commands.command(
        name="export",
        description="Download your full chat history with Yuri (GDPR data portability).",
    )
    async def export(self, interaction: discord.Interaction):
        """DM the user their full chat history as a JSON file."""
        await interaction.response.defer(ephemeral=True)

        cursor = self.bot.chat_collection.find(
            {"user_id": interaction.user.id}, {"_id": 0, "user_id": 0}
        ).sort("timestamp", 1)

        records = []
        async for doc in cursor:
            ts = doc.get("timestamp")
            if isinstance(ts, datetime.datetime):
                doc["timestamp"] = ts.isoformat()
            records.append(doc)

        if not records:
            await interaction.followup.send(
                "i don't have any of your data stored bestie 💀 nothing to export.",
                ephemeral=True,
            )
            return

        payload = {
            "exported_at": utils.utcnow().isoformat(),
            "user_id": interaction.user.id,
            "record_count": len(records),
            "records": records,
        }
        data = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")

        try:
            await interaction.user.send(
                file=discord.File(
                    io.BytesIO(data),
                    filename=f"yuri_data_export_{interaction.user.id}.json",
                ),
                content=f"⬇️ Here's your data export: {len(records)} records. "
                f"Use `/clearhistory` if you want to delete it all.",
            )
            await interaction.followup.send(
                "✅ check your DMs, i sent your data export there.", ephemeral=True
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ i can't DM you. please enable DMs from server members and try again.",
                ephemeral=True,
            )

    @app_commands.command(
        name="privacy",
        description="Control what data Yuri uses about you (presence, dossier, etc).",
    )
    @app_commands.choices(
        setting=[
            app_commands.Choice(name="Presence: Allow (default)", value="presence_allow"),
            app_commands.Choice(name="Presence: Hide", value="presence_hide"),
        ]
    )
    async def privacy(
        self,
        interaction: discord.Interaction,
        setting: app_commands.Choice[str],
    ):
        """Toggle whether presence data is included in the user's dossier."""
        await interaction.response.defer(ephemeral=True)

        include_presence = setting.value == "presence_allow"
        await self.bot.privacy_collection.update_one(
            {"user_id": interaction.user.id},
            {
                "$set": {
                    "include_presence": include_presence,
                    "updated_at": utils.utcnow(),
                }
            },
            upsert=True,
        )

        if include_presence:
            msg = "✅ Presence data is now **shared** with the AI when roasting / rating you (default)."
        else:
            msg = (
                "ℹ️ Presence data is now **hidden**. /roast, /rate, /ship, /compatibility "
                "will not include your Spotify songs, games, or custom status."
            )

        await interaction.followup.send(msg, ephemeral=True)

    @commands.command(name="stats")
    @commands.is_owner()
    async def stats(self, ctx):
        """Owner only: shows bot stats across all servers."""
        total_messages = await self.bot.chat_collection.count_documents({})
        total_users = len(await self.bot.chat_collection.distinct("user_id"))
        total_servers = len(self.bot.guilds)
        total_grudges = await self.bot.grudge_collection.count_documents({})
        total_crushes = await self.bot.crush_collection.count_documents({})
        total_feedback = await self.bot.feedback_collection.count_documents({})

        # Today's activity
        today_start = utils.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        todays_messages = await self.bot.chat_collection.count_documents(
            {"timestamp": {"$gte": today_start}}
        )

        msg = (
            f"**ℹ️ YURI STATS**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"↔️ **Servers:** {total_servers}\n"
            f"↔️ **Unique Users:** {total_users}\n"
            f"↔️ **Total Messages:** {total_messages:,}\n"
            f"↔️ **Messages Today:** {todays_messages:,}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"✊ **Active Grudges:** {total_grudges}\n"
            f"♥️ **Pending Crushes:** {total_crushes}\n"
            f"📬 **Feedback Items:** {total_feedback}\n"
        )
        await ctx.send(msg)

    @commands.command(name="wipeall")
    @commands.is_owner()
    async def wipe_all(self, ctx):
        """Owner only: wipe ALL chat history. Requires '!wipeall confirm'."""
        if not ctx.message.content.strip().lower().endswith("confirm"):
            await ctx.send(
                "⚠️ **SYSTEM PURGE REQUESTED.**\n"
                "This will permanently delete **EVERY user's** chat history "
                "across **ALL servers**.\n\n"
                "To confirm, run: `!wipeall confirm`."
            )
            return

        await self.bot.chat_collection.delete_many({})
        log.warning("SYSTEM PURGE: chat_history collection wiped by owner %s.", ctx.author.id)
        await ctx.send("⚠️ **SYSTEM PURGE:** I have forgotten EVERYONE. Db cleared.")

    @commands.command(name="usercount")
    @commands.is_owner()
    async def user_count(self, ctx):
        """Owner only: Shows how many unique users are in the database."""
        users = await self.bot.chat_collection.distinct("user_id")
        await ctx.send(f"ℹ️ Database contains context data for **{len(users)}** users.")

    @commands.command(name="fetchlog")
    @commands.is_owner()
    async def fetch_log(self, ctx, user_id: int):
        """Owner only: Fetches a user's chat history for debugging context."""
        if ctx.guild is not None:
            await ctx.send("ℹ️ This command is DM-only. DM me instead to avoid leaking data.")
            return

        cursor = self.bot.chat_collection.find({"user_id": user_id}).sort("timestamp", 1)
        log_text = ""
        async for doc in cursor:
            role = "YURI" if doc["role"] == "model" else "USER"
            log_text += f"[{doc['timestamp']}] {role}: {doc['parts'][0]}\n"

        if not log_text:
            await ctx.send("No Data.")
            return

        await ctx.send(
            file=discord.File(
                io.BytesIO(log_text.encode()),
                filename=f"debug_log_{user_id}.txt",
            )
        )

    @commands.command(name="dailylog")
    @commands.is_owner()
    async def daily_log(self, ctx):
        """Owner only: Shows recent bot interactions to monitor for errors or usage spikes."""
        if ctx.guild is not None:
            await ctx.send("ℹ️ This command is DM-only. DM me instead to avoid leaking data.")
            return

        now = utils.utcnow()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        cursor = self.bot.chat_collection.find({"timestamp": {"$gte": start}}).sort("timestamp", 1)

        log_text = f"DAILY DIAGNOSTIC LOG: {start.date()}\n" + "=" * 40 + "\n"
        count = 0
        async for doc in cursor:
            name = doc["user_id"]
            msg = str(doc["parts"][0]).replace("\n", " ")
            log_text += f"[{doc['timestamp'].strftime('%H:%M')}] {name}: {msg[:50]}\n"
            count += 1

        if count == 0:
            await ctx.send("❌ No logs today.")
            return

        await ctx.send(
            f"Found {count} messages.",
            file=discord.File(
                io.BytesIO(log_text.encode()),
                filename="daily_diagnostic_log.txt",
            ),
        )

    @commands.command()
    @commands.is_owner()
    async def inbox(self, ctx):
        """Owner only: Lists all feedback submissions."""
        if ctx.guild is not None:
            await ctx.send("ℹ️ This command is DM-only. DM me instead to avoid leaking feedback.")
            return

        cursor = self.bot.feedback_collection.find({}).sort("timestamp", -1)
        log_text = "INBOX (Format: [CATEGORY] Name (ID): Message)\n" + "=" * 50 + "\n"
        count = 0
        async for doc in cursor:
            log_text += f"[{doc['category'].upper()}] {doc['username']} ({doc['user_id']}): {doc['message']}\n"
            count += 1

        if count == 0:
            await ctx.send("📭 Empty.")
            return

        await ctx.send(
            f"📬 {count} items.",
            file=discord.File(
                io.BytesIO(log_text.encode()),
                filename="inbox.txt",
            ),
        )

    @commands.command(name="reply")
    @commands.is_owner()
    async def reply(self, ctx, user_id: int, *, message: str):
        """Reply to a user's feedback via DM."""
        try:
            target = await self.bot.fetch_user(user_id)
            if not target:
                await ctx.send("❌ User not found.")
                return

            embed = discord.Embed(
                title="📬 Response from Developer",
                description=message,
                color=discord.Color.from_rgb(255, 105, 180),
                timestamp=utils.utcnow(),
            )
            embed.set_footer(
                text="Pls Don't reply to this message, if there's anything else then use /feedback once again"
            )

            await target.send(embed=embed)
            await ctx.send(f"✅ Reply sent to **{target.name}**!")

        except discord.Forbidden:
            await ctx.send("❌ **Failed:** User has DMs disabled.")
        except Exception as e:
            log.error("reply command failed: %s", e)
            await ctx.send(f"❌ **Error:** {e}")


async def setup(bot):
    await bot.add_cog(Admin(bot))
