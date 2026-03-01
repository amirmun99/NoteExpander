from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from app.agents.crew import compare_notes, followup_note
from app.database.crud import create_note, get_note, list_notes
from app.discord_bot.handlers import _extract_tags
from app.pipeline.processor import process_note

if TYPE_CHECKING:
    from .bot import NoteExpanderBot

logger = logging.getLogger(__name__)


def _dashboard_url(bot: NoteExpanderBot, note_id: str = "") -> str:
    host = bot.settings.dashboard.host
    if host == "0.0.0.0":
        host = "localhost"
    base = f"http://{host}:{bot.settings.dashboard.port}"
    return f"{base}/notes/{note_id}" if note_id else base


def _is_admin(interaction: discord.Interaction, bot: NoteExpanderBot) -> bool:
    """True if the user may run admin commands (allow/deny/listen)."""
    user_id = interaction.user.id
    # Guild admins always qualify
    if interaction.guild and hasattr(interaction.user, "guild_permissions"):
        if interaction.user.guild_permissions.manage_guild:
            return True
    # First entry in config allowed_user_ids is treated as owner
    config_ids = bot.settings.discord.allowed_user_ids
    if config_ids and user_id == config_ids[0]:
        return True
    # Discord application owner
    if bot.application and bot.application.owner:
        return user_id == bot.application.owner.id
    return False


