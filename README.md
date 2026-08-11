# Marketapp Gift Listing Watcher

Polls marketapp.org's Telegram Gifts marketplace for gifts matching your
criteria (collection, model, price range, etc.) and pings you on Telegram
the moment a match goes on sale.

## Setup (local)

1. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

2. Get your credentials:
   - Telegram bot token: from @BotFather
   - Telegram chat ID: the chat you want notified
   - Marketapp API key: from https://marketapp.org/api-token

   These are **not** stored in `config.py` -- they're read from environment
   variables, so set them in your shell before running:
   ```
   export TELEGRAM_BOT_TOKEN="..."
   export TELEGRAM_CHAT_ID="..."
   export MARKETAPP_API_KEY="..."
   ```

3. (Optional but recommended) Find the collection address for the gift
   collection you're watching:
   ```
   python list_collections.py
   ```
   This prints each collection's name, address, and current floor price.
   Copy the address you want into a watch's `collection_address` field.

4. Edit `WATCHES` in `config.py` to describe what you're looking for -- see
   the comments above `WATCHES` in that file for the full `require` /
   `require_any_of` / `exclude` / `max_percent_above_floor` format.

5. Run it:
   ```
   python bot.py
   ```
   Leave it running in a terminal (or use `nohup`/`screen`/`tmux` to keep it
   alive after closing the terminal). It checks every 1 minute by default —
   change `POLL_INTERVAL_SECONDS` in `config.py` to adjust.

   **Note:** every time you start the bot it's a fresh start — the first
   check notifies about every listing currently matching your watches, same
   as if it had never seen any of them. Expect a burst of messages right
   after startup. After that first check, a listing already notified about
   won't be re-sent unless its price changes. Nothing is saved to disk, so
   restarting the bot resets this and you'll get that same burst again.

## Running it for free on GitHub Actions (24/7, no server needed)

This repo includes `.github/workflows/bot.yml`, which runs the bot
continuously on GitHub's free Actions runners.

### 1. Push this repo to GitHub

Use a **public** repository. GitHub Actions minutes are unlimited/free for
public repos; private repos only get 2,000 free minutes/month, which a
24/7 bot burns through in a couple of days. Your secrets (see below) stay
hidden regardless of the repo being public — GitHub encrypts them and
never shows their values in code, logs, or workflow files, even to you
after you save them.

### 2. Add three repository secrets

Go to **Settings -> Secrets and variables -> Actions -> New repository
secret** and add:

| Secret name          | Value                                  |
|-----------------------|-----------------------------------------|
| `TELEGRAM_BOT_TOKEN`  | your bot's token, from @BotFather       |
| `TELEGRAM_CHAT_ID`    | the chat ID to notify                   |
| `MARKETAPP_API_KEY`   | your key from marketapp.org/api-token   |

These three are all the workflow needs — everything else (`WATCHES`,
`POLL_INTERVAL_SECONDS`, thresholds, etc.) lives in `config.py`, which is
plain code, not a secret.

### 3. Start it

Push to GitHub, then go to the **Actions** tab -> **Marketapp Watcher** ->
**Run workflow** to kick off the first run manually (the scheduled trigger
only relaunches it periodically — see below — it won't auto-start on push).
After that, it keeps itself alive indefinitely; see "How the 1-minute
polling actually works" below.

### How the 1-minute polling actually works (and the 5-minute limit)

GitHub's `schedule:` (cron) trigger has a hard floor of 5 minutes — it will
not start a new job any more often than that, no matter what cron
expression you give it. There's no way around that specific limit, and this
workflow doesn't try to.

What it does instead: `bot.py` already has its own infinite loop
(`while True: check_watches(); time.sleep(POLL_INTERVAL_SECONDS)`) — once a
single job/workflow run starts, that loop polls every 60 seconds for as
long as *that one job* keeps running, completely independent of cron. The
5-minute floor only throttles how often GitHub will *start a new job* — it
says nothing about what a job does internally once it's alive. So a job
that loops on its own sidesteps the floor entirely, because it's not
relying on repeated cron triggers to get per-minute checks in the first
place.

The only real constraint left is that GitHub caps **any single job** at 6
hours of execution. So the workflow's `schedule: cron: "0 */5 * * *"` isn't
there to drive the 1-minute polling — it's there to relaunch a *fresh* run
every 5 hours, comfortably before the current one hits that 6-hour wall.
`concurrency: cancel-in-progress: true` makes the new run immediately
cancel whichever old run is still going, so there's a clean handoff and
you never get two bots running (and double-notifying) at once.

One side effect worth knowing: each relaunch is a fresh process, so (per
the "fresh start" behavior above) you'll get a notification burst about
every ~5h50m when the job restarts, same as restarting it locally.

### Keeping it alive long-term

GitHub auto-disables scheduled workflows on a repo that's had zero
activity for 60 days. As long as the bot is actually running (which counts
as activity), this shouldn't come up — but if you ever pause it for a
while, you may need to re-enable it manually from the Actions tab.

## How it works

- `bot.py` — main loop, checks each watch on a timer; the first check each
  run notifies about everything matching, later checks only notify about
  new matches or ones whose price changed since last notified (tracked
  in memory only, reset on restart)
- `marketapp_client.py` — queries marketapp.org's `/v1/gifts/onsale/` endpoint
- `list_collections.py` — one-off helper to look up collection addresses
- `notifier.py` — sends Telegram messages via your bot
- `config.py` — all your settings, filters, and watch definitions (reads
  secrets from environment variables, doesn't store them)
- `.github/workflows/bot.yml` — runs the bot continuously on GitHub Actions

## Notes

- Prices from the API are in nanoGRAM (like nanoTON); the bot converts them
  to GRAM automatically for the notification message.
- The API is currently free (per marketapp.org's docs) but may change in
  the future — worth keeping an eye on https://t.me/marketapp_api.
- Treat your `MARKETAPP_API_KEY` and `TELEGRAM_BOT_TOKEN` like passwords —
  never hardcode them in `config.py` or commit them; they belong in
  environment variables locally and in GitHub Secrets on Actions.
