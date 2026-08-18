#!/usr/bin/env python3
"""
FIBA competition scraper and dashboard builder.

Pick a tournament by name. No URLs to paste anywhere:

    python3 fiba_scrape.py --list                     # browse every event FIBA lists
    python3 fiba_scrape.py --list women olympic       # filter that list
    python3 fiba_scrape.py --event guadalajara        # scrape the matching event
    python3 fiba_scrape.py --event guadalajara --watch 15   # until every game is final

Writes box scores, play-by-play and derived tables to "<Competition>/data/",
then splices the data into dashboard_template.html and publishes the result to
docs/index.html for GitHub Pages.

Re-runs are incremental: games already in the CSVs are skipped, so this can run
on a schedule through a tournament and only fetch what is new. Only games FIBA
has marked final are scraped, since a game in progress has no complete box.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

BASE = "https://www.fiba.basketball"
EVENTS_URL = f"{BASE}/en/events"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
UNDEFINED = "$undefined"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})


# ---------------------------------------------------------------------------
# Fetching + payload extraction
# ---------------------------------------------------------------------------

def fetch(url: str, tries: int = 5, pause: float = 3.0) -> str:
    """GET a page, retrying on the transport errors fiba.basketball throws."""
    last = None
    for attempt in range(1, tries + 1):
        try:
            r = SESSION.get(url, timeout=60)
            r.raise_for_status()
            return r.text
        except Exception as exc:  # noqa: BLE001 - retry anything transport-level
            last = exc
            if attempt < tries:
                print(f"  retry {attempt}/{tries} — {type(exc).__name__}: {exc}")
                time.sleep(pause * attempt)
    raise RuntimeError(f"Failed to fetch after {tries} attempts: {url}") from last


def rsc_payloads(html: str):
    """
    Yield the JSON payloads embedded in the page.

    fiba.basketball is a Next.js app that ships its data inside
    `self.__next_f.push([1, "<chunk-id>:<json>"])` script tags. The R script
    slices fixed character offsets off the front; the chunk ids vary in length
    between pages, so strip them with a pattern instead.
    """
    soup = BeautifulSoup(html, "html.parser")
    scripts = sorted((s.get_text() for s in soup.find_all("script")), key=len, reverse=True)
    prefix = "self.__next_f.push("
    for text in scripts:
        if not text.startswith(prefix):
            continue
        try:
            outer = json.loads(text[len(prefix):-1])
        except Exception:
            continue
        if not (isinstance(outer, list) and len(outer) > 1 and isinstance(outer[1], str)):
            continue
        body = re.sub(r"^[0-9a-fA-F]+:", "", outer[1])   # drop the chunk id
        body = re.sub(r"^[A-Za-z]?(?=[\[{])", "", body)  # drop a type tag if present
        try:
            yield json.loads(body)
        except Exception:
            continue


def find_dicts(obj, required: set[str], limit: int | None = None, depth: int = 0) -> list[dict]:
    """Collect every dict in a nested structure that has all of `required` keys."""
    found: list[dict] = []

    def walk(node, d):
        if d > 30 or (limit is not None and len(found) >= limit):
            return
        if isinstance(node, dict):
            if required <= node.keys():
                found.append(node)
            for value in node.values():
                walk(value, d + 1)
        elif isinstance(node, list):
            for value in node:
                walk(value, d + 1)

    walk(obj, depth)
    return found


def clean(value):
    """Next.js writes absent values as the literal string '$undefined'."""
    if value == UNDEFINED:
        return None
    return value


# ---------------------------------------------------------------------------
# Event discovery — this is what removes the manual URL step
# ---------------------------------------------------------------------------

def list_events() -> pd.DataFrame:
    """Every event FIBA publishes, with the slug needed to reach its games."""
    html = fetch(EVENTS_URL)
    for payload in rsc_payloads(html):
        raw = find_dicts(payload, {"slug", "fibaOfficialName"})
        if not raw:
            continue
        rows = {}
        for e in raw:
            slug = clean(e.get("slug"))
            name = clean(e.get("fibaOfficialName")) or clean(e.get("title"))
            if not slug or not name or slug in rows:
                continue
            hosts = clean(e.get("fibaHostJson")) or []
            host = ""
            if isinstance(hosts, list) and hosts:
                cities = hosts[0].get("cities") or []
                city = cities[0].get("name", "") if cities else ""
                country = hosts[0].get("countryName", "") or ""
                host = ", ".join(p for p in (city, country) if p)
            rows[slug] = {
                "slug": slug,
                "name": name,
                "start": (clean(e.get("eventDateStart")) or "")[:10],
                "end": (clean(e.get("eventDateEnd")) or "")[:10],
                "host": host,
                "discipline": clean(e.get("fibaSource")) or "",
                "gender": clean(e.get("gender")) or clean(e.get("fibaGender")) or "",
            }
        if rows:
            return pd.DataFrame(rows.values()).sort_values("start").reset_index(drop=True)
    raise RuntimeError("Could not read the event index — the page structure may have changed.")


def resolve_event(query: str, events: pd.DataFrame) -> pd.Series:
    """Match a tournament by name fragments, e.g. 'women olympic guadalajara'."""
    tokens = [t for t in re.split(r"\s+", query.strip().lower()) if t]
    haystack = (events["name"] + " " + events["slug"] + " " + events["host"]).str.lower()
    hits = events[haystack.apply(lambda h: all(t in h for t in tokens))]

    if hits.empty:
        raise SystemExit(
            f"No event matched {query!r}.\n"
            f"Run  python3 {Path(__file__).name} --list  to see what is available."
        )
    if len(hits) > 1:
        # Prefer an unambiguous winner if one name is clearly the shortest match.
        print(f"{len(hits)} events matched {query!r}:\n")
        for _, r in hits.iterrows():
            print(f"  {r['start']}  {r['name']}  [{r['host']}]")
        raise SystemExit("\nNarrow the --event text until one event matches.")
    return hits.iloc[0]


# ---------------------------------------------------------------------------
# Schedule discovery
# ---------------------------------------------------------------------------

def event_games(slug: str, played_only: bool = True) -> tuple[pd.DataFrame, int]:
    """The event's full schedule, as game rows with a built game URL."""
    url = f"{BASE}/en/events/{slug}/games"
    print(f"Reading schedule from {url}")
    html = fetch(url)

    games = []
    for payload in rsc_payloads(html):
        found = find_dicts(payload, {"gameId", "teamA", "teamB"})
        if found:
            games = found
            break
    if not games:
        raise RuntimeError(f"Could not find the games list on {url}")

    rows = {}
    for g in games:
        gid = clean(g.get("gameId"))
        if gid is None or gid in rows:
            continue
        team_a = clean(g.get("teamA")) or {}
        team_b = clean(g.get("teamB")) or {}
        rows[gid] = {
            "gameId": gid,
            "home": clean(team_a.get("code")),
            "away": clean(team_b.get("code")),
            "home_score": clean(g.get("teamAScore")) or 0,
            "away_score": clean(g.get("teamBScore")) or 0,
            "date": (clean(g.get("gameDateTime")) or "")[:10],
            "round": (clean(g.get("round")) or {}).get("roundName", ""),
            "stat_status": clean(g.get("gameStatisticStatusCode")),
            "is_live": bool(clean(g.get("isLive"))),
        }

    sched = pd.DataFrame(rows.values())
    # The R script's header warns you to wait until games are FINAL, because a
    # game that has not finished has no usable box score. Enforce that here:
    # gameStatisticStatusCode flips EMPTY -> VALID once stats exist, isLive
    # marks a game in progress, and a knockout slot has no team code until the
    # bracket fills in. A score check alone is not enough — a live game shows a
    # running score, and scraping it would freeze partial stats into the CSVs.
    sched["played"] = (
        sched["home"].notna()
        & sched["away"].notna()
        & (sched["home"].astype(str) != "")
        & (sched["away"].astype(str) != "")
        & (sched["stat_status"] == "VALID")
        & ~sched["is_live"]
    )

    pending = int((~sched["played"]).sum())
    live = int((sched["is_live"] & ~sched["played"]).sum())
    if played_only:
        sched = sched[sched["played"]]
        if pending:
            note = f" ({live} in progress right now)" if live else ""
            print(f"Skipping {pending} game(s) not yet final{note}.")

    sched = sched.sort_values(["date", "gameId"]).reset_index(drop=True)
    sched["game_link"] = [
        f"{BASE}/en/events/{slug}/games/{r.gameId}-{r.home}-{r.away}"
        for r in sched.itertuples()
    ]
    print(f"Found {len(sched)} playable game(s) across {sched['round'].nunique()} round(s).")
    return sched, pending


