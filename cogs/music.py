"""
Music playback — mention-command only (e.g. "@Welcomer play imagine dragons"),
with a stylish interactive "Now Playing" card (buttons + a filter dropdown),
deliberately NOT registered as slash commands per the requested design.

Requires:
  - the 'ffmpeg' binary installed on the host (apt install ffmpeg on the VPS)
  - yt-dlp and PyNaCl (see requirements.txt)
  - the bot's Voice States intent enabled (already set in bot.py)

Note on the filter dropdown: changing a filter restarts the current track
from the beginning with that ffmpeg audio filter applied — there's no
seek-and-reapply, so switching filters mid-song will replay it from 0:00.
"Autoplay" is a simple approximation (searches for something similar to the
last track when the queue runs out) — not YouTube's actual Mix/Radio algorithm.
"""
import asyncio
import functools
import logging
import random
import time

import discord
from discord.ext import commands
import yt_dlp as youtube_dl

from config import SUCCESS_COLOR, ERROR_COLOR, EMBED_COLOR
from utils.embeds import brand_embed, loading_embed

log = logging.getLogger("welcomer.music")
youtube_dl.utils.bug_reports_message = lambda: ""

YDL_OPTS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
}
IDLE_DISCONNECT_SECONDS = 180

FILTERS = {
    "none": None,
    "bassboost": "bass=g=20",
    "nightcore": "asetrate=44100*1.25,aresample=44100",
    "vaporwave": "asetrate=44100*0.8,aresample=44100,atempo=1.15",
    "8d": "apulsator=hz=0.08",
}
FILTER_LABELS = {
    "none": "🚫 None",
    "bassboost": "🔊 Bass Boost",
    "nightcore": "⏫ Nightcore",
    "vaporwave": "🌴 Vaporwave",
    "8d": "🎧 8D Audio",
}


def _extract(query: str) -> dict:
    """Blocking — always run this in an executor."""
    with youtube_dl.YoutubeDL(YDL_OPTS) as ydl:
        info = ydl.extract_info(query, download=False)
        if "entries" in info:
            info = info["entries"][0]
        return info


def _format_duration(seconds) -> str:
    if not seconds:
        return "--:--"
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _progress_bar(elapsed: float, total, length: int = 18) -> str:
    if not total:
        return "🔴 `LIVE`"
    ratio = max(0.0, min(elapsed / total, 1.0))
    filled = int(ratio * length)
    bar = "▬" * filled + "🔘" + "▬" * (length - filled)
    return f"`{_format_duration(elapsed)}` {bar} `{_format_duration(total)}`"


class Track:
    def __init__(self, title, webpage_url, duration, requester, thumbnail=None, uploader=None):
        self.title = title
        self.webpage_url = webpage_url
        self.duration = duration
        self.requester = requester
        self.thumbnail = thumbnail
        self.uploader = uploader or "Unknown artist"


