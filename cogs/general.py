import discord
from discord.ext import commands, tasks
from discord import app_commands
import random
import datetime

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
            (discord.ActivityType.listening, "sarcasm.mp3")
        ]
        type_, name = random.choice(statuses)
        await self.bot.change_presence(status=discord.Status.idle, activity=discord.Activity(type=type_, name=name))

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
        
        # --- JUDGMENT COMMANDS ---
        embed.add_field(
            name="👀 **JUDGMENT**", 
            value=(
                "`/roast @user` - Absolutely destroy someone's ego.\n"
                "`/rate @user` - I judge their vibe (0-100%).\n"
                "`/ship @user` - Check compatibility between two people."
            ), 
            inline=False
        )
        
        # --- SOCIAL & FUN ---
        embed.add_field(
            name="🔥 **DRAMA & CHAOS**", 
            value=(
                "`/rename @user` - Give someone a cursed nickname.\n"
                "`/truth` - Get a spicy Truth question.\n"
                "`/dare` - Get a chaotic Dare.\n"
                "`/confess [msg]` - Send an anonymous confession.\n"
                "`/crush @user` - Secretly match! If they pick you too, I DM both."
            ), 
            inline=False
        )
        
        # --- UTILITY ---
        embed.add_field(
            name="🧠 **BRAIN**", 
            value=(
                "`/ask [question]` - Ask me anything (I have Internet access).\n"
                "`/wipe` - Make me forget our conversation history."
            ), 
            inline=False
        )
        
         # --- ADMIN ONLY ---
        embed.add_field(
            name="🪽️ **ADMIN ONLY**", 
            value=(
                "`/setup [channel]` - Set where confessions appear.\n"
                "`/grudge @user` - Make me hate someone permanently.\n"
                "`/ungrudge @user` - Forgive a user."
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
            "timestamp": datetime.datetime.utcnow()
        })
        
        response = "ok sent."
        if category.value == "bug": response = "👾 **Bug Reported.** Thanks for reporting, We will look after it."
        elif category.value == "feature": response = "✨ **Suggestion Sent.** We will see what we can do."
        
        await interaction.followup.send(response)

async def setup(bot):
    await bot.add_cog(General(bot))
