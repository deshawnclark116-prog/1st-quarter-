# 1st Quarter

An automated first-quarter player projection bot for upcoming NBA and NFL games. It reads public ESPN schedules and game summaries, calculates each player's recent first-quarter production, and writes a Markdown report plus machine-readable JSON.

## Where to find predictions

The latest run writes [predictions.md](predictions.md) and [predictions.json](predictions.json) in this repository. GitHub Actions runs daily at 14:00 and 00:00 UTC, plus Sunday at 16:30 UTC to refresh NFL availability closer to kickoff. It can also be started manually from the **Actions** tab using **Run workflow**. The report is also added to the run summary.

## Run locally

Requires Python 3.11 or newer. No third-party packages or API keys are needed.

```bash
python quarterbot.py
python quarterbot.py --sport nba --date 2026-11-13 --days 1
python quarterbot.py --sport nfl --games 8 --output nfl-predictions.md
python -m unittest discover -s tests -v
```

`--date` defaults to today's date in New York. Game times in the Markdown report are shown in Eastern time (EST or EDT); JSON includes both the UTC `date` and local `date_et`. `--days` controls how many calendar dates to scan, and `--games` controls the number of prior team games sampled. A player appears only when their most recent game roster includes them and they have at least three first-quarter samples. Estimates are recency-weighted averages. The displayed range uses observed variation, widened for small samples. It is **not** a probability or a guarantee.

The report includes games that have not started at generation time. Once a game starts, it is excluded because a pregame first-quarter projection is no longer timely. Before showing a player, the bot checks the current ESPN team roster and game injury list. It removes players marked Out or Doubtful, and, for NFL passing yards, shows only the team's lead passer from its most recent game. These checks do not confirm game-day participation or tonight's starter.

## Data limits

- ESPN is an unofficial public data source for this bot; feed fields, rate limits, and availability can change.
- ESPN's roster and injury feeds can lag. The bot does not confirm game-day inactive lists, starting lineups, minutes, snap counts, or betting lines. Check those before relying on a projection.
- NFL yards are parsed from play descriptions. Unusual plays and stat corrections can cause differences from official first-quarter box scores.
- A missing player is not a prediction of zero. A game with too little usable data is labeled as such.
- The bot does not place bets or calculate a betting edge.

The workflow grants `contents: write` only so it can update the two report files. It does not require stored secrets.