class GuildMusicState:
    def __init__(self, bot: commands.Bot, guild: discord.Guild):
        self.bot = bot
        self.guild = guild
        self.queue: list[Track] = []
        self.current: Track | None = None
        self.voice_client: discord.VoiceClient | None = None
        self.volume: float = 1.0
        self.stay_247: bool = False
        self.text_channel: discord.abc.Messageable | None = None
        self.next_song_event = asyncio.Event()
        self.task = bot.loop.create_task(self._player_loop())

        # Player-card state
        self.loop_mode = "off"       # "off" | "track" | "queue"
        self.autoplay = False
        self.audio_filter = "none"
        self.started_at: float | None = None
        self.now_playing_message: discord.Message | None = None
        self.restart_requested = False  # set true to replay current track (e.g. after a filter change)

    def is_idle(self) -> bool:
        return not (self.voice_client and (self.voice_client.is_playing() or self.voice_client.is_paused()))

    def elapsed(self) -> float:
        return time.time() - self.started_at if self.started_at else 0.0

    def enqueue(self, track: Track):
        was_idle = self.is_idle()
        self.queue.append(track)
        if was_idle:
            self.next_song_event.set()

    def ffmpeg_options(self) -> dict:
        af = FILTERS.get(self.audio_filter)
        return {
            "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            "options": f"-vn -af {af}" if af else "-vn",
        }

    async def _find_autoplay_track(self) -> "Track | None":
        if not self.current:
            return None
        loop = asyncio.get_event_loop()
        try:
            info = await loop.run_in_executor(
                None, functools.partial(_extract, f"ytsearch1:{self.current.uploader} {self.current.title} similar")
            )
        except Exception:
            return None
        if not info or info.get("webpage_url") == self.current.webpage_url:
            return None
        return Track(
            title=info.get("title", "Unknown title"),
            webpage_url=info.get("webpage_url"),
            duration=info.get("duration"),
            requester=self.bot.user,
            thumbnail=info.get("thumbnail"),
            uploader=info.get("uploader"),
        )

    async def _player_loop(self):
        while True:
            self.next_song_event.clear()
            self.restart_requested = False

            if not self.queue:
                if self.autoplay and self.current:
                    next_track = await self._find_autoplay_track()
                    if next_track:
                        self.queue.append(next_track)

            if not self.queue:
                if self.stay_247:
                    await asyncio.sleep(1)
                    continue
                try:
                    await asyncio.wait_for(self.next_song_event.wait(), timeout=IDLE_DISCONNECT_SECONDS)
                    continue
                except asyncio.TimeoutError:
                    if self.voice_client and not self.queue:
                        try:
                            await self.voice_client.disconnect()
                        except Exception:
                            pass
                        self.voice_client = None
                    continue

            self.current = self.queue.pop(0)
            await self._play_current()
            await self.next_song_event.wait()

            if self.restart_requested and self.current:
                self.queue.insert(0, self.current)
            elif self.loop_mode == "track" and self.current:
                self.queue.insert(0, self.current)
            elif self.loop_mode == "queue" and self.current:
                self.queue.append(self.current)
            self.current = None

    async def _play_current(self):
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, functools.partial(_extract, self.current.webpage_url))
            stream_url = data["url"]
        except Exception:
            log.exception("Failed to resolve stream for %s", self.current.title)
            if self.text_channel:
                await self.text_channel.send(
                    embed=brand_embed(self.bot, description=f"⚠️ Skipping **{self.current.title}** — couldn't load it.", color=ERROR_COLOR)
                )
            self.current = None
            self.next_song_event.set()
            return

        if not self.voice_client or not self.voice_client.is_connected():
            self.current = None
            self.next_song_event.set()
            return

        source = discord.PCMVolumeTransformer(
            discord.FFmpegPCMAudio(stream_url, **self.ffmpeg_options()), volume=self.volume
        )

        def _after(err, guild_id=self.guild.id):
            if err:
                log.error("Player error in guild %s: %s", guild_id, err)
            self.bot.loop.call_soon_threadsafe(self.next_song_event.set)

        self.started_at = time.time()
        self.voice_client.play(source, after=_after)

        if self.text_channel:
            view = MusicControls(self.bot.get_cog("Music"), self)
            embed = build_now_playing_embed(self.bot, self)
            try:
                self.now_playing_message = await self.text_channel.send(embed=embed, view=view)
            except discord.HTTPException:
                self.now_playing_message = None


def build_now_playing_embed(bot: commands.Bot, state: GuildMusicState) -> discord.Embed:
    t = state.current
    loop_labels = {"off": "Off", "track": "Track", "queue": "Queue"}
    embed = discord.Embed(
        title=f"🎶 {t.title}",
        description=(
            f"*by {t.uploader}*\n\n"
            f"{_progress_bar(state.elapsed(), t.duration)}\n\n"
            f"**Loop:** {loop_labels[state.loop_mode]} · "
            f"**Volume:** {int(state.volume * 100)}% · "
            f"**Filter:** {FILTER_LABELS[state.audio_filter]}"
        ),
        color=EMBED_COLOR,
    )
    if t.thumbnail:
        embed.set_thumbnail(url=t.thumbnail)
    embed.set_footer(
        text=f"Requested by {getattr(t.requester, 'display_name', str(t.requester))} · Now Playing",
        icon_url=bot.user.display_avatar.url if bot.user else None,
    )
    return embed