# ---------------------------------------------------------------------------
# Per-game scraping
# ---------------------------------------------------------------------------

PLAYER_BOX_COLS = [
    "PM", "Starter", "AS", "BS", "DR", "FD", "FG2A", "FG2M", "FG2P", "FG3A",
    "FG3M", "FG3P", "FGA", "FGM", "FTA", "FTM", "FTP", "OR", "PF", "PTS",
    "REB", "ST", "TO", "EFF", "FGIA", "FGIM", "FGIP", "TP",
]


def team_stat_slice(stats: dict) -> dict:
    """
    Mirror the R `select(AS:TO, -Leaders)` — a positional range over the team
    stat block, minus the nested Leaders object.
    """
    keys = list(stats.keys())
    try:
        lo, hi = keys.index("AS"), keys.index("TO")
    except ValueError:
        return {k: clean(v) for k, v in stats.items() if k != "Leaders"}
    chosen = keys[min(lo, hi): max(lo, hi) + 1]
    return {k: clean(stats[k]) for k in chosen if k != "Leaders"}


def scrape_game(url: str) -> dict | None:
    """Pull game details, rosters, box scores and play-by-play from one game page."""
    html = fetch(url)
    node = None
    for payload in rsc_payloads(html):
        hits = find_dicts(payload, {"game", "playersTeamA", "gameDetails"}, limit=1)
        if hits:
            node = hits[0]
            break
    if node is None:
        print(f"  no game data found — skipping {url}")
        return None

    g = node["game"]
    team_a, team_b = g["teamA"], g["teamB"]
    details = {
        "gameId": g["gameId"],
        "date": (clean(g.get("gameDateTime")) or "")[:10],
        "home_team": team_a["officialName"],
        "home_short": team_a["code"],
        "home_id": team_a["organisationId"],
        "home_score": clean(node.get("teamAScore")),
        "away_team": team_b["officialName"],
        "away_short": team_b["code"],
        "away_id": team_b["organisationId"],
        "away_score": clean(node.get("teamBScore")),
        "country": clean(g.get("hostCountry")),
        "city": clean(g.get("hostCity")),
        "fibaZone": (clean(g.get("competition")) or {}).get("fibaZone"),
        "competition": (clean(g.get("competition")) or {}).get("officialName"),
        "round": (clean(g.get("round")) or {}).get("roundName"),
        # Group letter (A/B/…) for group-phase games. Knockout fixtures reuse
        # this field for a bracket slot number, so only keep real group codes.
        "group": None,
        "game_link": url,
    }
    pairing = clean(g.get("groupPairingCode"))
    if pairing and str(pairing).strip().isalpha():
        details["group"] = str(pairing).strip().upper()

    # --- rosters -----------------------------------------------------------
    competitors = []
    for players, nat, tid in (
        (node.get("playersTeamA") or [], details["home_team"], details["home_id"]),
        (node.get("playersTeamB") or [], details["away_team"], details["away_id"]),
    ):
        for p in players:
            num = clean(p.get("uniformNumber"))
            try:
                num = float(num)
            except (TypeError, ValueError):
                continue  # R drops players without a shirt number
            competitors.append({
                "pId": clean(p.get("personId")),
                "name": f"{clean(p.get('firstName')) or ''} {clean(p.get('lastName')) or ''}".strip(),
                "uniformNumber": num,
                "position": clean(p.get("position")),
                "nationality": nat,
                "teamId": tid,
                "gameId": details["gameId"],
            })
    competitors = pd.DataFrame(competitors)

    # --- box scores --------------------------------------------------------
    team_nodes = node["gameDetails"]["c"]
    box_rows, team_rows = [], []
    for idx, tnode in enumerate(team_nodes):
        for child in tnode.get("Children") or []:
            stats = child.get("Stats") or {}
            row = {"pId": pd.to_numeric(re.sub(r"^P_", "", str(child.get("Id"))), errors="coerce")}
            row.update({c: clean(stats.get(c)) for c in PLAYER_BOX_COLS})
            box_rows.append(row)

        side = "home" if idx == 0 else "away"
        team_rows.append({
            "gameId": details["gameId"],
            "nationality": details[f"{side}_team"],
            "teamId": details[f"{side}_id"],
            "shortCode": details[f"{side}_short"],
            "ID": idx + 1,
            **team_stat_slice(tnode.get("Stats") or {}),
        })

    box = pd.DataFrame(box_rows)
    if not box.empty and not competitors.empty:
        box = box.merge(competitors[["pId", "teamId", "nationality", "uniformNumber", "name"]],
                        on="pId", how="left")
        box.insert(0, "gameId", details["gameId"])
        competitors = competitors[competitors["name"].isin(box["name"])]

    # --- play-by-play ------------------------------------------------------
    pbp_rows = []
    for period_id, period in (node.get("playByPlay", {}).get("items") or {}).items():
        for ev in period.get("items") or []:
            row = {k: clean(v) for k, v in ev.items()}
            row["period"] = period_id
            row["gameId"] = details["gameId"]
            pbp_rows.append(row)
    pbp = pd.DataFrame(pbp_rows)
    if not pbp.empty and not competitors.empty:
        pbp["pId"] = pd.to_numeric(pbp.get("pId"), errors="coerce")
        pbp["oId"] = pd.to_numeric(pbp.get("oId"), errors="coerce")
        pbp = pbp.merge(
            competitors[["pId", "teamId", "name", "nationality", "uniformNumber"]],
            left_on=["pId", "oId"], right_on=["pId", "teamId"], how="left",
        )

    return {
        "details": pd.DataFrame([details]),
        "competitors": competitors,
        "box": box,
        "team_box": pd.DataFrame(team_rows),
        "pbp": pbp,
    }


