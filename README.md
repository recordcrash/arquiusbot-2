# ARQuiusbot

![Deploy Status](https://github.com/recordcrash/arquiusbot-2/actions/workflows/main.yml/badge.svg?branch=master)

**ARQuiusbot** is a modern multi-feature Discord bot. It handles moderation, role management, logging, random text generation, and more. The code is split across multiple cogs.

It's designed for the [Homestuck Discord](https://discord.gg/homestuck), but can be adapted to other private servers. Each instance only supports one server at a time.

## Features

- **Slash Commands:** Modern Discord slash commands for moderation, channel management, fun text generation, color roles, and more.
- **Moderation & Utility Tools:** Commands include `/raidban` (ban multiple users), `/modhelp`, `/channel ban`, `/channel unban`, `/banlist`, and others.
- **Logging:** Logs mod actions, user joins/leaves, and bot events to configurable channels.
- **Role Management:** `color_role_manager` cog lets users self-assign color roles via reactions.
- **Daily Counters & Timed Statuses:** Automated tasks (daily counters, rotating statuses) configured via `cogs.json`.
- **Fun Generators:** Generates random names, dungeon names, tavern names, troll names, and more via `/generate` commands.

## Project Structure

```
ARQuiusbot/
├─ bot.py                    # Main bot entry point
├─ bootstrap.py              # Migrate database from old Arquiusbot
├─ classes/                  # Shared classes & utilities
├─ cogs/                     # Feature-specific cogs
│  ├─ admin.py
│  ├─ ban_manager.py
│  ├─ bullshit_generator.py
│  ├─ color_role_manager.py
│  ├─ daily_counter.py
│  ├─ errors.py
│  ├─ event_listeners.py
│  ├─ linky.py
│  ├─ log_manager.py
│  ├─ misc.py
│  ├─ mod_commands.py
│  └─ status.py
├─ constants/                # Static constants
├─ config/
│  ├─ bot.json.dist          # Example bot config (rename to bot.json)
│  └─ cogs.json.dist         # Example cog configurations
├─ texts/                    # Text files used for random generation
├─ views/                    # UI components (dropdowns, modals)
├─ LICENSE
├─ README.md                 # You are here!
├─ pyproject.toml            # Project dependencies
└─ ...
```

## Installation

1\. **Clone the repository:**

```
git clone <repository-url>
cd ARQuiusbot
```

2\. **Configure your bot:**

Copy and rename the provided example files:

- `config/bot.json.dist` → `config/bot.json`
- `config/cogs.json.dist` → `config/cogs.json`

Edit these files to match your bot and guild settings, following the comments in each file.

3\. **Install dependencies:**

```
uv pip install -r requirements.txt
```

4\. **Run the bot:**

```
python bot.py
```

The bot will sync slash commands to your server on startup.

## Command Overview

| Command              | Description                                     |
|----------------------|-------------------------------------------------|
| `/banlist`           | Show a list of active channel bans.             |
| `/channel ban`       | Temporarily ban a user in the channel.          |
| `/channel unban`     | Remove a channel ban role from a user.          |
| `/daily`             | Manually post the daily stats report.           |
| `/flex`              | Request a STRONG flex from the bot.             |
| `/generate <option>` | Generate fun random names (tavern, troll, etc). |
| `/ignoreplebs`       | Toggle command execution restrictions.          |
| `/info`              | Shows basic user data.                          |
| `/latex`             | Render a LaTeX equation.                        |
| `/linky`             | Trigger Linkybot message.                       |
| `/modhelp`           | Show moderation commands.                       |
| `/modperms`          | Show your guild permissions.                    |
| `/ping`              | Ping the bot.                                   |
| `/raidban`           | Ban multiple users at once.                     |
| `/reportlog`         | Manually post the bot's error logs.             |

There are other owner-only commands that use the prefix `D-->`. 
These are mostly used for reloading cogs and other debug actions, and irrelevant to end users.

## Cogs Overview

Each feature is modularized as a Discord "Cog":

- **Admin (`admin.py`):** Administrative commands.
- **Ban Manager (`ban_manager.py`):** Manage channel bans and unbans.
- **Bullshit Generator (`bullshit_generator.py`):** Fun/random text generation commands.
- **Color Role Manager (`color_role_manager.py`):** Allow users to self-assign color roles through reactions or manual commands.
- **Daily Counter (`daily_counter.py`):** Counts daily stats per channel and thread, then posts them.
- **Error Handling (`errors.py`):** Centralized command-error handler.
- **Event Listeners (`event_listeners.py`):** Responds to events like user join/leave, image reactions.
- **Linky (`linky.py`):** Stores/retrieves a single user's messages randomly.
- **Log Manager (`log_manager.py`):** Handles mod and bot logs.
- **Misc (`misc.py`):** Miscellaneous utility/fun commands.
- **Mod Commands (`mod_commands.py`):** Moderation tools and commands.
- **Status (`status.py`):** Periodically updates the bot's presence.

## Database

ARQuiusbot uses SQLite (`bot_data.db` by default) to store:
- Last known user roles (for reassigning upon rejoin)
- Temporary moderation states (channel bans/ignoreplebs status)

Ensure your bot has permission to create/read/write to this file.

## Running the Bot

Once configuration files and dependencies are in place:

```
python bot.py
```

On initial run, commands are synced to Discord.

## Contribution

Feel free to open issues or submit pull requests if you have improvements, bug fixes, or suggestions.

## License

This project is licensed under the repository's [LICENSE](LICENSE).

---

Thank you for using **ARQuiusbot**! If you have any questions or suggestions, please open an issue on GitHub.