class FilterSelect(discord.ui.Select):
    def __init__(self, cog: "Music", state: GuildMusicState):
        self.cog = cog
        self.state = state
        options = [
            discord.SelectOption(label=label, value=key, default=(key == state.audio_filter))
            for key, label in FILTER_LABELS.items()
        ]
        super().__init__(placeholder="🎛️ Select Filters", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        self.state.audio_filter = self.values[0]
        if self.state.voice_client and (self.state.voice_client.is_playing() or self.state.voice_client.is_paused()):
            self.state.restart_requested = True
            self.state.voice_client.stop()  # re-plays the same track with the new filter, from 0:00
        await interaction.response.edit_message(embed=build_now_playing_embed(self.cog.bot, self.state), view=self.view)


class MusicControls(discord.ui.View):
    def __init__(self, cog: "Music", state: GuildMusicState):
        super().__init__(timeout=None)
        self.cog = cog
        self.state = state
        self.add_item(FilterSelect(cog, state))
        self._sync_button_labels()

    def _sync_button_labels(self):
        self.pause_btn.label = "Resume" if (self.state.voice_client and self.state.voice_client.is_paused()) else "Pause"
        self.loop_btn.label = {"off": "Loop", "track": "Loop: Track", "queue": "Loop: Queue"}[self.state.loop_mode]
        self.loop_btn.style = discord.ButtonStyle.primary if self.state.loop_mode != "off" else discord.ButtonStyle.secondary
        self.autoplay_btn.label = "Autoplay: On" if self.state.autoplay else "Autoplay"
        self.autoplay_btn.style = discord.ButtonStyle.success if self.state.autoplay else discord.ButtonStyle.secondary

    async def _refresh(self, interaction: discord.Interaction):
        self._sync_button_labels()
        await interaction.response.edit_message(embed=build_now_playing_embed(self.cog.bot, self.state), view=self)

    @discord.ui.button(label="Pause", style=discord.ButtonStyle.secondary, row=1)
    async def pause_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self.state.voice_client
        if vc and vc.is_playing():
            vc.pause()
        elif vc and vc.is_paused():
            vc.resume()
        await self._refresh(interaction)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary, row=1)
    async def skip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.state.voice_client and (self.state.voice_client.is_playing() or self.state.voice_client.is_paused()):
            self.state.loop_mode = "off" if self.state.loop_mode == "track" else self.state.loop_mode
            self.state.voice_client.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, row=1)
    async def stop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.state.queue.clear()
        self.state.loop_mode = "off"
        self.state.autoplay = False
        if self.state.voice_client and (self.state.voice_client.is_playing() or self.state.voice_client.is_paused()):
            self.state.voice_client.stop()
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Loop", style=discord.ButtonStyle.secondary, row=2)
    async def loop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        order = ["off", "track", "queue"]
        self.state.loop_mode = order[(order.index(self.state.loop_mode) + 1) % len(order)]
        await self._refresh(interaction)

    @discord.ui.button(label="Shuffle", style=discord.ButtonStyle.secondary, row=2)
    async def shuffle_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        random.shuffle(self.state.queue)
        await interaction.response.send_message("🔀 Queue shuffled.", ephemeral=True)

    @discord.ui.button(label="Autoplay", style=discord.ButtonStyle.secondary, row=2)
    async def autoplay_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.state.autoplay = not self.state.autoplay
        await self._refresh(interaction)


