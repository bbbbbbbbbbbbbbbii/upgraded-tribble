# Welcomer Bot — Professional Discord Welcome/Leave Bot

A full-featured, modular Discord bot built with `discord.py` (slash commands),
`aiosqlite` for per-server settings, and `Pillow` for generated welcome banner
images (avatar + name + member count), similar to popular welcomer bots.

## Features

- **`/autosetup`** — one command configures welcome, leave, and DM messages
  end-to-end: creates a "Welcomer" category with `#welcome` / `#leave-log`
  channels (or reuses existing ones), applies polished ready-to-use message
  templates, enables the image card, optionally assigns an autorole, and shows
  a **confirmation prompt with Confirm/Cancel buttons** before touching
  anything — then a **live preview** of exactly what members will see
- **Welcome messages** — channel message + auto-generated image card, fully
  customizable text with placeholders (`{mention}`, `{server}`, `{membercount}`, ...)
- **Custom welcome card backgrounds** — set any image URL as the card background
- **DM welcome** — optional private welcome message to new members
- **Leave/goodbye messages** — separate channel + customizable text
- **Autorole** — auto-assign one or more roles to new members
- **Per-server embed color**
- **Join/leave stats tracking**
- **Reset commands** (`/welcome reset`, `/welcome dm-reset`, `/leave reset`) to
  restore default text after experimenting
- **Test/preview commands** so admins can see changes before going live
- **Consistent branded embeds** — every reply carries the bot's avatar and a
  timestamp in the footer, like a polished commercial bot
- **Clean slash-command UI** with grouped commands (`/welcome`, `/leave`, `/autorole`)
- **Per-guild SQLite storage** — settings persist across restarts, isolated per server
- **Mention commands** — say `@Welcomer ping`, `@Welcomer help`, or
  `@Welcomer autorole add @Role` and the bot responds directly, same as the
  slash version (works alongside `/ping`, `/help`, `/autorole`, not instead of)
- **Music player — pure Lavalink (mention-only, no slash commands)** —
  `@Welcomer play <song>` (or `p`), `skip`, `stop`, `pause`, `resume`, `loop`,
  `shuffle`, `autoplay`, `queue`, `nowplaying` (`np`), `join`, `leave`,
  `volume <0-150>`, `24/7` mode, plus a live Now Playing card with buttons and
  a filter dropdown (Bass Boost / Nightcore / Vaporwave / 8D). All searching,
  decoding, and streaming happens on the Lavalink node — **no yt-dlp and no
  ffmpeg run on the bot's own host.**

## Extra setup for mention-commands & music

1. **Enable "Message Content Intent"** — Discord Developer Portal → your app →
   Bot → scroll to Privileged Gateway Intents → turn on **Message Content
   Intent**. Without this, the bot can't read `@Welcomer ping`-style messages
   at all (slash commands still work fine either way). This is the #1 reason
   mention-commands appear "broken" — the bot silently can't see the message.
