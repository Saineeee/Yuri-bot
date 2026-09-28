"""Image generation cog — `/imagine` command using Google Imagen.

Lets users generate images from text prompts. Yuri's personality is woven
into the prompt (she adds her own commentary). Generated images are stored
in MongoDB with a 7-day TTL for abuse tracking.
"""
import discord
from discord.ext import commands
from discord import app_commands
from google import genai
from google.genai import types

import os
import io
import logging
from typing import Optional

import utils

log = logging.getLogger(__name__)


IMAGINE_MODEL = "imagen-3.0-generate-002"
MAX_PROMPT_CHARS = 500
IMAGINE_COOLDOWN_SECS = 30


class ImageGen(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._gemini_client: Optional[genai.Client] = None

    @property
    def gemini_client(self) -> Optional[genai.Client]:
        if self._gemini_client is None:
            key = os.getenv("GEMINI_API_KEY")
            if key:
                self._gemini_client = genai.Client(api_key=key)
        return self._gemini_client

    @app_commands.command(
        name="imagine",
        description="Generate an image from a text prompt (Yuri adds her own spin).",
    )
    @app_commands.describe(
        prompt="Describe the image you want (max 500 chars).",
    )
    @app_commands.checks.cooldown(1, IMAGINE_COOLDOWN_SECS)
    async def imagine(self, interaction: discord.Interaction, prompt: str) -> None:
        """Generate an image using Google Imagen.

        The prompt is passed through Yuri's AI brain first to add her
        personality, then sent to Imagen. The result is posted as an
        attachment with Yuri's commentary.
        """
        if len(prompt) > MAX_PROMPT_CHARS:
            await interaction.response.send_message(
                f"keep the prompt under {MAX_PROMPT_CHARS} chars bestie 💀",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        client = self.gemini_client
        if client is None:
            await interaction.followup.send(
                "image generation isn't configured rn (no GEMINI_API_KEY)"
            )
            return

        safe_prompt = utils.sanitize_for_prompt(prompt)

        ai = self.bot.get_cog("AI")
        enhanced_prompt = safe_prompt
        yuri_commentary = ""
        if ai is not None:
            try:
                enhance_override = (
                    f"The user wants to generate an image with this prompt: '{safe_prompt}'. "
                    f"Rewrite it into a single, vivid, detailed image-generation prompt "
                    f"(no commentary, just the prompt — max 200 words). Make it visually "
                    f"specific. Do NOT include any text you wouldn't want in the image. "
                    f"Reply with ONLY the enhanced prompt, nothing else."
                )
                enhanced, _ = await ai.get_combined_response(
                    interaction.user.id, None, prompt_override=enhance_override
                )
                if enhanced and len(enhanced) < 1000:
                    enhanced_prompt = enhanced.strip().strip('"').strip("'")

                commentary_override = (
                    f"The user just generated an image with the prompt: '{safe_prompt}'. "
                    f"Write ONE short sentence (max 15 words) reacting to it as Yuri. "
                    f"Be chaotic and gen-z. Reply with ONLY the reaction."
                )
                yuri_commentary, _ = await ai.get_combined_response(
                    interaction.user.id, None, prompt_override=commentary_override
                )
            except Exception as e:
                log.warning("prompt enhancement failed, using raw prompt: %s", e)

        try:
            response = await client.aio.models.generate_images(
                model=IMAGINE_MODEL,
                prompt=enhanced_prompt,
                config=types.GenerateImagesConfig(
                    number_of_images=1,
                ),
            )

            if not response.generated_images:
                await interaction.followup.send(
                    "the image machine broke rn, try a different prompt"
                )
                return

            img_data = response.generated_images[0].image.image_bytes
            if not img_data:
                await interaction.followup.send(
                    "got an empty image back, try again"
                )
                return

        except Exception as e:
            log.warning("image generation failed: %s", e)
            err_msg = str(e).lower()
            if "safety" in err_msg or "blocked" in err_msg:
                await interaction.followup.send(
                    "ayoo... that prompt got blocked by the safety filter bestie "
                    "keep it clean"
                )
            else:
                await interaction.followup.send(
                    f"image generation failed rn ({type(e).__name__})"
                )
            return

        public_prompt = utils.sanitize_for_discord(prompt)
        public_commentary = utils.sanitize_for_discord(yuri_commentary) if yuri_commentary else ""

        embed = discord.Embed(
            title="🎨 Yuri's Imagination",
            color=discord.Color.from_rgb(255, 105, 180),
            timestamp=utils.utcnow(),
        )
        embed.add_field(name="Prompt", value=public_prompt, inline=False)
        if public_commentary:
            embed.add_field(name="Yuri says", value=public_commentary, inline=False)
        embed.set_footer(text=f"generated by {interaction.user.display_name} • /imagine")

        file = discord.File(io.BytesIO(img_data), filename="yuri_imagine.png")
        embed.set_image(url="attachment://yuri_imagine.png")

        await interaction.followup.send(embed=embed, file=file)

        await self.bot.image_gen_col.insert_one({
            "user_id": interaction.user.id,
            "username": interaction.user.name,
            "guild_id": interaction.guild_id,
            "prompt": prompt[:MAX_PROMPT_CHARS],
            "enhanced_prompt": enhanced_prompt[:1000],
            "timestamp": utils.utcnow(),
        })


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ImageGen(bot))
