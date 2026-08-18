# Legacy

The original Basketball Canada R pipeline and the U17 World Cup data it was
first pointed at. Kept for reference — the live Olympic Pre-Qualifying
dashboard is built by `fiba_scrape.py` at the repo root, and nothing here runs
in CI.

- `FIBA EVENT GAME SCRAPE (For Sharing).R` — the original scraper
  (contact: Cohen MacDonald, cmacdonald@basketball.ca), still working, with
  the hardcoded game-URL list replaced by auto-discovery
- `getGameLinks.R` — pulls a competition's schedule off its event page
- `loadPackages.R` — package bootstrap
- `U17 World Cup Qualifying (Brno, Czechia)/` — scraped data and dashboard for
  the FIBA U17 Women's Basketball World Cup 2026, used to validate the Python
  port (team advanced box, enriched player table and standings match exactly)

Run it from inside this folder, so the relative `source()` calls resolve:

```r
setwd("legacy"); source("FIBA EVENT GAME SCRAPE (For Sharing).R")
```
