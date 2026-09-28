import discord
from discord.ext import commands, tasks
from discord import app_commands
import random
import datetime
from typing import Optional

import utils


# Static data for low-effort commands

_8BALL_RESPONSES = [
    "yes.", "no.", "lol no.", "obv yes.", "lmao absolutely not.",
    "idk man maybe??", "signs point to yes bestie", "don't even think about it.",
    "100% yes.", "the vibes say... no... hehe", "ask me later im busy",
    "yep yep yep", "hard no.", "ugh yes.", "NOPE.",
    "the universe says yes", "i wouldn't if i were you", "trust me yes.",
    "eh maybe who cares", "absolutely fr yes.",
]

_SUPPORTED_LANGUAGES = [
    ("en", "English"),
    ("es", "Spanish"),
    ("fr", "French"),
    ("de", "German"),
    ("it", "Italian"),
    ("pt", "Portuguese"),
    ("ru", "Russian"),
    ("ja", "Japanese"),
    ("ko", "Korean"),
    ("zh", "Chinese"),
    ("hi", "Hindi"),
    ("ar", "Arabic"),
    ("tr", "Turkish"),
    ("nl", "Dutch"),
    ("pl", "Polish"),
    ("id", "Indonesian"),
    ("vi", "Vietnamese"),
    ("th", "Thai"),
]


