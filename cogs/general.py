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
            description="I judge you based on your chat history now.",
            color=discord.Color.from_rgb(255, 105, 180)
        )
        embed.add_field(name="👀 **JUDGMENT**", value="`/roast` `/rate` `/ship`", inline=False)
        embed.add_field(name="🔥 **DRAMA**", value="`/rename` `/confess` `/crush` `/truth` `/dare`", inline=False)
        embed.add_field(name="🧠 **BRAIN**", value="`/ask` `/wipe`\n*I can also see images and hear voice notes!*", inline=False)
        embed.set_footer(text="Use /feedback for bugs!")
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
        await interaction.followup.send("✅ Sent!")

async def setup(bot):
    await bot.add_cog(General(bot))