class Music(commands.Cog, name="Music"):
    """Mention-command music player: @Bot play/skip/stop/queue/join/leave/24-7/volume."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.states: dict[int, GuildMusicState] = {}

    def get_state(self, guild: discord.Guild) -> GuildMusicState:
        if guild.id not in self.states:
            self.states[guild.id] = GuildMusicState(self.bot, guild)
        return self.states[guild.id]

    async def _ensure_voice(self, ctx: commands.Context) -> GuildMusicState | None:
        state = self.get_state(ctx.guild)
        if ctx.author.voice is None or ctx.author.voice.channel is None:
            await ctx.reply(embed=brand_embed(self.bot, description="❌ Join a voice channel first.", color=ERROR_COLOR), mention_author=False)
            return None
        if state.voice_client is None or not state.voice_client.is_connected():
            state.voice_client = await ctx.author.voice.channel.connect()
        elif state.voice_client.channel.id != ctx.author.voice.channel.id:
            await state.voice_client.move_to(ctx.author.voice.channel)
        state.text_channel = ctx.channel
        return state

    # ---------- join / leave ----------
    @commands.command(name="join", aliases=["summon"])
    async def join(self, ctx: commands.Context):
        state = await self._ensure_voice(ctx)
        if state:
            await ctx.reply(embed=brand_embed(self.bot, description=f"✅ Joined **{state.voice_client.channel.name}**.", color=SUCCESS_COLOR), mention_author=False)

    @commands.command(name="leave", aliases=["disconnect", "dc"])
    async def leave(self, ctx: commands.Context):
        state = self.get_state(ctx.guild)
        if state.voice_client:
            state.queue.clear()
            await state.voice_client.disconnect()
            state.voice_client = None
            state.current = None
        await ctx.reply(embed=brand_embed(self.bot, description="👋 Left the voice channel.", color=SUCCESS_COLOR), mention_author=False)

    # ---------- play ----------
    @commands.command(name="play", aliases=["p"])
    async def play(self, ctx: commands.Context, *, query: str = None):
        if not query:
            return await ctx.reply(embed=brand_embed(self.bot, description=f"Usage: `@{self.bot.user.name} play <song name or link>`", color=ERROR_COLOR), mention_author=False)

        state = await self._ensure_voice(ctx)
        if not state:
            return

        status = await ctx.reply(embed=loading_embed(self.bot, f"🔎 Searching for **{query}**..."), mention_author=False)

        loop = asyncio.get_event_loop()
        try:
            info = await loop.run_in_executor(None, functools.partial(_extract, query))
        except Exception:
            log.exception("Search failed for query: %s", query)
            return await status.edit(embed=brand_embed(self.bot, description="❌ Couldn't find that song.", color=ERROR_COLOR))

        track = Track(
            title=info.get("title", "Unknown title"),
            webpage_url=info.get("webpage_url") or query,
            duration=info.get("duration"),
            requester=ctx.author,
            thumbnail=info.get("thumbnail"),
            uploader=info.get("uploader"),
        )
        was_idle = state.is_idle()
        state.enqueue(track)

        if was_idle:
            await status.edit(embed=brand_embed(self.bot, description=f"▶️ Starting **{track.title}**...", color=SUCCESS_COLOR))
        else:
            await status.edit(embed=brand_embed(self.bot, title="➕ Added to queue", description=f"**{track.title}**\nPosition in queue: **{len(state.queue)}**", color=SUCCESS_COLOR))

    # ---------- playback controls (text-command versions, still work alongside the buttons) ----------
    @commands.command(name="skip", aliases=["next", "s"])
    async def skip(self, ctx: commands.Context):
        state = self.get_state(ctx.guild)
        if not state.voice_client or not (state.voice_client.is_playing() or state.voice_client.is_paused()):
            return await ctx.reply(embed=brand_embed(self.bot, description="❌ Nothing is playing.", color=ERROR_COLOR), mention_author=False)
        skipped = state.current.title if state.current else "the current track"
        state.loop_mode = "off" if state.loop_mode == "track" else state.loop_mode
        state.voice_client.stop()
        await ctx.reply(embed=brand_embed(self.bot, description=f"⏭️ Skipped **{skipped}**.", color=SUCCESS_COLOR), mention_author=False)

    @commands.command(name="stop")
    async def stop(self, ctx: commands.Context):
        state = self.get_state(ctx.guild)
        state.queue.clear()
        state.loop_mode = "off"
        state.autoplay = False
        if state.voice_client and (state.voice_client.is_playing() or state.voice_client.is_paused()):
            state.voice_client.stop()
        await ctx.reply(embed=brand_embed(self.bot, description="⏹️ Stopped and cleared the queue.", color=SUCCESS_COLOR), mention_author=False)

    @commands.command(name="pause")
    async def pause(self, ctx: commands.Context):
        state = self.get_state(ctx.guild)
        if state.voice_client and state.voice_client.is_playing():
            state.voice_client.pause()
            return await ctx.reply(embed=brand_embed(self.bot, description="⏸️ Paused.", color=SUCCESS_COLOR), mention_author=False)
        await ctx.reply(embed=brand_embed(self.bot, description="❌ Nothing is playing.", color=ERROR_COLOR), mention_author=False)

    @commands.command(name="resume", aliases=["unpause"])
    async def resume(self, ctx: commands.Context):
        state = self.get_state(ctx.guild)
        if state.voice_client and state.voice_client.is_paused():
            state.voice_client.resume()
            return await ctx.reply(embed=brand_embed(self.bot, description="▶️ Resumed.", color=SUCCESS_COLOR), mention_author=False)
        await ctx.reply(embed=brand_embed(self.bot, description="❌ Nothing is paused.", color=ERROR_COLOR), mention_author=False)

    # ---------- info ----------
    @commands.command(name="queue", aliases=["q"])
    async def queue_cmd(self, ctx: commands.Context):
        state = self.get_state(ctx.guild)
        if not state.current and not state.queue:
            return await ctx.reply(embed=brand_embed(self.bot, description="The queue is empty.", color=EMBED_COLOR), mention_author=False)
        lines = []
        if state.current:
            lines.append(f"**Now playing:** {state.current.title} · `{_format_duration(state.current.duration)}`")
        for i, tr in enumerate(state.queue[:10], start=1):
            lines.append(f"`{i}.` {tr.title} · `{_format_duration(tr.duration)}` — {getattr(tr.requester, 'mention', tr.requester)}")
        if len(state.queue) > 10:
            lines.append(f"...and {len(state.queue) - 10} more")
        await ctx.reply(embed=brand_embed(self.bot, title="🎼 Queue", description="\n".join(lines), color=EMBED_COLOR), mention_author=False)

    @commands.command(name="nowplaying", aliases=["np"])
    async def nowplaying(self, ctx: commands.Context):
        state = self.get_state(ctx.guild)
        if not state.current:
            return await ctx.reply(embed=brand_embed(self.bot, description="Nothing is playing.", color=EMBED_COLOR), mention_author=False)
        await ctx.reply(embed=build_now_playing_embed(self.bot, state), view=MusicControls(self, state), mention_author=False)

    @commands.command(name="volume", aliases=["vol"])
    async def volume(self, ctx: commands.Context, level: int = None):
        state = self.get_state(ctx.guild)
        if level is None:
            return await ctx.reply(embed=brand_embed(self.bot, description=f"🔊 Current volume: **{int(state.volume * 100)}%**", color=EMBED_COLOR), mention_author=False)
        level = max(0, min(level, 150))
        state.volume = level / 100
        if state.voice_client and state.voice_client.source:
            state.voice_client.source.volume = state.volume
        await ctx.reply(embed=brand_embed(self.bot, description=f"🔊 Volume set to **{level}%**.", color=SUCCESS_COLOR), mention_author=False)

    @commands.command(name="24/7", aliases=["247"])
    async def twenty_four_seven(self, ctx: commands.Context):
        state = self.get_state(ctx.guild)
        state.stay_247 = not state.stay_247
        status = "enabled — I'll stay connected even with an empty queue" if state.stay_247 else "disabled — I'll leave after being idle a while"
        await ctx.reply(embed=brand_embed(self.bot, description=f"🔁 24/7 mode {status}.", color=SUCCESS_COLOR), mention_author=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