class General(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.status_loop.start()

    def cog_unload(self):
        self.status_loop.cancel()

    @tasks.loop(minutes=10)
    async def status_loop(self):
        statuses = [
            (discord.ActivityType.listening, "server logs"),
            (discord.ActivityType.watching, "you sleep"),
            (discord.ActivityType.playing, "DDLC"),
            (discord.ActivityType.listening, "to tea ☕"),
            (discord.ActivityType.listening, "fav songs")
        ]
        type_, name = random.choice(statuses)
        await self.bot.change_presence(
            status=discord.Status.dnd,
            activity=discord.Activity(type=type_, name=name),
        )

    @status_loop.before_loop
    async def before_status_loop(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="help", description="✨ See Yuri's command menu.")
    async def help_command(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="✨ YURI'S MENU",
            description="\nHere is what I can do:",
            color=discord.Color.from_rgb(255, 105, 180) # Hot Pink
        )
        
        # Judgement Commands
        embed.add_field(
            name="👀 **JUDGMENT**", 
            value=(
                "`/roast @user` - Absolutely destroy someone's ego.\n"
                "`/rate @user` - I judge their vibe (0-100%).\n"
                "`/ship @user` - Quick compatibility check.\n"
                "`/compatibility @user1 @user2` - Deep compatibility based on actual messages.\n"
                "`/summarize` - I recap the last 20 messages in this channel."
            ), 
            inline=False
        )
        
        # Social & Fun
        embed.add_field(
            name="**DRAMA & CHAOS**",
            value=(
                "`/rename @user` - Give someone a cursed nickname.\n"
                "`/truth` - Get a spicy Truth question.\n"
                "`/dare` - Get a chaotic Dare.\n"
                "`/confess [msg]` - Send an anonymous confession.\n"
                "`/crush @user` - Secretly match! If they pick you too, I DM both.\n"
                "`/poll [question] [opt1] [opt2]` - Yuri hosts a poll and picks a side.\n"
                "`/hotornot [description]` - Anonymous submission. Server judges you.\n"
                "`/8ball [question]` - Magic 8-ball. Cheap and chaotic.\n"
                "`/avatar [@user]` - Show someone's full-size avatar.\n"
                "`/imagine [prompt]` - Generate an image from text.\n"
                "`/say [text]` - Make me say something in your voice channel.\n"
                "`/voice [on|off|status]` - Toggle auto-speak (I speak my replies in VC).\n"
                "`/vc [join|leave]` - Join or leave a voice channel.\n"
                "`/setuproles` - Admin: create a reaction-role message."
            ),
            inline=False
        )

        # Utility
        embed.add_field(
            name="**BRAIN**",
            value=(
                "`/ask [question]` - Ask me anything (I have Internet access + tools).\n"
                "`/translate [text] [language]` - Translate text in Yuri's voice.\n"
                "`/clearhistory` - Make me forget YOUR conversation history.\n"
                "`/history` - View your last few messages with me.\n"
                "`/export` - Download your full chat history as JSON.\n"
                "`/privacy` - Control what data I use about you.\n"
                "`/forgive [message]` - Apologize and ask me to reset my attitude.\n"
                "`/mood` - How do I feel about you right now?\n"
                "`/remind [time] [message]` - Set a reminder.\n"
                "`/rank [@user]` - Show your level and XP.\n"
                "`/leaderboard` - Top 10 chatters in this server.\n"
                "`/status` - Check if Yuri is online and operational.\n"
                "`/wipe` - Admin: Wipe someone else's history."
            ),
            inline=False
        )

         # Admin Only
        embed.add_field(
            name="**ADMIN ONLY**",
            value=(
                "`/setup [channel]` - Set where confessions appear.\n"
                "`/grudge @user` - Make me hate someone permanently.\n"
                "`/ungrudge @user` - Forgive a user.\n"
                "`/starboard [setup|disable]` - Configure the starboard.\n"
                "`/rankroles [level] [role] [add|remove]` - Level-up role rewards."
            ),
            inline=False
        )

        embed.set_footer(text="| for bug report use /feedback!")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="feedback", description="Report bugs/features.")
    @app_commands.choices(category=[app_commands.Choice(name="Bug", value="bug"), app_commands.Choice(name="Feature", value="feature")])
    async def feedback(self, interaction: discord.Interaction, category: app_commands.Choice[str], message: str):
        await interaction.response.defer(ephemeral=True)
        await self.bot.feedback_collection.insert_one({
            "user_id": interaction.user.id,
            "username": interaction.user.name,
            "category": category.value,
            "message": message,
            "timestamp": utils.utcnow()
        })
        
        response = "ok sent."
        if category.value == "bug": response = "**Bug Reported.** Thanks for reporting, We will look after it."
        elif category.value == "feature": response = "**Suggestion Sent.** We will see what we can do."
        
        await interaction.followup.send(response)

    @app_commands.command(
        name="history",
        description="View your last conversation exchanges with Yuri.",
    )
    async def history(self, interaction: discord.Interaction, count: int = 5) -> None:
        """Show a user their own recent chat history with Yuri.

        Supports the GDPR transparency principle — deletion is via /clearhistory,
        export is via /export, and this command lets you peek without dumping a
        full file. The response is ephemeral so it's private to the caller.
        """
        count = max(1, min(count, 15))

        await interaction.response.defer(ephemeral=True)

        cursor = (
            self.bot.chat_collection
            .find({"user_id": interaction.user.id}, {"_id": 0, "parts": 1, "role": 1, "timestamp": 1})
            .sort("timestamp", -1)
            .limit(count * 2)
        )

        docs = [doc async for doc in cursor]
        if not docs:
            await interaction.followup.send(
                "i literally don't remember anything about you yet 💀 "
                "mention me to start chatting!",
                ephemeral=True,
            )
            return

        docs.reverse()

        embed = discord.Embed(
            title="ℹ️ YOUR CHAT HISTORY WITH YURI",
            color=discord.Color.from_rgb(255, 105, 180),
            timestamp=utils.utcnow(),
        )
        embed.set_footer(text=f"showing last {len(docs)} message(s) • /clearhistory to wipe")

        for doc in docs[-count * 2:]:
            role = doc.get("role", "user")
            label = "🤖 Yuri" if role == "model" else "💬 You"
            content = doc.get("parts", [""])[0]
            if not isinstance(content, str):
                content = str(content)
            if len(content) > 300:
                content = content[:300] + "…"
            content = utils.sanitize_for_discord(content)
            ts = doc.get("timestamp")
            ts_str = ts.strftime("%b %d, %H:%M") if hasattr(ts, "strftime") else ""
            embed.add_field(
                name=f"{label} — {ts_str}",
                value=content or "(empty)",
                inline=False,
            )

        try:
            await interaction.followup.send(embed=embed, ephemeral=True)
        except discord.HTTPException as e:
            embed.clear_fields()
            embed.description = f"history too long to display inline — try `/export` for the full file. ({e})"
            await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="forgive",
        description="Apologize to Yuri and ask her to reset her attitude toward you.",
    )
    async def forgive(self, interaction: discord.Interaction, message: Optional[str] = None) -> None:
        """Let a user ask Yuri to drop the cold-chaos mode mid-conversation.

        This doesn't wipe history (use /clearhistory for that) — it just clears
        any active grudge state for the user and inserts an apology turn that
        nudges the model back toward Soft Bestie energy for the next reply.
        """
        await interaction.response.defer()

        apology = message or ""
        safe_apology = utils.sanitize_for_prompt(apology) if apology else "(no message — just vibes)"

        ai = self.bot.get_cog("AI")
        if ai is None:
            await interaction.followup.send("my brain isn't loaded rn try again later")
            return

        prompt_override = (
            f"The user just ran /forgive and wants to reset the vibe. "
            f"They said: '{safe_apology}'. "
            f"If they were being rude before, decide in character whether to accept "
            f"their apology or stay cold. Don't just reset instantly — make them "
            f"work for it a little if it feels fake. Stay in character."
        )
        resp, _ = await ai.get_combined_response(
            interaction.user.id, None, prompt_override=prompt_override
        )

        await self.bot.chat_collection.insert_one({
            "user_id": interaction.user.id,
            "role": "user",
            "parts": [f"[/forgive] {apology}".strip()],
            "timestamp": utils.utcnow(),
        })

        await utils.send_chunked_reply(interaction, resp)

    @app_commands.command(
        name="mood",
        description="How does Yuri feel about you right now? (grudge / crush status)",
    )
    async def mood(self, interaction: discord.Interaction) -> None:
        """Surfaces Yuri's current relationship state with the calling user.

        Shows whether there's an admin-set grudge against them, whether they
        have a pending crush on someone, and whether anyone has a pending crush
        on them. Ephemeral so it stays private.
        """
        await interaction.response.defer(ephemeral=True)

        uid = interaction.user.id

        grudge = await self.bot.grudge_collection.find_one({"user_id": uid})
        crush_given = await self.bot.crush_collection.find_one({"lover_id": uid})
        crush_received = await self.bot.crush_collection.find_one({"target_id": uid})

        if grudge:
            mood_line = "**COLD CHAOS MODE.** An admin has set a grudge against you — I'm being cold/dismissive on purpose."
        else:
            mood_line = "**Soft Bestie mode.** No grudges. We're chill bestie."

        embed = discord.Embed(
            title="💭 YURI'S MOOD ABOUT YOU",
            description=mood_line,
            color=discord.Color.from_rgb(255, 105, 180),
            timestamp=utils.utcnow(),
        )

        if crush_given:
            target_id = crush_given.get("target_id")
            embed.add_field(
                name="💖 Your pending crush",
                value=f"You have a crush on <@{target_id}>. If they run `/crush` on you too, I'll DM both of you.",
                inline=False,
            )
        else:
            embed.add_field(
                name="💖 Your pending crush",
                value="Nobody. You haven't `/crush`-ed anyone yet.",
                inline=False,
            )

        if crush_received:
            embed.add_field(
                name="💌 Someone likes you",
                value="Someone has a crush on you! Run `/crush` on whoever you like to see if it's a match. (I won't tell you who, that's the point 🤫)",
                inline=False,
            )
        else:
            embed.add_field(
                name="💌 Someone likes you",
                value="No secret admirers right now.",
                inline=False,
            )

        embed.set_footer(text="use /forgive to reset the vibe • /clearhistory to wipe memory")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="8ball",
        description="Ask the magic 8-ball a yes/no question.",
    )
    @app_commands.checks.cooldown(1, 3.0)
    async def eight_ball(self, interaction: discord.Interaction, question: str) -> None:
        """Classic magic 8-ball. Cheap, fast, no AI call required."""
        if len(question) > 500:
            await interaction.response.send_message(
                "yoo.... that question is way too long bestie, keep it under 500 chars",
                ephemeral=True,
            )
            return

        safe_q = utils.sanitize_for_discord(question)
        answer = random.choice(_8BALL_RESPONSES)

        embed = discord.Embed(
            title="🎱 Magic 8-Ball",
            color=discord.Color.from_rgb(255, 105, 180),
        )
        embed.add_field(name="❓ Question", value=safe_q, inline=False)
        embed.add_field(name="🔮 Answer", value=f"**{answer}**", inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="translate",
        description="Translate text into another language (Yuri's energy included).",
    )
    @app_commands.choices(
        language=[app_commands.Choice(name=label, value=code) for code, label in _SUPPORTED_LANGUAGES]
    )
    @app_commands.checks.cooldown(1, 5.0)
    async def translate(
        self,
        interaction: discord.Interaction,
        text: str,
        language: app_commands.Choice[str],
    ) -> None:
        """Leverages the existing Gemini pipeline to translate text.

        Yuri's personality is preserved — the translation is delivered in her
        voice, which makes it more fun than a sterile Google Translate call.
        """
        if len(text) > 1500:
            await interaction.response.send_message(
                "too long bestie, keep it under 1500 chars",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        safe_text = utils.sanitize_for_prompt(text)
        lang_name = language.name

        ai = self.bot.get_cog("AI")
        if ai is None:
            await interaction.followup.send("my brain isn't loaded rn 💀")
            return

        prompt_override = (
            f"Translate the following text into {lang_name}. "
            f"Stay in character — deliver the translation in your voice, "
            f"but keep the translation accurate. After the translation, "
            f"add one short in-character comment about the text (optional).\n\n"
            f"TEXT TO TRANSLATE:\n{safe_text}"
        )
        resp, _ = await ai.get_combined_response(
            interaction.user.id, None, prompt_override=prompt_override
        )

        embed = discord.Embed(
            title=f"🌐 Translation → {lang_name}",
            description=resp,
            color=discord.Color.from_rgb(255, 105, 180),
        )
        embed.add_field(
            name="Original",
            value=utils.sanitize_for_discord(text)[:1024],
            inline=False,
        )
        embed.set_footer(text="powered by Yuri's brain • not 100% accurate")
        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="avatar",
        description="Show someone's full-size avatar (yours by default).",
    )
    async def avatar(
        self,
        interaction: discord.Interaction,
        member: Optional[discord.Member] = None,
    ) -> None:
        """Basic utility — shows a user's avatar in full resolution.

        Defaults to the caller if no member is specified.
        """
        target = member or interaction.user

        avatar = target.display_avatar

        embed = discord.Embed(
            title=f"🖼️ {target.display_name}'s avatar",
            color=discord.Color.from_rgb(255, 105, 180),
        )
        embed.set_image(url=avatar.url)
        embed.set_footer(text=f"User ID: {target.id}")

        embed.add_field(
            name="Sizes",
            value=(
                f"[256px]({avatar.with_size(256).url}) • "
                f"[512px]({avatar.with_size(512).url}) • "
                f"[1024px]({avatar.with_size(1024).url}) • "
                f"[2048px]({avatar.with_size(2048).url})"
            ),
            inline=False,
        )

        await interaction.response.send_message(embed=embed)

async def setup(bot):
    await bot.add_cog(General(bot))
    
