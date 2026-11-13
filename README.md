# 1st Quarter

**Model status: experimental baseline.** The baseline estimates are not proven profitable; NBA historical-horizon and shot-opportunity research has been backtested, but prospective betting-edge validation is still required. Do not treat them as validated picks.

An automated first-quarter player projection bot for upcoming NBA and NFL games. It reads public ESPN schedules and game summaries, calculates each player's recent first-quarter production, and writes a Markdown report plus machine-readable JSON.

## Where to find predictions

The latest run writes [predictions.md](predictions.md) and [predictions.json](predictions.json) in this repository. On the development branch, GitHub Actions polls every 15 minutes, while each game is only scored in its own roughly T-30 minute primary window and T-15 retry window (if not previously verified) and grades finalized games once daily. It can also be started manually from the **Actions** tab using **Run workflow**. The report is also added to the run summary.

## Run locally

Requires Python 3.11 or newer. No third-party packages or API keys are needed.

```bash
python quarterbot.py
python quarterbot.py --sport nba --date 2026-11-13 --days 1
python quarterbot.py --sport nfl --games 8 --output nfl-predictions.md
python -m unittest discover -s tests -v
```

`--date` defaults to today's date in New York. Game times in the Markdown report are shown in Eastern time (EST or EDT); JSON includes both the UTC `date` and local `date_et`. `--days` controls how many calendar dates to scan, and `--games` controls the number of prior team games sampled. The published baseline remains experimental. NBA players appear only when there is sufficient historical data, the player is eligible, and both teams have verified starting fives. Estimates are recency-weighted averages. The displayed range uses observed variation, widened for small samples. It is **not** a probability or a guarantee.

The report includes games that have not started at generation time. Once a game starts, it is excluded because a pregame first-quarter projection is no longer timely. Before showing a player, the bot checks the current ESPN team roster and game injury list. NFL player projections also require an NFL.com official game-day inactive report for both teams; if that report is missing or cannot be parsed, the game is withheld. The bot removes officially inactive players and, for NFL passing yards, shows only the team's lead passer from its most recent game. These checks do not confirm tonight's starter or snap count.

## Data limits

- ESPN is an unofficial public data source for this bot; feed fields, rate limits, and availability can change.
- ESPN's roster and injury feeds can lag. The bot does not confirm game-day inactive lists, starting lineups, minutes, snap counts, or betting lines. Check those before relying on a projection.
- NFL yards are parsed from play descriptions. Unusual plays and stat corrections can cause differences from official first-quarter box scores.
- A missing player is not a prediction of zero. A game with too little usable data is labeled as such.
- The bot does not place bets or calculate a betting edge.

The workflow grants `contents: write` only so it can update the two report files. It does not require stored secrets.

## Verification and validation upgrade (development branch)

The system is **NOT a validated or profitable betting system**. The current
six-game recency-weighted calculation remains the published baseline. This
branch adds safeguards and a separate experimental challenger:

- **NBA:** publish player projections only when NBA.com's daily-lineups feed
  supplies five individually **Confirmed** starters for **both** scheduled
  teams; then include confirmed starters only. An active roster and an
  **Expected** lineup are not sufficient. If the feed is blocked or changed,
  withhold predictions rather than silently pretending confirmation.
- **NFL:** retain the official NFL.com inactive gate. This still does not
  prove the starting quarterback or individual snap counts.
- **Timing:** only process games inside the next two hours; hold projections
  if fewer than two minutes remain before scheduled tipoff/kickoff. GitHub
  Actions polls every 15 minutes during likely US game windows. Schedules
  can be delayed by GitHub, so publication is not guaranteed.
- **Archive:** every eligible pregame run writes an immutable record under
  `archive/YYYY-MM-DD/`. Games are never archived retroactively.
- **Grading:** `python quarter_grade.py` checks completed ESPN game
  summaries, takes the latest valid pregame snapshot per matchup, and
  appends per-player errors and market-level MAE/bias/range coverage in
  `grading/rows.csv` and `grading/summary.json`. Missing player data
  is not assumed to equal zero. NBA first-quarter points are graded only
  when play-by-play totals reconcile with official team Q1 points.