2. **Run a Lavalink v4 node.** This is a separate small Java server the bot
   talks to over HTTP/WebSocket — it does the actual audio work.
   - Requires **Java 17+**.
   - Download the latest `Lavalink.jar` from the official releases:
     https://github.com/lavalink-devs/Lavalink/releases
   - Next to the jar, create an `application.yml` (minimal example):
     ```yaml
     server:
       port: 2333
     lavalink:
       server:
         password: "youshallnotpass"
         sources:
           youtube: true
           soundcloud: true
           bandcamp: true
           twitch: true
           vimeo: true
           http: true
     ```
     > Note: as of Lavalink v4, YouTube playback needs the community
     > `youtube-source` plugin (add it under a `plugins:` block in
     > `application.yml` per that plugin's README) since built-in YouTube
     > support was removed upstream for ToS reasons. SoundCloud works
     > out of the box and is a solid fallback.
   - Start it: `java -jar Lavalink.jar`
   - You can self-host this on the same VPS as the bot, or use a separate
     machine, or (for quick testing only) a public Lavalink node — public
     nodes are unreliable and shouldn't be used for anything but trying
     things out.
3. **Point the bot at your node** — in `.env`, set `LAVALINK_HOST`,
   `LAVALINK_PORT`, `LAVALINK_PASSWORD` (and `LAVALINK_SECURE=true` if it's
   behind HTTPS) to match your `application.yml`.
4. **Install the Python dependencies**:
   ```
   pip install -r requirements.txt
   ```
   (this installs `wavelink`, the Lavalink client — no `yt-dlp`/`PyNaCl` needed)
5. Make sure the bot's role has **Connect** and **Speak** permissions in
   whatever voice channels you want it to join.

If the Lavalink node isn't reachable, the bot still starts fine — every music
command just replies "Music isn't available right now" instead of failing
silently.


## Project Structure

```
welcomer-bot/
├── bot.py                 # entry point
├── config.py               # constants & env loading
├── database.py              # aiosqlite settings layer
├── requirements.txt
├── .env.example
├── utils/
│   ├── placeholders.py     # {placeholder} text rendering
│   ├── image_gen.py        # Pillow welcome-card generator
│   └── embeds.py            # shared branded-embed builder
└── cogs/
    ├── welcome.py           # on_member_join + /welcome commands
    ├── leave.py             # on_member_remove + /leave commands
    ├── autorole.py          # /autorole commands
    ├── autosetup.py         # /autosetup guided one-command setup
    ├── general.py           # /config, /embed-color, /stats, /ping, /help
    ├── mention_commands.py  # @Bot ping/help/autorole (mirrors slash versions)
    └── music.py             # @Bot play/skip/stop/... — Lavalink via wavelink
```

## Setup

1. **Create a bot application**: https://discord.com/developers/applications
   - Bot tab → Reset Token → copy it
   - Under **Privileged Gateway Intents**, enable **SERVER MEMBERS INTENT**
     (required for join/leave events and autorole)
   - OAuth2 → URL Generator → scopes: `bot`, `applications.commands`
     Permissions: `Manage Roles`, `Send Messages`, `Embed Links`, `Attach Files`
   - Use the generated URL to invite the bot to your server

2. **Install dependencies** (Python 3.10+):
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment**:
   ```bash
   cp .env.example .env
   # edit .env and paste your bot token
   ```
   Optionally set `DEV_GUILD_ID` to your test server's ID while developing —
   this makes slash commands sync instantly instead of waiting up to an hour
   for global sync.

4. **Run the bot**:
   ```bash
   python bot.py
   ```

## Commands

All configuration commands require the **Manage Server** permission.

### 🛠️ Quick start
| Command | Description |
|---|---|
| `/autosetup [welcome_channel] [leave_channel] [dm_welcome] [autorole]` | Configures everything in one step. Shows a review embed with **Confirm/Cancel** buttons first — nothing is created or changed until you confirm. Reuses existing `#welcome` / `#leave-log` channels if found, otherwise creates a `Welcomer` category with both. Finishes with a live preview of the exact welcome message + image card a new member would see. |

### Welcome
| Command | Description |
|---|---|
| `/welcome channel <#channel>` | Set the welcome channel (also enables welcome) |
| `/welcome toggle <on/off>` | Enable/disable welcome messages |
| `/welcome message <text>` | Set welcome text (supports placeholders) |
| `/welcome reset` | Restore the default welcome message |
| `/welcome card <on/off>` | Toggle the generated image banner |
| `/welcome background <url>` | Set a custom card background image (`reset` to clear) |
| `/welcome dm <on/off> [text]` | Configure a private welcome DM |
| `/welcome dm-reset` | Restore the default DM message |
| `/welcome test` | Preview the welcome message/card (anyone can run) |
| `/welcome placeholders` | Show available `{placeholders}` (anyone can run) |

### Leave
| Command | Description |
|---|---|
| `/leave channel <#channel>` | Set the leave channel (also enables leave) |
| `/leave toggle <on/off>` | Enable/disable leave messages |
| `/leave message <text>` | Set leave text (supports placeholders) |
| `/leave reset` | Restore the default leave message |
| `/leave test` | Preview the leave message (anyone can run) |

### Autorole
| Command | Description |
|---|---|
| `/autorole add <@role>` | Auto-assign this role to new members |
| `/autorole remove <@role>` | Remove a role from autorole |
| `/autorole list` | List current autoroles (anyone can run) |
| `/autorole clear` | Remove all autoroles |

### Music (mention-only — `@Welcomer <command>`, not `/`)
| Command | Description |
|---|---|
| `play <song/link>` (`p`) | Play now or add to the queue (Lavalink search + direct links) |
| `skip` (`s`) | Skip the current track |
| `stop` | Stop and clear the queue |
| `pause` / `resume` | Pause/resume playback |
| `loop` | Cycle loop mode: Off → Track → Queue |
| `shuffle` | Shuffle the queue |
| `autoplay` | Toggle auto-queuing similar tracks when the queue ends |
| `queue` (`q`) | Show the current queue |
| `nowplaying` (`np`) | Show the Now Playing card with buttons + filter dropdown |
| `join` / `leave` | Voice channel control |
| `24/7` | Stay connected even with an empty queue |
| `volume <0-150>` | Set playback volume |

### General
| Command | Description |
|---|---|
| `/config` | View the full current configuration |
| `/embed-color <#hex>` | Set the embed color used everywhere (`reset` for default) |
| `/stats` | Show tracked join/leave counts (anyone can run) |
| `/ping` | Latency check (anyone can run) |
| `/help` | List all commands (anyone can run) |

## Placeholders

Usable in `/welcome message`, `/leave message`, and the DM message:

| Placeholder | Result |
|---|---|
| `{mention}` | Pings the user |
| `{user}` | `Name#0000` |
| `{user_name}` | Display name |
| `{server}` | Server name |
| `{membercount}` | Current member count |
| `{membercount_ordinal}` | e.g. `42nd` |

Example:
```
/welcome message text: 🎉 {mention} just joined {server}! You're our {membercount_ordinal} member.
```

## Notes on the image card

The welcome banner is generated on the fly with Pillow — no third-party image
API needed. It draws:
- A circular avatar with an accent-colored ring (matches your embed color)
- The member's display name and a "Member #N" subtitle
- Either your custom background URL (blurred + darkened for legibility) or a
  smooth gradient fallback

If avatar/background fetching fails for any reason (rate limit, bad URL,
network hiccup), the bot automatically falls back to a plain embed with no
image rather than erroring out.

## Extending it

- Add more cogs under `cogs/` and list them in `EXTENSIONS` in `bot.py`.
- All settings live in one SQLite row per guild (`database.py`) — add new
  columns to `SCHEMA`/`_DEFAULTS` and they're immediately usable via
  `db.get_settings()` / `db.update()`.