# ---------------------------------------------------------------------------
# Derived tables (ports of the R transformations)
# ---------------------------------------------------------------------------

def build_player_box(box: pd.DataFrame) -> pd.DataFrame:
    df = box.drop_duplicates().copy()
    for c in ("PTS", "FGM", "FGA", "FG2M", "FG2A", "FG3M", "FG3A", "FTM", "FTA"):
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    df["10+ ppg"] = (df["PTS"] >= 10).astype(int)
    df["FGP"] = (df["FGM"] / df["FGA"]).round(3)
    df["FG2P"] = (df["FG2M"] / df["FG2A"]).round(3)
    df["FG3P"] = (df["FG3M"] / df["FG3A"]).round(3)
    # NOTE: the R script computes FTP as FTM/FGA, which looks like a slip.
    # Free-throw percentage is FTM/FTA, which is what is used here.
    df["FTP"] = (df["FTM"] / df["FTA"]).round(3)
    parts = df["TP"].astype(str).str.split(":", expand=True)
    df["mins"] = pd.to_numeric(parts[0], errors="coerce")
    df["secs"] = pd.to_numeric(parts[1], errors="coerce") if parts.shape[1] > 1 else 0
    df["MP"] = (df["mins"] + df["secs"] / 60).round(1)
    return df


def build_team_box(team_box: pd.DataFrame, player_box: pd.DataFrame) -> pd.DataFrame:
    calc = (player_box.groupby(["nationality", "teamId", "gameId"], as_index=False)
            [["10+ ppg", "MP"]].sum())
    calc["MP"] = calc["MP"].round(0)

    merged = team_box.merge(calc, on=["nationality", "teamId", "gameId"], how="left")

    opp = merged.copy()
    opp["ID"] = opp["ID"].map({1: 2, 2: 1})
    opp.columns = [f"opp_{c}" for c in opp.columns]

    out = merged.merge(opp, left_on=["gameId", "ID"], right_on=["opp_gameId", "opp_ID"], how="left")
    return out.drop_duplicates()