- **Shadow challenger:** a separate NBA estimate uses historical
  first-quarter production specifically from games that the player started,
  shrunk toward the current baseline. It is logged as
  `shadow_role_estimate` and is **not** used to rank, publish, or
  place bets. The grader calculates paired errors for the two estimates.

### What still has to be proven

The NBA.com lineup feed's live response shape and accessibility must be
confirmed on a real pregame GitHub Actions run; synthetic parser tests are
not enough. The historical first-quarter event parser also requires
independent reconciliation and error monitoring. No pre-existing bet prices,
closing lines, calibrated hit probabilities, transaction costs, or net
returns are recorded, so **nothing in the grade ledger constitutes a
winning betting track record**.

Only promote a challenger after a predeclared, chronologically held-out
test across multiple game dates and sufficient samples per market, with
improved paired error/calibration and no unacceptable deterioration in
high-confidence picks. Positive net expected value against real bettable
prices after vig must be shown separately. Do not select by the best
backtested-looking subset.

The branch's new workflow `grade.yml` grades once per day. Confirm that
GitHub Actions are enabled before relying on the automation.

## November 13, 2026 — NBA history-window validation

The historical research branch compared identical player-game predictions
under six versus ten previous NBA team games in both 2025 and 2026.
The ten-game horizon had lower first-quarter points, rebounds and assists
mean absolute errors on matched retrospective samples in both years.
Consequently the NBA baseline now uses **10 recent games**, while NFL remains
at **6** (NFL was not part of the NBA evaluation). You can override those
with `--nba-games N` and `--games N` respectively. The original weighted
estimator is unchanged.

A separate `shadow_shot_estimate` is archived for NBA point projections;
it has **no influence on published predictions**. Its forward-only grade
and game-cluster confidence interval are tracked by the nightly grader.

Do not confuse retrospective improvements with betting profit: these
research comparisons used the final-game box score to identify starters.
The live workflow only publishes when both NBA starting fives have been
confirmed before the game's start. Real betting-market prices, odds and
net ROI are still required before this can be labeled a winning system.

## November 13: game-relative automatic runs

Each NBA or NFL game now has its **own** schedule based on the start time
reported by ESPN rather than a fixed 7 PM slate trigger:

- **Primary check:** when 20–35 minutes remain before scheduled tipoff or kickoff
  (nominally T−30). Fetch recent stats, verify injuries and availability,
  require five `Confirmed` NBA starters per team, and publish only if checks pass.
- **Retry check:** when more than 2 but fewer than 20 minutes remain
  (nominally T−15). Only games that have **not** published verified
  predictions are recomputed; missing lineup confirmation gets another attempt.
- **Protected cutoff:** new predictions cannot publish inside two minutes
  of scheduled kickoff, even if GitHub Actions or network calls were delayed.
- **Already verified:** retain the original confirmed T−30 snapshot in
  the latest report without generating a false fresh prediction at T−15.
- **Per-game independence:** a 7:00 PM matchup and an 8:30 PM matchup
  can be checked at different times without overwriting each other's
  valid verified reports. Roster/injury and official NFL inactive gates
  remain unchanged.

The workflow now runs a lightweight scoreboard preflight every fifteen
minutes at minutes 02, 17, 32 and 47 UTC, covering all hours and days.
Only when a game is due, a previously published game has begun, or
an authorized manual/push run occurs does it run the full model and
write predictions. The offset reduces top-of-hour cron congestion.

**Operational caveat:** GitHub Actions cron is best-effort; GitHub may
delay or drop scheduled jobs. This setup targets approximately T−30/T−15
and does not guarantee an exact wall-clock trigger. Verify real
`schedule` event executions under Actions before relying on it to
automatically publish on game day. Use `workflow_dispatch` manually
if GitHub's scheduler is disabled or delayed.
