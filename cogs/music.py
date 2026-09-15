"""
Music playback — mention-command only (e.g. "@Welcomer play imagine dragons"),
backed entirely by Lavalink through the `wavelink` client library.

There is NO yt-dlp and NO ffmpeg on the bot's own host. Every search,
download, and audio filter runs on the Lavalink node itself; the bot only
sends control frames (play/pause/volume/filters) and Discord relays the
resulting audio. See README.md → "Music (Lavalink) setup" for how to point
this at a Lavalink v4 server.

Same interactive "Now Playing" card as before (buttons + a filter dropdown),
still deliberately NOT registered as slash commands per the requested design
— everything here is a mention/prefix command: "@Welcomer play ...".
"""
import asyncio
import logging
import random

import discord
from discord.ext import commands
from discord.http import Route
import wavelink

from config import SUCCESS_COLOR, ERROR_COLOR, EMBED_COLOR
from database import db
from utils.embeds import brand_embed, loading_embed

log = logging.getLogger("welcomer.music")

# Effectively "never auto-leave from idling" — requested explicitly. 24/7 mode
# already skips this timer entirely; this just makes the non-24/7 default
# very long too, rather than the previous 3-minute idle disconnect.
IDLE_DISCONNECT_SECONDS = 99999999


async def set_voice_status(bot: commands.Bot, channel_id: int, status: str):
    """
    Sets the little text under a voice channel's name in the channel list
    (the same feature you see in the Discord client, added mid-2024).
    discord.py doesn't wrap this everywhere across versions, so it's called
    directly via the real endpoint: PUT /channels/{id}/voice-status.
    Requires the "Set Voice Channel Status" permission (included in
    Administrator, which this bot already needs for anti-nuke).
    Silently no-ops on failure — a missing status is cosmetic, never worth
    erroring the whole music flow over.
    """
    status = (status or "")[:480]  # Discord's cap is 500 chars; leave headroom
    try:
        route = Route("PUT", "/channels/{channel_id}/voice-status", channel_id=channel_id)
        await bot.http.request(route, json={"status": status})
    except Exception:
        log.debug("Couldn't set voice channel status on %s", channel_id, exc_info=True)

# Lavalink-native filters (applied server-side, no restart-from-0 needed —
# unlike the old ffmpeg approach, Lavalink re-applies filters live).
FILTER_LABELS = {
    "none": "🚫 None",
    "bassboost": "🔊 Bass Boost",
    "nightcore": "⏫ Nightcore",
    "vaporwave": "🌴 Vaporwave",
    "8d": "🎧 8D Audio",
}


def build_filters(key: str) -> wavelink.Filters:
    """Fresh Filters object for the given preset. 'none' clears everything."""
    filters = wavelink.Filters()
    if key == "bassboost":
        filters.equalizer.set(bands=[
            {"band": 0, "gain": 0.30},
            {"band": 1, "gain": 0.25},
            {"band": 2, "gain": 0.20},
            {"band": 3, "gain": 0.10},
        ])
    elif key == "nightcore":
        filters.timescale.set(pitch=1.2, speed=1.15, rate=1.0)
    elif key == "vaporwave":
        filters.timescale.set(pitch=0.8, speed=0.85, rate=1.0)
    elif key == "8d":
        filters.rotation.set(rotation_hz=0.2)
    return filters