def register_commands(bot: NoteExpanderBot) -> None:
    """Attach all slash commands to bot.tree."""

    # ── /research ────────────────────────────────────────────────────────

    @bot.tree.command(name="research", description="Submit an idea or note for AI research")
    @app_commands.describe(idea="Your project idea or question. Add #deep to use OpenAI instead of local Ollama.")
    async def research_cmd(interaction: discord.Interaction, idea: str) -> None:
        if not bot.state.is_allowed(interaction.user.id, bot.settings.discord.allowed_user_ids):
            await interaction.response.send_message(
                "You are not in the allowed-users list for this bot.", ephemeral=True
            )
            return

        clean_idea, requested_provider, flags = _extract_tags(idea)

        if requested_provider == "openai":
            model_label = f"OpenAI · `{bot.settings.llm.openai_model}`"
        else:
            model_label = f"{'Ollama' if bot.settings.llm.provider == 'ollama' else 'OpenAI'} · `{bot.settings.llm_model_string}`"

        preview = clean_idea[:120] + ("..." if len(clean_idea) > 120 else "")
        await interaction.response.send_message(
            f"Got it! Researching ({model_label}): *\"{preview}\"*\n"
            f"I'll ping you here when the report is ready (usually 2–5 min)."
        )

        note = await create_note(
            raw_text=clean_idea,
            source="text",
            discord_user_id=str(interaction.user.id),
            discord_channel_id=str(interaction.channel_id),
            discord_message_id=str(interaction.id),
            requested_provider=requested_provider,
        )
        asyncio.create_task(
            process_note(note.id, bot.settings, bot, pipeline_flags=flags),
            name=f"process_note_{note.id}",
        )

    # ── /status ──────────────────────────────────────────────────────────

    @bot.tree.command(name="status", description="Show your recent research notes")
    async def status_cmd(interaction: discord.Interaction) -> None:
        notes = await list_notes(limit=50)
        user_id = str(interaction.user.id)

        if not _is_admin(interaction, bot):
            notes = [n for n in notes if n.discord_user_id == user_id]
        notes = notes[:8]

        if not notes:
            await interaction.response.send_message(
                "No research notes yet. Use `/research <your idea>` or DM me to get started!",
                ephemeral=True,
            )
            return

        STATUS_EMOJI = {
            "pending": "⏳",
            "processing": "🔍",
            "complete": "✅",
            "failed": "❌",
        }
        base_url = _dashboard_url(bot)
        lines = [f"**Recent notes** — [Open dashboard]({base_url})\n"]
        for note in notes:
            emoji = STATUS_EMOJI.get(note.status, "❓")
            title = note.report_title or (note.raw_text[:50] + ("…" if len(note.raw_text) > 50 else ""))
            url = _dashboard_url(bot, note.id)
            lines.append(f"{emoji} [{title}]({url}) — `{note.status}`")

        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    # ── /listen ───────────────────────────────────────────────────────────

    @bot.tree.command(
        name="listen",
        description="Auto-research every message posted in this channel (admin only)",
    )
    async def listen_cmd(interaction: discord.Interaction) -> None:
        if not _is_admin(interaction, bot):
            await interaction.response.send_message(
                "Only server admins can add listen channels.", ephemeral=True
            )
            return

        channel_id = interaction.channel_id
        if channel_id in bot.state.listen_channel_ids:
            await interaction.response.send_message(
                f"<#{channel_id}> is already in the listen list.", ephemeral=True
            )
            return

        bot.state.listen_channel_ids.add(channel_id)
        bot.state.save()
        await interaction.response.send_message(
            f"✅ Now listening in <#{channel_id}>.\n"
            f"Every message here will be sent for research. Use `/unlisten` to stop."
        )

    # ── /unlisten ─────────────────────────────────────────────────────────

    @bot.tree.command(
        name="unlisten",
        description="Stop auto-researching messages in this channel (admin only)",
    )
    async def unlisten_cmd(interaction: discord.Interaction) -> None:
        if not _is_admin(interaction, bot):
            await interaction.response.send_message(
                "Only server admins can manage listen channels.", ephemeral=True
            )
            return

        channel_id = interaction.channel_id
        if channel_id not in bot.state.listen_channel_ids:
            await interaction.response.send_message(
                f"<#{channel_id}> is not in the listen list.", ephemeral=True
            )
            return

        bot.state.listen_channel_ids.discard(channel_id)
        bot.state.save()
        await interaction.response.send_message(f"✅ Stopped listening in <#{channel_id}>.")

    # ── /channels ─────────────────────────────────────────────────────────

    @bot.tree.command(name="channels", description="List channels the bot auto-listens to")
    async def channels_cmd(interaction: discord.Interaction) -> None:
        if not bot.state.listen_channel_ids:
            await interaction.response.send_message(
                "No channels in the auto-listen list.\n"
                "Use `/listen` in any channel to add it, or just DM me / @mention me.",
                ephemeral=True,
            )
            return
        lines = ["**Auto-listen channels:**"]
        for cid in sorted(bot.state.listen_channel_ids):
            lines.append(f"• <#{cid}>")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    # ── /allow ────────────────────────────────────────────────────────────

    @bot.tree.command(name="allow", description="Grant a user access to the bot (admin only)")
    @app_commands.describe(user="User to allow")
    async def allow_cmd(interaction: discord.Interaction, user: discord.User) -> None:
        if not _is_admin(interaction, bot):
            await interaction.response.send_message(
                "Only admins can manage user access.", ephemeral=True
            )
            return
        bot.state.extra_allowed_user_ids.add(user.id)
        bot.state.save()
        await interaction.response.send_message(
            f"✅ {user.mention} can now submit research notes.", ephemeral=True
        )

    # ── /deny ─────────────────────────────────────────────────────────────

    @bot.tree.command(name="deny", description="Remove a user's bot access (admin only)")
    @app_commands.describe(user="User to remove")
    async def deny_cmd(interaction: discord.Interaction, user: discord.User) -> None:
        if not _is_admin(interaction, bot):
            await interaction.response.send_message(
                "Only admins can manage user access.", ephemeral=True
            )
            return
        bot.state.extra_allowed_user_ids.discard(user.id)
        bot.state.save()
        await interaction.response.send_message(
            f"✅ Removed {user.mention}'s access.", ephemeral=True
        )

    # ── /compare ──────────────────────────────────────────────────────────

    @bot.tree.command(
        name="compare",
        description="Compare 2–4 completed research notes and rank them",
    )
    @app_commands.describe(
        note_ids="Space-separated note IDs (first 8 chars OK). Example: abc12345 def67890",
    )
    async def compare_cmd(interaction: discord.Interaction, note_ids: str) -> None:
        if not bot.state.is_allowed(interaction.user.id, bot.settings.discord.allowed_user_ids):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return

        ids = note_ids.split()
        if len(ids) < 2 or len(ids) > 4:
            await interaction.response.send_message(
                "Provide 2–4 note IDs. Use `/status` to see your note IDs.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)

        # Resolve partial IDs against all notes
        all_notes = await list_notes(limit=500)
        id_map = {n.id: n for n in all_notes}
        prefix_map = {n.id[:8]: n for n in all_notes}

        notes_data = []
        not_found = []
        for nid in ids:
            note = id_map.get(nid) or prefix_map.get(nid[:8])
            if note is None:
                not_found.append(nid)
            elif note.status != "complete":
                await interaction.followup.send(
                    f"Note `{nid[:8]}` is not complete yet (status: {note.status}).",
                    ephemeral=True,
                )
                return
            else:
                notes_data.append({
                    "title": note.report_title or "Untitled",
                    "type": note.project_type or "unknown",
                    "confidence": note.confidence or 0,
                    "report_markdown": note.report_markdown or "",
                })

        if not_found:
            await interaction.followup.send(
                f"Could not find notes: {', '.join(not_found)}",
                ephemeral=True,
            )
            return

        try:
            comparison = await asyncio.to_thread(
                compare_notes, notes_data, bot.settings
            )
        except Exception as e:
            logger.exception("Compare failed: %s", e)
            await interaction.followup.send(f"Comparison failed: `{e}`", ephemeral=True)
            return

        # Post result — Discord message limit is 2000 chars
        titles = " vs ".join(n["title"][:30] for n in notes_data)
        header = f"**Comparison: {titles}**\n\n"
        body = comparison[:1900 - len(header)]
        await interaction.followup.send(header + body)

    # ── /followup ─────────────────────────────────────────────────────────

    @bot.tree.command(
        name="followup",
        description="Ask a focused follow-up question about a completed research note",
    )
    @app_commands.describe(
        note_id="Note ID (first 8 chars OK)",
        question="Your follow-up question",
    )
    async def followup_cmd(interaction: discord.Interaction, note_id: str, question: str) -> None:
        if not bot.state.is_allowed(interaction.user.id, bot.settings.discord.allowed_user_ids):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        # Resolve note
        all_notes = await list_notes(limit=500)
        note = None
        for n in all_notes:
            if n.id == note_id or n.id.startswith(note_id[:8]):
                note = n
                break

        if note is None:
            await interaction.followup.send(
                f"Note `{note_id[:8]}` not found. Use `/status` to see your notes.",
                ephemeral=True,
            )
            return

        if note.status != "complete":
            await interaction.followup.send(
                f"Note `{note_id[:8]}` is not complete yet (status: {note.status}).",
                ephemeral=True,
            )
            return

        if not note.report_markdown:
            await interaction.followup.send("That note has no report content.", ephemeral=True)
            return

        try:
            answer = await asyncio.to_thread(
                followup_note,
                note.report_markdown,
                note.report_title or "Untitled",
                question,
                bot.settings,
            )
        except Exception as e:
            logger.exception("Follow-up failed: %s", e)
            await interaction.followup.send(f"Follow-up failed: `{e}`", ephemeral=True)
            return

        # Store as a new note in the DB for dashboard viewing
        try:
            followup_text = f"[Follow-up on: {note.report_title}]\n\n{question}"
            new_note = await create_note(
                raw_text=followup_text,
                source="text",
                discord_user_id=str(interaction.user.id),
                discord_channel_id=str(interaction.channel_id),
                discord_message_id=str(interaction.id),
            )
            # Mark it complete directly with the answer
            from app.database.crud import save_report, update_note_status
            await update_note_status(new_note.id, "processing")
            await save_report(
                new_note.id,
                report_markdown=answer,
                report_title=f"Follow-up: {question[:60]}",
                project_type=note.project_type or "unknown",
                confidence=note.confidence or 0,
                llm_provider=bot.settings.llm_provider_label,
            )
            await update_note_status(new_note.id, "complete")
            dashboard_url = _dashboard_url(bot, new_note.id)
        except Exception:
            dashboard_url = None

        header = f"**Follow-up on:** {note.report_title or 'Untitled'}\n**Q:** {question}\n\n"
        body = answer[:1900 - len(header)]
        msg = header + body
        if dashboard_url:
            msg += f"\n\n[View full report]({dashboard_url})"
        await interaction.followup.send(msg)

    # ── /help ─────────────────────────────────────────────────────────────

    @bot.tree.command(name="help", description="Show how to use Note Expander")
    async def help_cmd(interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="Note Expander",
            description=(
                "Send ideas from anywhere — get back comprehensive AI research reports.\n"
                f"Dashboard: {_dashboard_url(bot)}"
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="📨  Submitting notes",
            value=(
                "`/research <idea>` — submit from anywhere\n"
                "**DM me** — any text or voice note becomes a research job\n"
                "**@mention me** in a channel — text after the mention is researched\n"
                "**Listen channels** — every message auto-triggers research\n"
                "**Tags:** `#deep` (OpenAI) · `#quick` (skip web search) · `#market`/`#tech` (force type) · `#noformat` (raw output)"
            ),
            inline=False,
        )
        embed.add_field(
            name="📋  Viewing & analysing results",
            value=(
                f"`/status` — your recent notes with live status + links\n"
                f"`/compare <id1> <id2> [id3] [id4]` — compare notes side-by-side\n"
                f"`/followup <id> <question>` — dig deeper into a completed report\n"
                f"Dashboard: {_dashboard_url(bot)}"
            ),
            inline=False,
        )
        embed.add_field(
            name="⚙️  Admin",
            value=(
                "`/listen` — auto-research all messages in this channel\n"
                "`/unlisten` — stop listening here\n"
                "`/channels` — list active listen channels\n"
                "`/allow @user` / `/deny @user` — manage access"
            ),
            inline=False,
        )
        embed.set_footer(text=f"LLM: {bot.settings.llm_provider_label}")
        await interaction.response.send_message(embed=embed, ephemeral=True)
