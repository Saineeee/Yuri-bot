import discord
from discord.ext import commands
from discord import app_commands
import utils
import asyncio
import datetime
from typing import Optional

class Social(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def get_ai_cog(self):
        return self.bot.get_cog("AI")

    @app_commands.command(name="roast", description="DESTROY someone based on history.")
    async def roast(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer()
        dossier = utils.get_user_dossier(member)
        history = await utils.get_user_history_text(self.bot.chat_collection, member.id)
        pfp = await utils.get_image_from_url(member.display_avatar.url) if member.display_avatar else None
        
        prompt = (f"TARGET:\n{dossier}\nRECENT CHATS:\n{history}\n"
                  f"INSTRUCTION: Roast them based on PFP and chat history. Call them out on things they said. Be brutal.")
        
        ai = await self.get_ai_cog()
        resp, _ = await ai.get_combined_response(interaction.user.id, None, image_input=pfp, prompt_override=prompt)
        await utils.send_chunked_reply(interaction, f"{member.mention} {resp}")

    @app_commands.command(name="rate", description="Judge vibe based on chat history.")
    async def rate(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer()
        dossier = utils.get_user_dossier(member)
        history = await utils.get_user_history_text(self.bot.chat_collection, member.id)
        pfp = await utils.get_image_from_url(member.display_avatar.url) if member.display_avatar else None
        
        prompt = (f"TARGET:\n{dossier}\nRECENT CHATS:\n{history}\n"
                  f"INSTRUCTION: Rate vibe (0-100%). If they are funny/nice in chats, give high score. If dry/rude, destroy them.")
        
        ai = await self.get_ai_cog()
        resp, _ = await ai.get_combined_response(interaction.user.id, None, image_input=pfp, prompt_override=prompt)
        await utils.send_chunked_reply(interaction, f"{member.mention} {resp}")

    @app_commands.command(name="ship", description="Check compatibility.")
    async def ship(self, interaction: discord.Interaction, member1: discord.Member, member2: Optional[discord.Member] = None):
        await interaction.response.defer()
        target2 = member2 if member2 else interaction.user
        
        d1 = utils.get_user_dossier(member1)
        h1 = await utils.get_user_history_text(self.bot.chat_collection, member1.id, limit=10)
        d2 = utils.get_user_dossier(target2)
        h2 = await utils.get_user_history_text(self.bot.chat_collection, target2.id, limit=10)
        
        combined_img = None
        if member1.display_avatar and target2.display_avatar:
            img1 = await utils.get_image_from_url(member1.display_avatar.url)
            img2 = await utils.get_image_from_url(target2.display_avatar.url)
            if img1 and img2:
                combined_img = await asyncio.to_thread(utils.stitch_images, img1, img2)

        prompt = (f"USER 1:\n{d1}\nCHATS:\n{h1}\n\nUSER 2:\n{d2}\nCHATS:\n{h2}\n"
                  f"INSTRUCTION: Check compatibility. Analyze chat styles. Give % Score.")
        
        ai = await self.get_ai_cog()
        resp, _ = await ai.get_combined_response(interaction.user.id, None, image_input=combined_img, prompt_override=prompt)
        await utils.send_chunked_reply(interaction, resp)

    @app_commands.command(name="confess", description="Send an anonymous confession.")
    async def confess(self, interaction: discord.Interaction, message: str):
        await interaction.response.defer(ephemeral=True)
        config = await self.bot.config_collection.find_one({"guild_id": interaction.guild_id})
        
        if not config or "confession_channel_id" not in config:
            await interaction.followup.send("❌ Admin must run `/setup` first!", ephemeral=True)
            return
            
        channel = interaction.guild.get_channel(config["confession_channel_id"]) or await interaction.guild.fetch_channel(config["confession_channel_id"])
        
        embed = discord.Embed(title="📨 Anonymous Confession", description=f'"{message}"', color=discord.Color.random())
        embed.set_footer(text="Sent via /confess • Identity Hidden")
        await channel.send(embed=embed)
        await interaction.followup.send("✅ Sent!", ephemeral=True)

    @app_commands.command(name="crush", description="Secretly match with your crush!")
    async def crush(self, interaction: discord.Interaction, target: discord.Member):
        await interaction.response.defer(ephemeral=True)
        if target.id == interaction.user.id or target.bot:
            await interaction.followup.send("Invalid target lol. 💀", ephemeral=True)
            return
            
        match = await self.bot.crush_collection.find_one({"lover_id": target.id, "target_id": interaction.user.id})
        if match:
            try: await interaction.user.send(f"💖 **MATCH!** {target.display_name} likes you back!")
            except: pass
            try: await target.send(f"💖 **MATCH!** {interaction.user.display_name} likes you back!")
            except: pass
            await interaction.channel.send(f"@everyone 🚨 **LOVE ALERT:** Two people just matched via `/crush`! 💍✨")
            await self.bot.crush_collection.delete_one({"_id": match["_id"]})
            await interaction.followup.send("💖 **IT'S A MATCH!**", ephemeral=True)
        else:
            await self.bot.crush_collection.update_one(
                {"lover_id": interaction.user.id, "target_id": target.id},
                {"$set": {"timestamp": datetime.datetime.utcnow()}},
                upsert=True
            )
            await interaction.followup.send(f"🤫 **Secret Kept.**", ephemeral=True)

    @app_commands.command(name="truth", description="Get a spicy Truth question.")
    async def truth(self, interaction: discord.Interaction):
        await interaction.response.defer()
        ai = await self.get_ai_cog()
        resp, _ = await ai.get_combined_response(interaction.user.id, None, prompt_override="Give a funny, spicy teenage Truth question.")
        await utils.send_chunked_reply(interaction, f"**TRUTH:** {resp}")

    @app_commands.command(name="dare", description="Get a chaotic Dare.")
    async def dare(self, interaction: discord.Interaction):
        await interaction.response.defer()
        ai = await self.get_ai_cog()
        resp, _ = await ai.get_combined_response(interaction.user.id, None, prompt_override="Give a funny, chaotic Dare for a discord user.")
        await utils.send_chunked_reply(interaction, f"**DARE:** {resp}")

async def setup(bot):
    await bot.add_cog(Social(bot))