def build_team_adv(team_box: pd.DataFrame) -> pd.DataFrame:
    d = team_box.copy()
    num = ["FGA", "FTA", "OR", "DR", "FGM", "TO", "PTS", "AS", "FG3M",
           "opp_FGA", "opp_FTA", "opp_OR", "opp_DR", "opp_FGM", "opp_TO", "opp_PTS"]
    for c in num:
        d[c] = pd.to_numeric(d.get(c), errors="coerce")

    d["Possessions"] = 0.5 * (
        (d["FGA"] + 0.4 * d["FTA"] - 1.07 * (d["OR"] / (d["OR"] + d["opp_DR"])) * (d["FGA"] - d["FGM"]) + d["TO"])
        + (d["opp_FGA"] + 0.4 * d["opp_FTA"] - 1.07 * (d["opp_OR"] / (d["opp_OR"] + d["DR"])) * (d["opp_FGA"] - d["opp_FGM"]) + d["opp_TO"])
    )
    d["ORTG"] = (100 * d["PTS"] / d["Possessions"]).round(1)
    d["DRTG"] = (100 * d["opp_PTS"] / d["Possessions"]).round(1)
    d["EFG%"] = (100 * (d["FGM"] + 0.5 * d["FG3M"]) / d["FGA"]).round(1)
    d["TO/Poss"] = (d["TO"] / d["Possessions"] * 100).round(1)
    d["DRB rt"] = (100 * d["DR"] / (d["DR"] + d["opp_OR"])).round(1)
    d["AST/FG%"] = (100 * d["AS"] / d["FGM"]).round(1)

    cols = ["gameId", "nationality", "teamId", "shortCode", "PTS", "Possessions",
            "ORTG", "10+ ppg", "DRTG", "EFG%", "TO/Poss", "DRB rt", "AST/FG%"]
    return d[[c for c in cols if c in d.columns]]


PERIOD_OFFSETS = {"Q1": 0, "Q2": 600, "Q3": 1200, "Q4": 1800,
                  "OT1": 2400, "OT2": 2700, "OT3": 3000, "OT4": 3300}


def enrich_pbp(pbp: pd.DataFrame, short_code_ref: pd.DataFrame) -> pd.DataFrame:
    d = pbp.copy()
    d["Code"] = d["period"].astype(str)
    d["x"] = pd.to_numeric(d.get("x"), errors="coerce")
    d["y"] = pd.to_numeric(d.get("y"), errors="coerce")

    ac, x, y = d.get("ac"), d["x"], d["y"]
    zone = pd.Series(pd.NA, index=d.index, dtype="object")
    zone[(ac == "P3") & (x > 200) & (y < 75)] = "Left Corner 3"
    zone[(ac == "P3") & (x < 20) & (y < 75)] = "Right Corner 3"
    zone[(ac == "P3") & zone.isna()] = "Above the Break 3"
    zone[(ac == "P2") & x.between(115, 165) & (y <= 50)] = "Restricted Area"
    zone[(ac == "P2") & zone.isna() & (y <= 100) & x.between(95, 185)] = "In The Paint (Non-RA)"
    zone[(ac == "P2") & zone.isna()] = "Mid-Range"
    d["zoneBasic"] = zone

    d["distanceShot"] = (((140 - x) ** 2 + (40 - y) ** 2) ** 0.5 / 10).round(1)
    dist = d["distanceShot"]
    rng = pd.Series(pd.NA, index=d.index, dtype="object")
    rng[(dist >= 22) & (d["zoneBasic"] != "Backcourt")] = "22+ ft."
    rng[dist.between(16, 22, inclusive="left")] = "16-22 ft."
    rng[dist.between(8, 16, inclusive="left")] = "8-16 ft."
    rng[dist < 8] = "Less Than 8 ft."
    d["zoneRange"] = rng

    parts = d["Time"].astype(str).str.split(":", expand=True)
    mins = pd.to_numeric(parts[0], errors="coerce")
    secs = pd.to_numeric(parts[1], errors="coerce") if parts.shape[1] > 1 else 0
    remaining = mins * 60 + secs
    length = d["Code"].map(lambda c: 300 if str(c).startswith("OT") else 600)
    d["seconds_elapsed"] = d["Code"].map(PERIOD_OFFSETS) + (length - remaining)

    if "nationality" in d.columns and not short_code_ref.empty:
        d = d.merge(short_code_ref, on="nationality", how="left")
    return d