def _format_duration(ms) -> str:
    if not ms:
        return "--:--"
    seconds = int(ms // 1000)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _progress_bar(elapsed_ms: float, total_ms, length: int = 18) -> str:
    if not total_ms:
        return "🔴 `LIVE`"
    ratio = max(0.0, min(elapsed_ms / total_ms, 1.0))
    filled = int(ratio * length)
    bar = "▬" * filled + "🔘" + "▬" * (length - filled)
    return f"`{_format_duration(elapsed_ms)}` {bar} `{_format_duration(total_ms)}`"


def _requester(track: wavelink.Playable):
    """We stash the requester on track.extras when queueing (see play())."""
    try:
        return track.extras.requester
    except AttributeError:
        return None


class LavalinkPlayer(wavelink.Player):
    """wavelink.Player subclass carrying the extra state our UI/commands need."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.text_channel: discord.abc.Messageable | None = None
        self.audio_filter: str = "none"
        self.stay_247: bool = False
        self.now_playing_message: discord.Message | None = None
        self._idle_task: asyncio.Task | None = None

    def cancel_idle_timer(self):
        if self._idle_task and not self._idle_task.done():
            self._idle_task.cancel()
        self._idle_task = None

    def start_idle_timer(self):
        self.cancel_idle_timer()
        if self.stay_247:
            return
        self._idle_task = asyncio.get_event_loop().create_task(self._idle_disconnect())

    async def _idle_disconnect(self):
        try:
            await asyncio.sleep(IDLE_DISCONNECT_SECONDS)
        except asyncio.CancelledError:
            return
        if self.stay_247 or self.playing or not self.queue.is_empty:
            return
        try:
            await self.disconnect()
        except Exception:
            pass


def build_now_playing_embed(bot: commands.Bot, player: LavalinkPlayer) -> discord.Embed:
    t = player.current
    loop_labels = {
        wavelink.QueueMode.normal: "Off",
        wavelink.QueueMode.loop: "Track",
        wavelink.QueueMode.loop_all: "Queue",
    }
    autoplay_label = "On" if player.autoplay is wavelink.AutoPlayMode.enabled else "Off"
    requester = _requester(t)
    embed = discord.Embed(
        title=f"🎶 {t.title}",
        url=t.uri,
        description=(
            f"*by {t.author or 'Unknown artist'}*\n\n"
            f"{_progress_bar(player.position, t.length)}\n\n"
            f"**Loop:** {loop_labels.get(player.queue.mode, 'Off')} · "
            f"**Volume:** {player.volume}% · "
            f"**Filter:** {FILTER_LABELS.get(player.audio_filter, '🚫 None')} · "
            f"**Autoplay:** {autoplay_label}"
        ),
        color=EMBED_COLOR,
    )
    if t.artwork:
        embed.set_thumbnail(url=t.artwork)
    embed.set_footer(
        text=f"Requested by {getattr(requester, 'display_name', 'Autoplay')} · Now Playing · Lavalink",
        icon_url=bot.user.display_avatar.url if bot.user else None,
    )
    return embed


class FilterSelect(discord.ui.Select):
    def __init__(self, cog: "Music", player: LavalinkPlayer):
        self.cog = cog
        self.player = player
        options = [
            discord.SelectOption(label=label, value=key, default=(key == player.audio_filter))
            for key, label in FILTER_LABELS.items()
        ]
        super().__init__(placeholder="🎛️ Select Filters", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        key = self.values[0]
        self.player.audio_filter = key
        await self.player.set_filters(build_filters(key))
        await interaction.response.edit_message(embed=build_now_playing_embed(self.cog.bot, self.player), view=self.view)


class MusicControls(discord.ui.View):
    def __init__(self, cog: "Music", player: LavalinkPlayer):
        super().__init__(timeout=None)
        self.cog = cog
        self.player = player
        self.add_item(FilterSelect(cog, player))
        self._sync_button_labels()

    def _sync_button_labels(self):
        self.pause_btn.label = "Resume" if self.player.paused else "Pause"
        loop_label = {
            wavelink.QueueMode.normal: "Loop",
            wavelink.QueueMode.loop: "Loop: Track",
            wavelink.QueueMode.loop_all: "Loop: Queue",
        }[self.player.queue.mode]
        self.loop_btn.label = loop_label
        self.loop_btn.style = (
            discord.ButtonStyle.primary if self.player.queue.mode != wavelink.QueueMode.normal
            else discord.ButtonStyle.secondary
        )
        autoplay_on = self.player.autoplay is wavelink.AutoPlayMode.enabled
        self.autoplay_btn.label = "Autoplay: On" if autoplay_on else "Autoplay"
        self.autoplay_btn.style = discord.ButtonStyle.success if autoplay_on else discord.ButtonStyle.secondary

    async def _refresh(self, interaction: discord.Interaction):
        self._sync_button_labels()
        await interaction.response.edit_message(embed=build_now_playing_embed(self.cog.bot, self.player), view=self)

    @discord.ui.button(label="Pause", style=discord.ButtonStyle.secondary, row=1)
    async def pause_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.player.pause(not self.player.paused)
        await self._refresh(interaction)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary, row=1)
    async def skip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.player.queue.mode == wavelink.QueueMode.loop:
            self.player.queue.mode = wavelink.QueueMode.normal
        await self.player.skip(force=True)
        await interaction.response.defer()

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, row=1)
    async def stop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.player.queue.clear()
        self.player.queue.mode = wavelink.QueueMode.normal
        self.player.autoplay = wavelink.AutoPlayMode.partial
        await self.player.skip(force=True)
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Loop", style=discord.ButtonStyle.secondary, row=2)
    async def loop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        order = [wavelink.QueueMode.normal, wavelink.QueueMode.loop, wavelink.QueueMode.loop_all]
        self.player.queue.mode = order[(order.index(self.player.queue.mode) + 1) % len(order)]
        await self._refresh(interaction)

    @discord.ui.button(label="Shuffle", style=discord.ButtonStyle.secondary, row=2)
    async def shuffle_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.player.queue.shuffle()
        await interaction.response.send_message("🔀 Queue shuffled.", ephemeral=True)

    @discord.ui.button(label="Autoplay", style=discord.ButtonStyle.secondary, row=2)
    async def autoplay_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        enabled = self.player.autoplay is wavelink.AutoPlayMode.enabled
        self.player.autoplay = wavelink.AutoPlayMode.partial if enabled else wavelink.AutoPlayMode.enabled
        await self._refresh(interaction)


class Music(commands.Cog, name="Music"):
    """Mention-command music player, powered by Lavalink: @Bot play/skip/stop/queue/join/leave/24-7/volume/loop/shuffle/autoplay."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        # If Lavalink is already reachable by the time we're ready, this is a
        # no-op duplicate of on_wavelink_node_ready's rejoin (harmless — it
        # just skips guilds already correctly connected). If Lavalink is NOT
        # reachable yet, this still gets 24/7 guilds into voice (music-less)
        # right away instead of leaving them disconnected until Lavalink
        # eventually comes up.
        if self._node_ready():
            await self._rejoin_247_channels()
        else:
            await self._rejoin_247_voice_only()

    async def _rejoin_247_voice_only(self):
        for row in await db.list_247_guilds():
            guild = self.bot.get_guild(row["guild_id"])
            if not guild or guild.voice_client is not None:
                continue
            channel = guild.get_channel(row["stay_247_channel_id"])
            if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
                continue
            try:
                await channel.connect(cls=discord.VoiceClient, self_deaf=True)
                await set_voice_status(self.bot, channel.id, "😌 Chilling")
                log.info("Rejoined 24/7 channel #%s in guild %s (voice-only, Lavalink down)", channel.name, guild.name)
            except Exception:
                log.exception("Failed to voice-only-rejoin 24/7 channel in guild %s", guild.id)

    def _node_ready(self) -> bool:
        # BUG FIX: `wavelink.Pool.nodes` lists every *registered* node, even
        # ones that failed to connect or dropped later — checking just the
        # count made join/24-7 think Lavalink was fine when it wasn't. Check
        # each node's actual connection status instead.
        return any(
            getattr(node, "status", None) == wavelink.NodeStatus.CONNECTED
            for node in wavelink.Pool.nodes.values()
        )

    async def _no_node_error(self, ctx: commands.Context):
        await ctx.reply(
            embed=brand_embed(
                self.bot,
                description="❌ Music isn't available right now — the Lavalink server isn't connected. Ask the bot host to check it.",
                color=ERROR_COLOR,
            ),
            mention_author=False,
        )

    async def _ensure_voice(self, ctx: commands.Context, require_music: bool = True) -> LavalinkPlayer | None:
        """
        require_music=True (used by `play` and anything that streams audio):
        Lavalink MUST be connected — errors out otherwise, same as before.

        require_music=False (used by `join`/`24-7`, which are just "be present
        in a voice channel" commands): if Lavalink isn't reachable, connects
        with a plain discord.py VoiceClient instead — no music playback, but
        the bot still joins and can sit there 24/7. If Lavalink IS reachable,
        it still connects with the full LavalinkPlayer so `play` works right
        away without needing to reconnect.
        """
        if ctx.author.voice is None or ctx.author.voice.channel is None:
            await ctx.reply(embed=brand_embed(self.bot, description="❌ Join a voice channel first.", color=ERROR_COLOR), mention_author=False)
            return None

        node_ready = self._node_ready()
        if require_music and not node_ready:
            await self._no_node_error(ctx)
            return None

        existing = ctx.voice_client
        target_cls = LavalinkPlayer if node_ready else discord.VoiceClient

        # If we're already connected with the "wrong" client type for what we
        # need now (e.g. joined voice-only earlier, Lavalink just came back
        # and `play` was called), swap the connection over.
        if existing is not None and node_ready and not isinstance(existing, LavalinkPlayer):
            try:
                await existing.disconnect(force=True)
            except Exception:
                pass
            existing = None

        if existing is None:
            try:
                player = await ctx.author.voice.channel.connect(cls=target_cls, self_deaf=True)
            except discord.ClientException:
                await ctx.reply(embed=brand_embed(self.bot, description="❌ Already connecting — try again in a second.", color=ERROR_COLOR), mention_author=False)
                return None
            if isinstance(player, LavalinkPlayer):
                player.autoplay = wavelink.AutoPlayMode.partial
            await set_voice_status(self.bot, player.channel.id, "😌 Chilling")
        else:
            player = existing
            if player.channel.id != ctx.author.voice.channel.id:
                old_channel_id = player.channel.id
                await player.move_to(ctx.author.voice.channel)
                await set_voice_status(self.bot, old_channel_id, "")
                if isinstance(player, LavalinkPlayer) and not player.playing:
                    await set_voice_status(self.bot, player.channel.id, "😌 Chilling")

        if isinstance(player, LavalinkPlayer):
            player.text_channel = ctx.channel
            player.cancel_idle_timer()
        return player

    def get_player(self, ctx: commands.Context) -> LavalinkPlayer | None:
        """Only returns a player if it's a real Lavalink-backed one — a
        voice-only (no Lavalink) connection returns None here, so playback
        commands correctly say "nothing is playing" instead of crashing on
        an attribute a plain discord.py VoiceClient doesn't have."""
        vc = ctx.voice_client
        return vc if isinstance(vc, LavalinkPlayer) else None

    # ---------- Lavalink node / player events ----------
    @commands.Cog.listener()
    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload):
        log.info("Lavalink node ready: %s (session %s)", payload.node.uri, payload.session_id)
        await self._rejoin_247_channels()

    async def _rejoin_247_channels(self):
        """Runs once Lavalink connects (on startup, or if it reconnects after
        being down). For every guild with 24/7 enabled: if we're not in
        voice at all, join fresh with full music support. If we're already
        sitting there voice-only (joined while Lavalink was down), upgrade
        that connection to a real LavalinkPlayer so playback works now."""
        for row in await db.list_247_guilds():
            guild = self.bot.get_guild(row["guild_id"])
            if not guild:
                continue
            channel = guild.get_channel(row["stay_247_channel_id"])
            if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
                continue

            existing = guild.voice_client
            if isinstance(existing, LavalinkPlayer):
                continue  # already fully set up
            if existing is not None:
                try:
                    await existing.disconnect(force=True)
                except Exception:
                    pass

            try:
                player: LavalinkPlayer = await channel.connect(cls=LavalinkPlayer, self_deaf=True)
                player.autoplay = wavelink.AutoPlayMode.partial
                player.stay_247 = True
                await set_voice_status(self.bot, channel.id, "😌 Chilling")
                log.info("Rejoined 24/7 channel #%s in guild %s (now with music)", channel.name, guild.name)
            except Exception:
                log.exception("Failed to rejoin 24/7 channel in guild %s", guild.id)

    @commands.Cog.listener()
    async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload):
        player: LavalinkPlayer = payload.player
        if not player or not isinstance(player, LavalinkPlayer):
            return
        track = payload.track
        status = f"🎵 Playing: {track.author} - {track.title}" if track.author else f"🎵 Playing: {track.title}"
        await set_voice_status(self.bot, player.channel.id, status)
        if not player.text_channel:
            return
        view = MusicControls(self, player)
        embed = build_now_playing_embed(self.bot, player)
        try:
            player.now_playing_message = await player.text_channel.send(embed=embed, view=view)
        except discord.HTTPException:
            player.now_playing_message = None

    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload):
        player: LavalinkPlayer = payload.player
        if not player or not isinstance(player, LavalinkPlayer):
            return
        if not player.playing and player.queue.is_empty:
            await set_voice_status(self.bot, player.channel.id, "😌 Chilling")
            player.start_idle_timer()

    # ---------- join / leave ----------
    @commands.command(name="join", aliases=["summon"])
    async def join(self, ctx: commands.Context):
        player = await self._ensure_voice(ctx, require_music=False)
        if player:
            note = "" if isinstance(player, LavalinkPlayer) else " (music isn't available right now — Lavalink isn't connected, so I'm here voice-only)"
            await ctx.reply(embed=brand_embed(self.bot, description=f"✅ Joined **{player.channel.name}**.{note}", color=SUCCESS_COLOR), mention_author=False)

    @commands.command(name="leave", aliases=["disconnect", "dc"])
    async def leave(self, ctx: commands.Context):
        vc = ctx.voice_client
        if vc:
            channel_id = vc.channel.id
            if isinstance(vc, LavalinkPlayer):
                vc.cancel_idle_timer()
                vc.queue.clear()
            await db.update(ctx.guild.id, stay_247=0)
            await vc.disconnect(force=True)
            await set_voice_status(self.bot, channel_id, "")
        await ctx.reply(embed=brand_embed(self.bot, description="👋 Left the voice channel.", color=SUCCESS_COLOR), mention_author=False)

    # ---------- play ----------
    @commands.command(name="play", aliases=["p"])
    async def play(self, ctx: commands.Context, *, query: str = None):
        if not query:
            return await ctx.reply(embed=brand_embed(self.bot, description=f"Usage: `@{self.bot.user.name} play <song name or link>`", color=ERROR_COLOR), mention_author=False)

        player = await self._ensure_voice(ctx, require_music=True)
        if not player:
            return

        status = await ctx.reply(embed=loading_embed(self.bot, f"🔎 Searching for **{query}**..."), mention_author=False)

        try:
            results: wavelink.Search = await wavelink.Playable.search(query)
        except Exception:
            log.exception("Search failed for query: %s", query)
            return await status.edit(embed=brand_embed(self.bot, description="❌ Couldn't reach Lavalink to search for that.", color=ERROR_COLOR))

        if not results:
            return await status.edit(embed=brand_embed(self.bot, description="❌ Couldn't find that song.", color=ERROR_COLOR))

        was_idle = not player.playing and player.queue.is_empty

        if isinstance(results, wavelink.Playlist):
            for track in results.tracks:
                track.extras = {"requester": ctx.author}
            await player.queue.put_wait(results.tracks)
            await status.edit(embed=brand_embed(self.bot, title="➕ Playlist queued", description=f"**{results.name}** — {len(results.tracks)} tracks", color=SUCCESS_COLOR))
        else:
            track = results[0]
            track.extras = {"requester": ctx.author}
            await player.queue.put_wait(track)
            if was_idle:
                await status.edit(embed=brand_embed(self.bot, description=f"▶️ Starting **{track.title}**...", color=SUCCESS_COLOR))
            else:
                await status.edit(embed=brand_embed(self.bot, title="➕ Added to queue", description=f"**{track.title}**\nPosition in queue: **{len(player.queue)}**", color=SUCCESS_COLOR))

        if not player.playing:
            next_track = player.queue.get()
            await player.play(next_track)

    # ---------- playback controls (text-command versions, still work alongside the buttons) ----------
    @commands.command(name="skip", aliases=["next", "s"])
    async def skip(self, ctx: commands.Context):
        player = self.get_player(ctx)
        if not player or not player.playing:
            return await ctx.reply(embed=brand_embed(self.bot, description="❌ Nothing is playing.", color=ERROR_COLOR), mention_author=False)
        skipped = player.current.title if player.current else "the current track"
        if player.queue.mode == wavelink.QueueMode.loop:
            player.queue.mode = wavelink.QueueMode.normal
        await player.skip(force=True)
        await ctx.reply(embed=brand_embed(self.bot, description=f"⏭️ Skipped **{skipped}**.", color=SUCCESS_COLOR), mention_author=False)

    @commands.command(name="stop")
    async def stop(self, ctx: commands.Context):
        player = self.get_player(ctx)
        if not player:
            return await ctx.reply(embed=brand_embed(self.bot, description="❌ Nothing is playing.", color=ERROR_COLOR), mention_author=False)
        player.queue.clear()
        player.queue.mode = wavelink.QueueMode.normal
        player.autoplay = wavelink.AutoPlayMode.partial
        if player.playing:
            await player.skip(force=True)
        await ctx.reply(embed=brand_embed(self.bot, description="⏹️ Stopped and cleared the queue.", color=SUCCESS_COLOR), mention_author=False)

    @commands.command(name="pause")
    async def pause(self, ctx: commands.Context):
        player = self.get_player(ctx)
        if player and player.playing and not player.paused:
            await player.pause(True)
            return await ctx.reply(embed=brand_embed(self.bot, description="⏸️ Paused.", color=SUCCESS_COLOR), mention_author=False)
        await ctx.reply(embed=brand_embed(self.bot, description="❌ Nothing is playing.", color=ERROR_COLOR), mention_author=False)

    @commands.command(name="resume", aliases=["unpause"])
    async def resume(self, ctx: commands.Context):
        player = self.get_player(ctx)
        if player and player.paused:
            await player.pause(False)
            return await ctx.reply(embed=brand_embed(self.bot, description="▶️ Resumed.", color=SUCCESS_COLOR), mention_author=False)
        await ctx.reply(embed=brand_embed(self.bot, description="❌ Nothing is paused.", color=ERROR_COLOR), mention_author=False)

    @commands.command(name="loop")
    async def loop(self, ctx: commands.Context):
        player = self.get_player(ctx)
        if not player:
            return await ctx.reply(embed=brand_embed(self.bot, description="❌ Nothing is playing.", color=ERROR_COLOR), mention_author=False)
        order = [wavelink.QueueMode.normal, wavelink.QueueMode.loop, wavelink.QueueMode.loop_all]
        player.queue.mode = order[(order.index(player.queue.mode) + 1) % len(order)]
        labels = {wavelink.QueueMode.normal: "Off", wavelink.QueueMode.loop: "Track", wavelink.QueueMode.loop_all: "Queue"}
        await ctx.reply(embed=brand_embed(self.bot, description=f"🔁 Loop mode: **{labels[player.queue.mode]}**.", color=SUCCESS_COLOR), mention_author=False)

    @commands.command(name="shuffle")
    async def shuffle(self, ctx: commands.Context):
        player = self.get_player(ctx)
        if not player or player.queue.is_empty:
            return await ctx.reply(embed=brand_embed(self.bot, description="❌ The queue is empty.", color=ERROR_COLOR), mention_author=False)
        player.queue.shuffle()
        await ctx.reply(embed=brand_embed(self.bot, description="🔀 Queue shuffled.", color=SUCCESS_COLOR), mention_author=False)

    @commands.command(name="autoplay")
    async def autoplay_cmd(self, ctx: commands.Context):
        player = self.get_player(ctx)
        if not player:
            return await ctx.reply(embed=brand_embed(self.bot, description="❌ Nothing is playing.", color=ERROR_COLOR), mention_author=False)
        enabled = player.autoplay is wavelink.AutoPlayMode.enabled
        player.autoplay = wavelink.AutoPlayMode.partial if enabled else wavelink.AutoPlayMode.enabled
        status = "disabled" if enabled else "enabled — I'll play similar tracks once the queue ends"
        await ctx.reply(embed=brand_embed(self.bot, description=f"🔄 Autoplay {status}.", color=SUCCESS_COLOR), mention_author=False)

    # ---------- info ----------
    @commands.command(name="queue", aliases=["q"])
    async def queue_cmd(self, ctx: commands.Context):
        player = self.get_player(ctx)
        if not player or (not player.current and player.queue.is_empty):
            return await ctx.reply(embed=brand_embed(self.bot, description="The queue is empty.", color=EMBED_COLOR), mention_author=False)
        lines = []
        if player.current:
            lines.append(f"**Now playing:** {player.current.title} · `{_format_duration(player.current.length)}`")
        upcoming = list(player.queue)[:10]
        for i, tr in enumerate(upcoming, start=1):
            requester = _requester(tr)
            lines.append(f"`{i}.` {tr.title} · `{_format_duration(tr.length)}` — {getattr(requester, 'mention', 'Autoplay')}")
        if len(player.queue) > 10:
            lines.append(f"...and {len(player.queue) - 10} more")
        await ctx.reply(embed=brand_embed(self.bot, title="🎼 Queue", description="\n".join(lines), color=EMBED_COLOR), mention_author=False)

    @commands.command(name="nowplaying", aliases=["np"])
    async def nowplaying(self, ctx: commands.Context):
        player = self.get_player(ctx)
        if not player or not player.current:
            return await ctx.reply(embed=brand_embed(self.bot, description="Nothing is playing.", color=EMBED_COLOR), mention_author=False)
        await ctx.reply(embed=build_now_playing_embed(self.bot, player), view=MusicControls(self, player), mention_author=False)

    @commands.command(name="volume", aliases=["vol"])
    async def volume(self, ctx: commands.Context, level: int = None):
        player = self.get_player(ctx)
        if not player:
            if not self._node_ready():
                return await self._no_node_error(ctx)
            return await ctx.reply(embed=brand_embed(self.bot, description="❌ I'm not in a voice channel.", color=ERROR_COLOR), mention_author=False)
        if level is None:
            return await ctx.reply(embed=brand_embed(self.bot, description=f"🔊 Current volume: **{player.volume}%**", color=EMBED_COLOR), mention_author=False)
        level = max(0, min(level, 150))
        await player.set_volume(level)
        await ctx.reply(embed=brand_embed(self.bot, description=f"🔊 Volume set to **{level}%**.", color=SUCCESS_COLOR), mention_author=False)

    @commands.command(name="24/7", aliases=["247", "24.7"])
    async def twenty_four_seven(self, ctx: commands.Context):
        """Toggles 24/7 presence. Works even without Lavalink connected — in
        that case the bot just sits in the voice channel (no music), and will
        automatically switch to full music mode once Lavalink comes back."""
        player = await self._ensure_voice(ctx, require_music=False)
        if not player:
            return
        currently_on = (await db.get_settings(ctx.guild.id))["stay_247"]
        turning_on = not currently_on

        if isinstance(player, LavalinkPlayer):
            player.stay_247 = turning_on
            if turning_on:
                player.cancel_idle_timer()

        if turning_on:
            await db.update(ctx.guild.id, stay_247=1, stay_247_channel_id=player.channel.id)
        else:
            await db.update(ctx.guild.id, stay_247=0)

        if turning_on:
            note = "" if isinstance(player, LavalinkPlayer) else " — no music playback right now since Lavalink isn't connected, but I'll stay in the channel and switch on music automatically once it's back"
            status = f"enabled — I'll stay connected 24/7, even through restarts{note}"
        else:
            status = "disabled — I'll leave after being idle a while"
        await ctx.reply(embed=brand_embed(self.bot, description=f"🔁 24/7 mode {status}.", color=SUCCESS_COLOR), mention_author=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
