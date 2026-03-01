from __future__ import annotations

import logging

import discord
from discord import app_commands

from app.config import Settings

from .commands import register_commands
from .handlers import handle_message
from .state import BotState

logger = logging.getLogger(__name__)


class NoteExpanderBot(discord.Client):
    def __init__(self, settings: Settings, state: BotState) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.settings = settings
        self.state = state
        self.tree = app_commands.CommandTree(self)
        register_commands(self)

    async def setup_hook(self) -> None:
        """Called before on_ready. Syncs slash commands with Discord."""
        if self.settings.discord_guild_id:
            # Guild sync: instant, good for personal bots
            guild = discord.Object(id=int(self.settings.discord_guild_id))
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logger.info("Synced %d commands to guild %s", len(synced), self.settings.discord_guild_id)
        else:
            # Global sync: can take up to 1 hour to propagate
            synced = await self.tree.sync()
            logger.info(
                "Synced %d commands globally (may take up to 1 hour to appear in Discord)",
                len(synced),
            )

        @self.tree.error
        async def on_command_error(
            interaction: discord.Interaction, error: app_commands.AppCommandError
        ) -> None:
            logger.exception("Slash command error: %s", error)
            msg = f"Something went wrong: `{error}`"
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(msg, ephemeral=True)
                else:
                    await interaction.response.send_message(msg, ephemeral=True)
            except Exception:
                pass

    async def on_ready(self) -> None:
        logger.info("Discord bot ready: %s (id=%s)", self.user, self.user.id)
        host = self.settings.dashboard.host
        if host == "0.0.0.0":
            host = "localhost"
        print(f"[Bot] Connected as {self.user} (id={self.user.id})")
        print(f"[Bot] LLM: {self.settings.llm_provider_label}")
        print(f"[Bot] Dashboard: http://{host}:{self.settings.dashboard.port}")
        print(f"[Bot] Listening modes: DM • @mention • /research • {len(self.state.listen_channel_ids)} listen channel(s)")
        if not self.settings.discord_guild_id:
            print("[Bot] NOTE: No DISCORD_GUILD_ID set — slash commands may take up to 1 hour to appear globally.")
            print("[Bot]       Set DISCORD_GUILD_ID in .env for instant command sync.")

    async def on_message(self, message: discord.Message) -> None:
        # Ignore the bot's own messages and other bots
        if message.author == self.user or message.author.bot:
            return

        # Guild filter (allows DMs through even when guild_id is set)
        if self.settings.discord_guild_id:
            in_dm = isinstance(message.channel, discord.DMChannel)
            wrong_guild = message.guild is None or str(message.guild.id) != self.settings.discord_guild_id
            if not in_dm and wrong_guild:
                return

        is_dm = isinstance(message.channel, discord.DMChannel)
        is_mentioned = self.user in message.mentions if self.user else False
        is_listen_channel = message.channel.id in self.state.listen_channel_ids

        # Only handle if the message is relevant to this bot
        if not (is_dm or is_mentioned or is_listen_channel):
            return

        logger.debug(
            "Handling message from %s (dm=%s mention=%s listen=%s): %r",
            message.author.id, is_dm, is_mentioned, is_listen_channel,
            message.content[:80],
        )

        await handle_message(
            message, self.settings, self.state, self, is_mentioned=is_mentioned
        )

    async def on_error(self, event: str, *args, **kwargs) -> None:
        logger.exception("Discord error in event %s", event)