def build_standings(details: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for side, opp in (("home", "away"), ("away", "home")):
        f = details[["gameId", "date", "fibaZone", "competition", "round",
                     f"{side}_team", f"{side}_short", f"{side}_score", f"{opp}_score"]].copy()
        f.columns = ["gameId", "date", "fibaZone", "competition", "round",
                     "team", "team_short", "score", "opp_score"]
        f["Win"] = (f["score"] > f["opp_score"]).astype(int)
        f["Loss"] = (f["score"] < f["opp_score"]).astype(int)
        f["Differential"] = f["score"] - f["opp_score"]
        frames.append(f.drop(columns=["score", "opp_score"]))
    return pd.concat(frames).sort_values("gameId").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Dashboard-prep: enriched player table + daily awards
# ---------------------------------------------------------------------------

def build_enriched(player_box, details, team_adv, pbp) -> pd.DataFrame:
    id_to_date = details[["gameId", "date"]].drop_duplicates()
    short_ref = pd.concat([
        details[["gameId", "home_team", "home_short"]].rename(
            columns={"home_team": "nationality", "home_short": "shortCode"}),
        details[["gameId", "away_team", "away_short"]].rename(
            columns={"away_team": "nationality", "away_short": "shortCode"}),
    ]).drop_duplicates()

    result = pd.concat([
        details.assign(shortCode=details["home_short"],
                       wl_diff=details["home_score"] - details["away_score"])[["gameId", "shortCode", "wl_diff"]],
        details.assign(shortCode=details["away_short"],
                       wl_diff=details["away_score"] - details["home_score"])[["gameId", "shortCode", "wl_diff"]],
    ])
    ortg = team_adv.drop_duplicates(["gameId", "shortCode"])[["gameId", "shortCode", "ORTG", "DRTG"]]

    df = player_box[pd.to_numeric(player_box["MP"], errors="coerce") >= 2].copy()
    df = (df.merge(id_to_date, on="gameId", how="left")
            .merge(short_ref, on=["gameId", "nationality"], how="left")
            .merge(result, on=["gameId", "shortCode"], how="left")
            .merge(ortg, on=["gameId", "shortCode"], how="left"))

    df["PM"] = pd.to_numeric(df["PM"], errors="coerce")
    off_delta = df["ORTG"] - 100
    def_delta = 100 - df["DRTG"]
    total = off_delta.abs() + def_delta.abs()
    off_frac = (off_delta.abs() / total).where(total > 0, 0.5)
    df["off_net"] = (df["PM"] * off_frac).round(1)
    df["def_net"] = (df["PM"] * (1 - off_frac)).round(1)
    df["WL"] = df["wl_diff"].apply(lambda v: f"+{v}" if pd.notna(v) and v >= 0 else str(v))
    df["stocks"] = pd.to_numeric(df["ST"], errors="coerce") + pd.to_numeric(df["BS"], errors="coerce")

    # PBP-derived per-player extras
    if not pbp.empty:
        shots = pbp[(pbp.get("ac").isin(["P2", "P3"])) & (pbp.get("made").astype(str).str.upper() == "TRUE")].copy()
        shots["pts"] = pd.to_numeric(shots.get("pts"), errors="coerce").fillna(0)
        shots["corner3m"] = shots["zoneBasic"].isin(["Left Corner 3", "Right Corner 3"]).astype(int)
        shots["abovebk3m"] = (shots["zoneBasic"] == "Above the Break 3").astype(int)
        shots["rim_makes"] = ((shots["zoneBasic"] == "Restricted Area") & (shots["ac"] == "P2")).astype(int)
        shots["midrange_makes"] = (shots["zoneBasic"] == "Mid-Range").astype(int)
        shots["fb_pts"] = shots["pts"].where(
            shots.get("txt", "").astype(str).str.lower().str.contains("fast"), 0).astype(int)
        agg = (shots.groupby(["gameId", "name", "shortCode"], as_index=False)
               [["corner3m", "abovebk3m", "rim_makes", "midrange_makes", "fb_pts"]].sum())
        df = df.merge(agg, on=["gameId", "name", "shortCode"], how="left")

        putbacks = compute_putbacks(pbp)
        df = df.merge(putbacks, on=["gameId", "name", "shortCode"], how="left")

    for c in ("corner3m", "abovebk3m", "rim_makes", "midrange_makes", "fb_pts", "putback_pts"):
        df[c] = pd.to_numeric(df.get(c), errors="coerce").fillna(0).astype(int)

    keep = ["date", "gameId", "name", "shortCode", "nationality", "PTS", "REB", "OR", "DR",
            "AS", "ST", "BS", "PF", "FD", "FTM", "FGA", "FGM", "FG3M", "TO", "MP", "PM",
            "off_net", "def_net", "stocks", "Starter", "WL", "wl_diff", "corner3m",
            "abovebk3m", "rim_makes", "midrange_makes", "fb_pts", "putback_pts"]
    out = df[[c for c in keep if c in df.columns]].rename(
        columns={"shortCode": "team", "Starter": "starter", "wl_diff": "WL_raw"})
    return out


def compute_putbacks(pbp: pd.DataFrame) -> pd.DataFrame:
    """Offensive rebound followed by a make from the same player within 3 events."""
    d = pbp[pbp.get("ac").isin(["REB", "P2", "P3"])].copy()
    if d.empty:
        return pd.DataFrame(columns=["gameId", "name", "shortCode", "putback_pts"])
    d["row_n"] = d.groupby("gameId").cumcount()

    orbs = d[(d["ac"] == "REB")
             & d.get("txt", "").astype(str).str.lower().str.contains("offensive")
             & d["pId"].notna()][["gameId", "row_n", "pId", "name", "shortCode"]]
    orbs = orbs.rename(columns={"row_n": "orb_row"})

    makes = d[(d["ac"].isin(["P2", "P3"]))
              & (d.get("made").astype(str).str.upper() == "TRUE")
              & d["pId"].notna()][["gameId", "row_n", "pId", "pts"]]
    makes = makes.rename(columns={"row_n": "shot_row", "pts": "shot_pts"})

    j = orbs.merge(makes, on=["gameId", "pId"], how="left")
    j = j[(j["shot_row"] > j["orb_row"]) & (j["shot_row"] <= j["orb_row"] + 3)]
    if j.empty:
        return pd.DataFrame(columns=["gameId", "name", "shortCode", "putback_pts"])
    j = j.sort_values("shot_row").groupby(["gameId", "name", "shortCode", "orb_row"], as_index=False).first()
    j["shot_pts"] = pd.to_numeric(j["shot_pts"], errors="coerce").fillna(0)
    return (j.groupby(["gameId", "name", "shortCode"], as_index=False)["shot_pts"]
            .sum().rename(columns={"shot_pts": "putback_pts"}))


AWARDS = [
    ("MVP", "🏆", "Highest total Net Pts", lambda d: d["PM"], True, {}),
    ("LVP", "💀", "Lowest total Net Pts", lambda d: d["PM"], False, {}),
    ("Heater", "🔥", "Highest Offensive Net Pts", lambda d: d["off_net"], True, {}),
    ("Off Night", "🌑", "Lowest Offensive Net Pts", lambda d: d["off_net"], False, {}),
    ("Stopper", "🛡️", "Highest Defensive Net Pts", lambda d: d["def_net"], True, {}),
    ("BBQ", "🔥🥩", "Lowest Defensive Net Pts", lambda d: d["def_net"], False, {}),
    ("Spark Plug", "⚡", "Best Net Pts off bench", lambda d: d["PM"], True, {"bench": True}),
    ("Ice Cold", "🧊", "Lowest FG% (min 4 att)", lambda d: d["FGM"] / d["FGA"], False, {"min_fga": 4}),
    ("Rain Maker", "🌧️", "Most above-break 3s made", lambda d: d["abovebk3m"], True, {}),
    ("Corner Pocket", "📐", "Most corner 3s made", lambda d: d["corner3m"], True, {}),
    ("Juggernaut", "🚂", "Most shots made at rim", lambda d: d["rim_makes"], True, {}),
    ("Surgical", "🔬", "Most mid-range makes", lambda d: d["midrange_makes"], True, {}),
    ("Speed Demon", "💨", "Most fast-break points", lambda d: d["fb_pts"], True, {}),
    ("Glass Cleaner", "🪟", "Most total rebounds", lambda d: d["REB"], True, {}),
    ("Ball Hawk", "🦅", "Most steals", lambda d: d["ST"], True, {}),
    ("Cleanup Crew", "🧹", "Most putback points", lambda d: d["putback_pts"], True, {}),
    ("Contact Artist", "🎯", "Most fouls drawn", lambda d: d["FD"], True, {}),
    ("Hacker", "🪓", "Most personal fouls committed", lambda d: d["PF"], True, {"min_val": 1}),
    ("Facilitator", "🎁", "Most assists", lambda d: d["AS"], True, {}),
    ("Hot Potato", "🥔", "Most turnovers", lambda d: d["TO"], True, {"min_val": 1}),
]


def build_awards(enriched: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for date in sorted(enriched["date"].dropna().unique()):
        day = enriched[enriched["date"] == date]
        for award, emoji, desc, expr, best, opts in AWARDS:
            d = day
            if opts.get("bench"):
                d = d[d["starter"].astype(str).str.upper() == "FALSE"]
            if opts.get("min_fga"):
                d = d[pd.to_numeric(d["FGA"], errors="coerce") >= opts["min_fga"]]
            val = expr(d) if len(d) else pd.Series(dtype=float)
            d = d.assign(_val=val).dropna(subset=["_val"])
            if opts.get("min_val") is not None:
                d = d[d["_val"] >= opts["min_val"]]
            if d.empty:
                rows.append({"date": date, "award": award, "emoji": emoji, "desc": desc,
                             "name": None, "team": None, "gameId": None, "stat_val": None})
                continue
            pick = d.nlargest(1, "_val") if best else d.nsmallest(1, "_val")
            r = pick.iloc[0]
            rows.append({"date": date, "award": award, "emoji": emoji, "desc": desc,
                         "name": r["name"], "team": r["team"],
                         "gameId": int(r["gameId"]), "stat_val": float(r["_val"])})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Dashboard splice
# ---------------------------------------------------------------------------

FLAG_MAP = {
    "AUS": "au", "CAN": "ca", "JPN": "jp", "HUN": "hu", "TUR": "tr", "ARG": "ar",
    "USA": "us", "FRA": "fr", "ESP": "es", "GBR": "gb", "GER": "de", "BRA": "br",
    "CHN": "cn", "KOR": "kr", "NGA": "ng", "BEL": "be", "CZE": "cz", "NED": "nl",
    "POL": "pl", "SWE": "se", "ITA": "it", "SRB": "rs", "LAT": "lv", "LTU": "lt",
    "NZL": "nz", "PUR": "pr", "MEX": "mx", "COL": "co", "GRE": "gr", "CRO": "hr",
    "SLO": "si", "SVK": "sk", "VEN": "ve", "DOM": "do", "PAR": "py", "CIV": "ci",
    "EGY": "eg",
    # added for the Olympic Pre-Qualifying field
    "SSD": "ss", "SEN": "sn", "PHI": "ph", "ANG": "ao", "MLI": "ml", "CHI": "cl",
    "URU": "uy", "CUB": "cu", "BAH": "bs", "JAM": "jm", "PAN": "pa", "CRC": "cr",
}


# Primary national colour per team, used to tint standings rows. Picked from
# each country's flag or national-team kit. Anything missing falls back to a
# neutral tint, so an unlisted team still renders.
TEAM_COLORS = {
    "ARG": "#75AADB", "AUS": "#00843D", "BEL": "#FDDA24", "BRA": "#009C3B",
    "CAN": "#D80621", "CHN": "#EE1C25", "CIV": "#F77F00", "COL": "#FCD116",
    "CRO": "#E62020", "CZE": "#11457E", "DOM": "#002D62", "EGY": "#CE1126",
    "ESP": "#AA151B", "FRA": "#002395", "GBR": "#012169", "GER": "#DD0000",
    "GRE": "#0D5EAF", "HUN": "#477050", "ITA": "#0064AA", "JPN": "#BC002D",
    "KOR": "#003478", "LAT": "#9E3039", "LTU": "#FDB913", "MEX": "#006847",
    "NED": "#FF6C00", "NGA": "#008751", "NZL": "#1B2432", "PAR": "#D52B1E",
    "PHI": "#0038A8", "POL": "#DC143C", "PUR": "#ED0000", "SEN": "#00853F",
    "SLO": "#005DA4", "SRB": "#C6363C", "SSD": "#0F47AF", "SVK": "#0B4EA2",
    "SWE": "#006AA7", "TUR": "#E30A17", "USA": "#0A3161", "VEN": "#FCD116",
    "ANG": "#CE1126", "BAH": "#00778B", "CHI": "#D52B1E", "CRC": "#002B7F",
    "CUB": "#002A8F", "JAM": "#009B3A", "MLI": "#14B53A", "PAN": "#005293",
    "URU": "#7BAFDE",
}


def write_dashboard(outdir: Path, competition: str, details, team_adv, enriched,
                    template: Path, spots: int = 4, event=None,
                    last_updated: str = ""):
    if not template.exists():
        print(f"\nTemplate not found at '{template.name}' — skipping HTML output.")
        print("Save your dashboard HTML as dashboard_template.html and rerun.")
        return

    lines = template.read_text(encoding="utf-8").split("\n")
    starts = [i for i, l in enumerate(lines) if l.strip() == "// %%DATA_START%%"]
    ends = [i for i, l in enumerate(lines) if l.strip() == "// %%DATA_END%%"]
    if len(starts) != 1 or len(ends) != 1:
        raise SystemExit("Template must contain exactly one %%DATA_START%% and one %%DATA_END%% marker.")

    gd_cols = ["gameId", "date", "home_team", "home_short", "home_score",
               "away_team", "away_short", "away_score", "round", "game_link", "competition"]
    if "group" in details.columns:
        gd_cols.append("group")
    gd = details[gd_cols]
    # Possessions rides along so the dashboard can show tournament pace.
    adv = team_adv[["gameId", "shortCode", "ORTG", "DRTG", "EFG%", "TO/Poss",
                    "DRB rt", "AST/FG%", "Possessions"]]

    has_groups = "group" in details.columns and details["group"].notna().any()
    gcol = ["group"] if has_groups else []
    wins = pd.concat([
        details[["home_team", "home_score", "away_score"] + gcol].rename(
            columns={"home_team": "team", "home_score": "score", "away_score": "opp"}),
        details[["away_team", "away_score", "home_score"] + gcol].rename(
            columns={"away_team": "team", "away_score": "score", "home_score": "opp"}),
    ])
    wins["win"] = (wins["score"] > wins["opp"]).astype(int)
    wins["diff"] = wins["score"] - wins["opp"]

    # `spots` counts places per group when the event has groups, matching how
    # FIBA actually advances teams (e.g. top 2 of Group A and top 2 of Group B).
    if has_groups:
        tbl = (wins.dropna(subset=["group"])
                   .groupby(["group", "team"], as_index=False)[["win", "diff"]].sum()
                   .sort_values(["group", "win", "diff"], ascending=[True, False, False]))
        qualifiers = (tbl.groupby("group").head(spots)["team"].tolist())
    else:
        tbl = (wins.groupby("team", as_index=False)[["win", "diff"]].sum()
                   .sort_values(["win", "diff"], ascending=[False, False]))
        qualifiers = tbl.head(spots)["team"].tolist()

    # Prefer FIBA's official event record; fall back to nothing so the page
    # can derive a range from the games it has.
    event_meta = {}
    if event is not None:
        name = str(event.get("name") or "")
        start, end = str(event.get("start") or ""), str(event.get("end") or "")
        # The published name often omits the year the branding carries.
        year = start[:4]
        if year and year not in name:
            name = f"{name} {year}"
        event_meta = {"name": name, "start": start, "end": end,
                      "host": str(event.get("host") or "")}

    def to_js(name, obj):
        if isinstance(obj, pd.DataFrame):
            payload = json.loads(obj.to_json(orient="records"))
        else:
            payload = obj
        return f"const {name} = {json.dumps(payload, ensure_ascii=False)};"

    block = [
        "// %%DATA_START%%",
        to_js("GAME_DETAILS", gd),
        to_js("ADV", adv),
        to_js("PLAYER_DATA", enriched),
        to_js("QUALIFIERS", qualifiers),
        to_js("QUALIFY_SPOTS", spots),
        # The event's own name and dates, so the header shows the real
        # tournament window rather than only the days already played.
        to_js("EVENT_META", event_meta),
        to_js("GENERATED_AT", last_updated),
        to_js("FLAG_MAP", FLAG_MAP),
        to_js("TEAM_COLORS", TEAM_COLORS),
        "// %%DATA_END%%",
    ]
    out = lines[:starts[0]] + block + lines[ends[0] + 1:]
    path = outdir / f"{competition} - dashboard.html"
    path.write_text("\n".join(out), encoding="utf-8")
    print(f"Dashboard written → {path}")

    # GitHub Pages serves from docs/, so keep a copy there for publishing.
    docs = Path(__file__).parent / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "index.html").write_text("\n".join(out), encoding="utf-8")
    print(f"Published copy    → {docs / 'index.html'}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def safe_name(text: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "-", text).strip()


def load_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", nargs="*", metavar="WORD",
                    help="list events (optionally filtered by words)")
    ap.add_argument("--event", nargs="+", metavar="WORD",
                    help="words identifying the tournament, e.g. --event women olympic guadalajara")
    ap.add_argument("--name", help="folder name for the output (default: official event name)")
    ap.add_argument("--qualify-spots", type=int, default=4, metavar="N",
                    help="how many teams advance; sets the cut line on the "
                         "Qualification board (default 4)")
    ap.add_argument("--all-games", action="store_true",
                    help="include games that are not final yet (usually fails to parse)")
    ap.add_argument("--watch", type=int, metavar="MINUTES", nargs="?", const=15,
                    help="stay running and re-check every N minutes (default 15) until "
                         "every game in the event is final")
    args = ap.parse_args()

    if args.list is not None:
        events = list_events()
        if args.list:
            q = " ".join(args.list).lower()
            hay = (events["name"] + " " + events["slug"] + " " + events["host"]).str.lower()
            events = events[hay.apply(lambda h: all(t in h for t in q.split()))]
        print(f"{len(events)} event(s):\n")
        for _, r in events.iterrows():
            print(f"  {r['start'] or '          '}  {r['name']}"
                  + (f"  [{r['host']}]" if r["host"] else ""))
        return

    if not args.event:
        ap.error("give --event WORDS (or --list to browse)")

    events = list_events()
    ev = resolve_event(" ".join(args.event), events)
    competition = safe_name(args.name or ev["name"])
    print(f"\nEvent:       {ev['name']}")
    print(f"Host:        {ev['host']}")
    print(f"Output dir:  {competition}/\n")

    while True:
        pending = run_once(ev, competition, args)
        if not args.watch:
            break
        if pending == 0:
            print("\nEvery game in this event is final — nothing left to watch.")
            break
        print(f"\n{pending} game(s) still to come. Re-checking in {args.watch} min "
              f"(Ctrl-C to stop).")
        time.sleep(args.watch * 60)


def run_once(ev, competition: str, args) -> int:
    """Scrape whatever is newly final; returns how many games are still pending."""
    outdir = Path(competition)
    datadir = outdir / "data"
    datadir.mkdir(parents=True, exist_ok=True)

    def p(kind):
        return datadir / f"{competition} - {kind}.csv"

    db = {k: load_csv(p(k)) for k in
          ("game details", "participant log", "player box scores", "pbp", "team box scores")}

    sched, pending = event_games(ev["slug"], played_only=not args.all_games)
    if not db["game details"].empty:
        done = set(db["game details"]["game_link"])
        sched = sched[~sched["game_link"].isin(done)]

    new = {"details": [], "competitors": [], "box": [], "team_box": [], "pbp": []}
    if sched.empty:
        print("No new games to scrape — all games already in database.")
    else:
        for i, row in enumerate(sched.itertuples(), 1):
            print(f"Getting {i} of {len(sched)}  ({row.home} vs {row.away})")
            got = scrape_game(row.game_link)
            if got:
                for k in new:
                    new[k].append(got[k])

    def combine(key, existing):
        frames = [f for f in new[key] if f is not None and not f.empty]
        parts = ([existing] if not existing.empty else []) + frames
        return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()

    details = combine("details", db["game details"])
    if details.empty:
        raise SystemExit("No game data available yet for this event.")

    competitors = combine("competitors", db["participant log"])
    raw_box = pd.concat([f for f in new["box"] if not f.empty], ignore_index=True) if new["box"] else pd.DataFrame()
    player_box = (pd.concat([db["player box scores"], build_player_box(raw_box)], ignore_index=True)
                  if not raw_box.empty else db["player box scores"])
    team_box_raw = pd.concat([f for f in new["team_box"] if not f.empty], ignore_index=True) if new["team_box"] else pd.DataFrame()

    details.to_csv(p("game details"), index=False)
    competitors.to_csv(p("participant log"), index=False)
    player_box.to_csv(p("player box scores"), index=False)

    if not team_box_raw.empty:
        team_box = build_team_box(team_box_raw, player_box)
        team_box = pd.concat([db["team box scores"], team_box], ignore_index=True).drop_duplicates()
    else:
        team_box = db["team box scores"]
    team_box.to_csv(p("team box scores"), index=False)

    team_adv = build_team_adv(team_box)
    team_adv.to_csv(p("team adv box scores"), index=False)

    short_ref = pd.concat([
        details[["home_team", "home_short"]].rename(columns={"home_team": "nationality", "home_short": "shortCode"}),
        details[["away_team", "away_short"]].rename(columns={"away_team": "nationality", "away_short": "shortCode"}),
    ]).drop_duplicates()

    new_pbp = pd.concat([f for f in new["pbp"] if not f.empty], ignore_index=True) if new["pbp"] else pd.DataFrame()
    pbp = (pd.concat([db["pbp"], enrich_pbp(new_pbp, short_ref)], ignore_index=True)
           if not new_pbp.empty else db["pbp"])
    pbp.to_csv(p("pbp"), index=False)

    build_standings(details).to_csv(p("standings"), index=False)

    team_adv_u = team_adv.drop_duplicates(["gameId", "shortCode"])
    enriched = build_enriched(player_box.drop_duplicates(), details.drop_duplicates(), team_adv_u, pbp)
    enriched.to_csv(p("player enriched"), index=False)

    awards = build_awards(enriched)
    awards.to_csv(p("daily awards"), index=False)

    print(f"\nComplete.")
    print(f"   → {len(details)} games")
    print(f"   → {len(enriched)} enriched player-game rows")
    print(f"   → {len(awards)} daily award rows "
          f"({enriched['date'].nunique()} dates x {len(AWARDS)} awards)")

    # The stamp in the header answers "how old is this data", not "when did the
    # build last run". A scheduled run that finds no finished game must leave it
    # untouched — otherwise the page changes on every run, which both misleads
    # the reader and defeats the workflow's commit-only-if-changed check.
    stamp_file = datadir / "last_updated.txt"
    scraped_any = any(f is not None and not f.empty for f in new["details"])
    if scraped_any or not stamp_file.exists():
        last_updated = datetime.now(timezone.utc).isoformat()
        stamp_file.write_text(last_updated, encoding="utf-8")
    else:
        last_updated = stamp_file.read_text(encoding="utf-8").strip()

    write_dashboard(outdir, competition, details, team_adv_u, enriched,
                    Path(__file__).parent / "dashboard_template.html",
                    spots=args.qualify_spots, event=ev, last_updated=last_updated)
    return pending


if __name__ == "__main__":
    main()
