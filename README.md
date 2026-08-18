# FIBA Tournament Dashboard

Self-updating dashboard for FIBA competitions, built for Canada Basketball.
Pure Python — no R.

**Live dashboard:** https://jordanngo205.github.io/fiba-tournament-dashboard/

Currently tracking the **FIBA Women's Olympic Pre-Qualifying Tournament 2026**
(Guadalajara, Mexico — 17–23 Aug 2026).

## How it works

No URLs are entered by hand anywhere. The scraper matches a tournament by name
against FIBA's own event index, then walks down to the games:

```
--event "olympic pre-qualifying guadalajara"
   → /en/events              event index, matched by name → slug
   → /en/events/<slug>/games full schedule, final games only
   → /games/<id>-<A>-<B>     box scores + play-by-play
   → CSVs → dashboard_template.html → docs/index.html
```

Every page's data comes out of the Next.js hydration payload that
fiba.basketball ships inside its HTML.

A GitHub Action re-runs this every hour, scrapes any game that has gone
final since the last run, rebuilds `docs/index.html`, and pushes. GitHub Pages
serves that file, so the public link updates itself.

## Usage

```bash
pip install -r requirements.txt

python3 fiba_scrape.py --list women olympic        # browse events
python3 fiba_scrape.py --event olympic pre-qualifying guadalajara \
                       --qualify-spots 2 \
                       --name "Olympic Pre-Qualifying 2026"

# stay running until every game is final
python3 fiba_scrape.py --event guadalajara --watch 15
```

Re-runs are incremental — games already in the CSVs are skipped.

Only games FIBA has marked final are scraped (`gameStatisticStatusCode == VALID`
and not `isLive`), because a game in progress has no complete box score.

## Outputs

Written to `<Competition>/data/`:

| File | Contents |
|---|---|
| `game details` | one row per game: teams, scores, round, venue |
| `player box scores` | per-player traditional box |
| `team box scores` | per-team box, with opponent columns joined on |
| `team adv box scores` | possessions, ORTG, DRTG, eFG%, TO/poss, DREB rate, AST/FG% |
| `pbp` | play-by-play with shot zones, distances, and seconds elapsed |
| `standings` | W/L and point differential per team per game |
| `player enriched` | box + PBP-derived stats + offensive/defensive net points |
| `daily awards` | 20 per-day superlatives |
| `participant log` | rosters |

## Dashboard template

`dashboard_template.html` holds all the layout and styling. The scraper splices
data between the `// %%DATA_START%%` and `// %%DATA_END%%` markers, emitting
`GAME_DETAILS`, `ADV`, `PLAYER_DATA`, `QUALIFIERS`, `QUALIFY_SPOTS`,
`GENERATED_AT` and `FLAG_MAP`.

Because those markers are re-emitted into the output, **any generated dashboard
is itself a valid template** — copy one over `dashboard_template.html` to reuse
its design.

Add a country to `FLAG_MAP` in `fiba_scrape.py` when a new team appears.

## Legacy

`legacy/` holds archived U17 World Cup data — the reference tournament used to
validate this scraper against the Basketball Canada R pipeline it replaces
(team advanced box, enriched player table and standings matched exactly, with
one deliberate difference: `FTP` is computed as `FTM/FTA` rather than the R
script's `FTM/FGA`). Those R scripts have been removed; the pipeline is
entirely Python. They remain in git history at commit `25c954b`.
