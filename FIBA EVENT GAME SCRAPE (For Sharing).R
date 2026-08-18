#### FIBA Game / Competiton Report Developed NOV 2024 ####
# Had a break through on evaluating the structure of the new fiba.basketball website.
# The data is hidden better than it was, but we are able to uncover the game json from
# the site now.
# MUST WAIT UNTIL GAMES ARE LISTED AS FINAL FOR GAMES TO PROCESS
# contact Cohen MacDonald (cmacdonald@basketball.ca if you have any issues)
rm(list = ls())

## Load Packages
source('loadPackages.R')

# Competition name (for organizing files) *** CHANGE FOR DIFFERENT TOURNAMENTS ***
competition <- 'U17 World Cup Qualifying (Brno, Czechia)'

# Get the game links from your competition *** CHANGE FOR DIFFERENT TOURNAMENTS ***
# Reads the full schedule off the event page, so there is no need to paste in
# every game URL by hand. Only games that have been played are returned, since
# box scores do not exist until a game is final — re-run as the event goes on.
source('getGameLinks.R')

event_url <- 'https://www.fiba.basketball/en/events/fiba-u17-womens-basketball-world-cup-2026'

game_links <- get_game_links(event_url)

# To scrape a hand-picked subset instead, overwrite game_links with your own
# vector of URLs, e.g.
# game_links <- c(
#   "https://www.fiba.basketball/en/events/fiba-u17-womens-basketball-world-cup-2026/games/114387-AUS-LAT"
# )

game_links <- unique(game_links) 

## Enter Details so we can build the game details and standings.
# If there is a forfeited game, enter the link into the forfeited_games object
# You will also need to enter information into the forfeited_details data.frame
# Forfeited games in FIBA end 20 - 0
forfeited_games <- c()

forfeit_details <- tibble(
  gameId = c(), #Select something that won't have a duplicate (make it up)
  date = as.Date(c()),
  home_team = c(),
  home_short = c(),
  home_id = c(),
  home_score = c(),
  away_team = c(),
  away_short = c(),
  away_id = c(),
  away_score = c(),
  country = "",
  city = "",
  fibaZone = "",
  competition = "",
  round = c(),
  game_link = c()
)


#### This creates folders in your project to organize where things go by competition.
# Will load in the already processed games if the competition is on going
if (dir.exists(paste0(competition))) {
  print('Directory Exists')
} else {
  dir.create(paste0(competition))
  print(paste0(competition, " - Directory Created"))
  dir.create(paste0(competition, "/data"))
  dir.create(paste0(competition, "/data/hyper_files"))
}

if (
  file.exists(paste0(competition, '/data/', competition, ' - game details.csv'))
) {
  db_game_details <- read_csv(
    file = paste0(competition, '/data/', competition, ' - game details.csv')
  )
  all_game_details <- tibble()
  ### Makes the list of games smaller so it only pulls the new games we haven't processed.
  game_links <- game_links[!game_links %in% db_game_details$game_link]
} else {
  db_game_details <- tibble()
  all_game_details <- tibble()
}

if (
  file.exists(paste0(
    competition,
    '/data/',
    competition,
    ' - participant log.csv'
  ))
) {
  db_competitors <- read_csv(
    file = paste0(competition, '/data/', competition, ' - participant log.csv')
  )
  all_competitors <- tibble()
} else {
  db_competitors <- tibble()
  all_competitors <- tibble()
}

if (
  file.exists(paste0(
    competition,
    '/data/',
    competition,
    ' - team box scores.csv'
  ))
) {
  db_team_box_stats <- read_csv(
    file = paste0(competition, '/data/', competition, ' - team box scores.csv')
  )
  all_team_box_stats <- tibble()
} else {
  db_team_box_stats <- tibble()
  all_team_box_stats <- tibble()
}

if (
  file.exists(paste0(
    competition,
    '/data/',
    competition,
    ' - player box scores.csv'
  ))
) {
  db_box_stats <- read_csv(
    file = paste0(
      competition,
      '/data/',
      competition,
      ' - player box scores.csv'
    )
  )
  all_box_stats <- tibble()
} else {
  db_box_stats <- tibble()
  all_box_stats <- tibble()
}

if (file.exists(paste0(competition, '/data/', competition, ' - pbp.csv'))) {
  db_pbp <- read_csv(
    file = paste0(competition, '/data/', competition, ' - pbp.csv')
  ) %>%
    mutate(Time = as.character(Time))
  all_pbp <- tibble()
} else {
  db_pbp <- tibble()
  all_pbp <- tibble()
}

if (
  file.exists(paste0(
    competition,
    '/data/',
    competition,
    ' - team adv box scores.csv'
  ))
) {
  db_team_adv_box_stats <- read_csv(
    file = paste0(
      competition,
      '/data/',
      competition,
      ' - team adv box scores.csv'
    )
  )
} else {
  db_team_adv_box_stats <- tibble()
}


#### Data Scraping Starts

# FIBA's server intermittently drops HTTP/2 streams mid-download, which killed
# the whole run partway through the loop. Retry a few times with a short backoff.
read_html_retry <- function(url, tries = 5, pause = 3) {
  for (attempt in seq_len(tries)) {
    result <- try(read_html(url), silent = TRUE)
    if (!inherits(result, 'try-error')) return(result)
    message('  retry ', attempt, '/', tries, ' — ', conditionMessage(attr(result, 'condition')))
    Sys.sleep(pause * attempt)
  }
  stop('Failed to read after ', tries, ' attempts: ', url)
}

# FIX: guard the loop so it doesn't run when game_links is empty
# (avoids the 1:0 iteration bug when all games are already in the DB)
if (length(game_links) > 0) {
  for (i in 1:length(game_links)) {
    print(paste0('Getting ', i, ' of ', length(game_links)))
    #Reads the link
    html <- read_html_retry(game_links[i])
    #Converts it all to text
    text <- html %>% html_nodes('script') %>% html_text()
    #finds the longest node which will always have all the game data
    text <- text[which.max(nchar(text))]
    
    #remove extra characters at the start and end then extract JSON out
    temp <- substr(text, 20, nchar(text) - 1) %>%
      jsonlite::fromJSON()
    
    #repeat on the second list item
    data <- jsonlite::fromJSON(substr(temp[[2]], 4, nchar(temp[[2]])))
    
    #the desired game detail information is stored in the fourth list item
    game <- data[[4]]
    
    game_details <- tibble(
      gameId = game$game$gameId,
      date = as.Date(game$game$gameDateTime),
      home_team = game$game$teamA$officialName,
      home_short = game$game$teamA$code,
      home_id = game$game$teamA$organisationId,
      home_score = game$game$teamAScore,
      away_team = game$game$teamB$officialName,
      away_short = game$game$teamB$code,
      away_id = game$game$teamB$organisationId,
      away_score = game$game$teamBScore,
      country = game$game$hostCountry,
      city = game$game$hostCity,
      fibaZone = game$game$competition$fibaZone,
      competition = game$game$competition$officialName,
      round = game$game$round$roundName,
      game_link = game_links[[i]]
    )
    
    if (nrow(game_details > 0)) {
      all_game_details <- bind_rows(all_game_details, game_details)
      
      ## Get Competitors
      competitors <- bind_rows(
        game$playersTeamA %>%
          mutate(
            nationality = game_details$home_team,
            teamId = game_details$home_id
          ) %>%
          mutate(name = paste(firstName, lastName), .keep = 'unused'),
        game$playersTeamB %>%
          mutate(
            nationality = game_details$away_team,
            teamId = game_details$away_id
          ) %>%
          mutate(name = paste(firstName, lastName), .keep = 'unused')
      ) %>%
        mutate(
          gameId = game_details$gameId,
          uniformNumber = suppressWarnings(as.numeric(uniformNumber))
        ) %>%
        rename(pId = personId) %>%
        filter(!is.na(uniformNumber))
      
      ## Get Box Scores (Players)
      box1 <- bind_cols(
        tibble(
          pId = as.numeric(sub(
            "^P_",
            "",
            game$gameDetails$c$Children[[1]][['Id']]
          ))
        ),
        game$gameDetails$c$Children[[1]][['Stats']]
      ) %>%
        select(
          pId,
          PM,
          Starter,
          AS,
          BS,
          DR,
          FD,
          FG2A,
          FG2M,
          FG2P,
          FG3A,
          FG3M,
          FG3P,
          FGA,
          FGM,
          FTA,
          FTM,
          FTP,
          OR,
          PF,
          PTS,
          REB,
          ST,
          TO,
          EFF,
          FGIA,
          FGIM,
          FGIP,
          TP
        )
      
      box2 <- bind_cols(
        tibble(
          pId = as.numeric(sub(
            "^P_",
            "",
            game$gameDetails$c$Children[[2]][['Id']]
          ))
        ),
        game$gameDetails$c$Children[[2]][['Stats']]
      ) %>%
        select(
          pId,
          PM,
          Starter,
          AS,
          BS,
          DR,
          FD,
          FG2A,
          FG2M,
          FG2P,
          FG3A,
          FG3M,
          FG3P,
          FGA,
          FGM,
          FTA,
          FTM,
          FTP,
          OR,
          PF,
          PTS,
          REB,
          ST,
          TO,
          EFF,
          FGIA,
          FGIM,
          FGIP,
          TP
        )
      
      box_stats <- bind_rows(box1, box2)
      
      box_stats <- left_join(box_stats, competitors, by = 'pId') %>%
        select(gameId, teamId, nationality, uniformNumber, name, pId:TP)
      
      all_box_stats <- bind_rows(all_box_stats, box_stats)
      
      competitors <- competitors %>% filter(name %in% box_stats$name)
      
      all_competitors <- bind_rows(all_competitors, competitors)
      
      ## Get Teamstats
      
      teamstats <- bind_cols(
        tibble(
          gameId = game_details$gameId,
          nationality = c(game_details$home_team, game_details$away_team),
          teamId = c(game_details$home_id, game_details$away_id),
          shortCode = c(game_details$home_short, game_details$away_short)
        ),
        game$gameDetails$c$Stats %>% select(AS:TO, -Leaders)
      ) %>%
        mutate(ID = sequence(n()))
      
      all_team_box_stats <- bind_rows(all_team_box_stats, teamstats)
      
      ## Get Play-by-play and shot data by period
      # list of periods for game
      period_list <- tibble(
        Code = as.character(game$gameDetails$c$Periods[[1]][['Id']])
      )
      
      pbp_game <- tibble()
      for (j in 1:nrow(period_list)) {
        pbp <- game$playByPlay$items[[j]]$items
        
        pbp <- left_join(
          pbp %>%
            mutate(
              pId = suppressWarnings(as.numeric(pId)),
              oId = suppressWarnings(as.numeric(oId))
            ),
          competitors,
          by = c('pId', 'oId' = 'teamId')
        ) %>%
          mutate(period = period_list[[j, 1]])
        
        pbp_game <- bind_rows(pbp_game, pbp)
      }
      
      all_pbp <- bind_rows(all_pbp, pbp_game)
    } else {
      #checks if it is in the forfeit list
      print("Checking forfeit")
      if (nrow(forfeit_details) != 0) {
        for (f in 1:nrow(forfeit_details)) {
          if (game_links[i] == forfeit_details$game_link[f]) {
            #append row
            all_game_details <- bind_rows(all_game_details, forfeit_details)
          }
        }
      }
      
      print('Skipping because no game details')
    }
  }
} else {
  print('No new games to scrape — all games already in database.')
}


### bind to DB and write game details
db_game_details <- bind_rows(db_game_details, all_game_details)
write_csv(
  db_game_details,
  paste0(competition, '/data/', competition, ' - game details.csv')
)

short_code_ref <- db_game_details %>%
  select(away_team, away_short) %>%
  rename(nationality = away_team, shortCode = away_short) %>%
  bind_rows(
    db_game_details %>%
      select(home_team, home_short) %>%
      rename(nationality = home_team, shortCode = home_short)
  ) %>%
  unique()

# append data player box — only process if there are new games
if (nrow(all_box_stats) > 0) {
  player_box_scores <- all_box_stats %>%
    unique() %>%
    mutate(
      `10+ ppg` = if_else(PTS >= 10, 1, 0),
      FGP = round(FGM / FGA, 3),
      FG2P = round(FG2M / FG2A, 3),
      FG3P = round(FG3M / FG3A, 3),
      FTP = round(FTM / FGA, 3)
    ) %>%
    separate(TP, into = c('mins', 'secs'), sep = ":", remove = F) %>%
    mutate(
      MP = round(as.numeric(mins) + (as.numeric(secs) / 60), 1),
      mins = as.numeric(mins),
      secs = as.numeric(secs)
    ) %>%
    select(gameId, nationality, teamId, name, pId, PM:MP)
  
  # bind to DB and write player box
  db_box_stats <- bind_rows(db_box_stats, player_box_scores)
  write_csv(
    db_box_stats,
    paste0(competition, '/data/', competition, ' - player box scores.csv')
  )
  
  # append data team box
  team_calculated_stats <- player_box_scores %>%
    unique() %>%
    group_by(nationality, teamId, gameId) %>%
    summarise_at(vars(c(`10+ ppg`, MP)), sum) %>%
    ungroup() %>%
    mutate(MP = round(MP, 0)) %>%
    group_by(gameId) %>%
    mutate(ID = sequence(n())) %>%
    arrange(gameId)
  
  opp_box_scores <- all_team_box_stats %>%
    mutate(ID = if_else(ID == 1, 2, 1))
  
  colnames(opp_box_scores) <- paste("opp", colnames(opp_box_scores), sep = "_")
  
  all_team_box_stats <- left_join(
    all_team_box_stats,
    team_calculated_stats %>% select(-ID),
    by = c('nationality', 'teamId', 'gameId')
  )
  
  team_box_scores <- left_join(
    all_team_box_stats,
    opp_box_scores,
    by = c('gameId' = 'opp_gameId', 'ID' = 'opp_ID')
  ) %>%
    unique()
  
  # bind to db then write file
  db_team_box_stats <- bind_rows(db_team_box_stats, team_box_scores)
  write_csv(
    db_team_box_stats,
    paste0(competition, '/data/', competition, ' - team box scores.csv')
  )
  
  # adv team box
  team_adv_box_scores <- db_team_box_stats %>%
    mutate(
      Possessions = (0.5 *
                       ((FGA + 0.4 * FTA - 1.07 * (OR / (OR + opp_DR)) * (FGA - FGM) + TO) +
                          (opp_FGA +
                             0.4 * opp_FTA -
                             1.07 * (opp_OR / (opp_OR + DR)) * (opp_FGA - opp_FGM) +
                             opp_TO))),
      ORTG = round(100 * PTS / Possessions, 1),
      DRTG = round(100 * opp_PTS / Possessions, 1),
      `EFG%` = round(100 * ((FGM + 0.5 * FG3M) / FGA), 1),
      `TO/Poss` = round(TO / Possessions * 100, 1),
      `DRB rt` = round(100 * (DR / (DR + opp_OR)), 1),
      `AST/FG%` = round(100 * (AS / FGM), 1)
    ) %>%
    select(
      gameId,
      nationality,
      teamId,
      shortCode,
      PTS,
      Possessions,
      ORTG,
      `10+ ppg`,
      DRTG,
      `EFG%`,
      `TO/Poss`,
      `DRB rt`,
      `AST/FG%`
    )
  
  db_team_adv_box_stats <- bind_rows(db_team_adv_box_stats, team_adv_box_scores)
  write_csv(
    db_team_adv_box_stats,
    paste0(competition, '/data/', competition, ' - team adv box scores.csv')
  )
  
  # append pbp
  pbp <- all_pbp %>% mutate(Code = as.character(period)) %>% unique()
  
  pbp <- pbp %>%
    mutate(x = as.double(x), y = as.double(y))
  
  ## Adding shot zones/distances to the dataset based on the layout of their court
  pbp_adj <- pbp %>%
    mutate(
      zoneBasic = case_when(
        ac == 'P3' & x > 200 & y < 75 ~ 'Left Corner 3',
        ac == 'P3' & x < 20 & y < 75 ~ 'Right Corner 3',
        ac == 'P3' ~ 'Above the Break 3',
        ac == 'P2' &
          x <= 140 + 25 &
          x >= 140 - 25 &
          y <= 40 + 10 ~ 'Restricted Area',
        ac == 'P2' & y <= 100 & x >= 95 & x <= 185 ~ 'In The Paint (Non-RA)',
        ac == 'P2' ~ 'Mid-Range',
        ac == 'P3' & y > 418 ~ 'Backcourt'
      ),
      distanceShot = round(sqrt((140 - x)^2 + (40 - y)^2) / 10, 1),
      zoneRange = case_when(
        distanceShot >= 22 & zoneBasic != 'Backcourt' ~ '22+ ft.',
        distanceShot < 22 & distanceShot >= 16 ~ '16-22 ft.',
        distanceShot < 16 & distanceShot >= 8 ~ '8-16 ft.',
        distanceShot < 8 ~ 'Less Than 8 ft.',
        zoneBasic == 'Backcourt' ~ 'Back Court Shot'
      )
    ) %>%
    separate(
      Time,
      into = c('game_mins', 'game_seconds', 'game_milliseconds'),
      sep = ":",
      remove = F,
      convert = T
    ) %>%
    mutate(
      seconds_elapsed = case_when(
        Code == 'Q1' ~ (600) - (game_mins * 60 + game_seconds),
        Code == 'Q2' ~ 600 * 1 + (600 - (game_mins * 60 + game_seconds)),
        Code == 'Q3' ~ 600 * 2 + (600 - (game_mins * 60 + game_seconds)),
        Code == 'Q4' ~ 600 * 3 + (600 - (game_mins * 60 + game_seconds)),
        Code == 'OT1' ~ 600 * 4 + (300 - (game_mins * 60 + game_seconds)),
        Code == 'OT2' ~ 600 * 4 + 300 * 1 + (300 - (game_mins * 60 + game_seconds)),
        Code == 'OT3' ~ 600 * 4 + 300 * 2 + (300 - (game_mins * 60 + game_seconds)),
        Code == 'OT4' ~ 600 * 4 + 300 * 3 + (300 - (game_mins * 60 + game_seconds))
      ),
      Id = as.double(Id)
    )
  
  pbp_adj <- left_join(pbp_adj, short_code_ref, by = 'nationality')
  
  # bind to pbp db and write file
  db_pbp <- bind_rows(db_pbp, pbp_adj)
  write_csv(db_pbp, paste0(competition, '/data/', competition, ' - pbp.csv'))
  
} else {
  print('No new game data to process — skipping box score / PBP pipeline.')
}

### Quick standings update
home <- db_game_details %>%
  select(
    gameId,
    date,
    fibaZone,
    competition,
    round,
    team = home_team,
    team_short = home_short,
    score = home_score,
    opp_team = away_team,
    opp_short = away_short,
    opp_score = away_score
  ) %>%
  mutate(
    Win = if_else(score > opp_score, 1, 0),
    Loss = if_else(score < opp_score, 1, 0),
    Differential = score - opp_score
  ) %>%
  select(
    gameId,
    date,
    fibaZone,
    competition,
    round,
    team,
    team_short,
    Win,
    Loss,
    Differential
  )

away <- db_game_details %>%
  select(
    gameId,
    date,
    fibaZone,
    competition,
    round,
    opp_team = home_team,
    opp_short = home_short,
    opp_score = home_score,
    team = away_team,
    team_short = away_short,
    score = away_score
  ) %>%
  mutate(
    Win = if_else(score > opp_score, 1, 0),
    Loss = if_else(score < opp_score, 1, 0),
    Differential = score - opp_score
  ) %>%
  select(
    gameId,
    date,
    fibaZone,
    competition,
    round,
    team,
    team_short,
    Win,
    Loss,
    Differential
  )

standings <- bind_rows(home, away) %>%
  arrange(gameId)

write_csv(
  standings,
  paste0(competition, '/data/', competition, ' - standings.csv')
)


# CONFIG *** CHANGE FOR DIFFERENT TOURNAMENTS ***
# -----------------------------------------------------------------------------
competition <- "U17 World Cup Qualifying (Brno, Czechia)" # Tournament Name (Should match the top)

data_dir <- paste0(competition, "/data/") # Directs Everything to Specific Folder


# 1. LOAD CSVs
# -----------------------------------------------------------------------------
game_details <- read_csv(paste0(data_dir, competition, " - game details.csv")) %>% distinct()
player_box   <- read_csv(paste0(data_dir, competition, " - player box scores.csv")) %>% distinct()
team_adv     <- read_csv(paste0(data_dir, competition, " - team adv box scores.csv")) %>% distinct(gameId, shortCode, .keep_all = TRUE)
pbp_raw      <- read_csv(paste0(data_dir, competition, " - pbp.csv"), col_types = cols(Time = col_character()))



# 2. HELPER LOOKUPS
# -----------------------------------------------------------------------------
# date per gameId
id_to_date <- game_details %>%
  select(gameId, date)

# team short code per nationality (home + away)
team_short_ref <- bind_rows(
  game_details %>% select(gameId, nationality = home_team, shortCode = home_short),
  game_details %>% select(gameId, nationality = away_team, shortCode = away_short)
) %>% distinct()

# game result (point differential) per (gameId, shortCode)
game_result <- bind_rows(
  game_details %>% transmute(gameId, shortCode = home_short,
                             wl_diff = home_score - away_score),
  game_details %>% transmute(gameId, shortCode = away_short,
                             wl_diff = away_score - home_score)
)

# ORTG / DRTG per (gameId, shortCode) — used to split PM into off/def
ortg_ref <- team_adv %>%
  distinct(gameId, shortCode, .keep_all = TRUE) %>%
  select(gameId, shortCode, ORTG, DRTG)



# 3. PBP-DERIVED STATS PER PLAYER PER GAME
# -----------------------------------------------------------------------------

# Filter to rows that have a player and a game
pbp <- pbp_raw %>%
  filter(!is.na(gameId), !is.na(name), name != "NA",
         !is.na(shortCode), shortCode != "NA") %>%
  arrange(gameId, order)

# --- 3a. Shot-zone makes ---------------------------------------------------
shot_stats <- pbp %>%
  filter(ac %in% c("P2", "P3"), made == "TRUE") %>%
  mutate(
    corner3m       = if_else(zoneBasic %in% c("Left Corner 3", "Right Corner 3"), 1L, 0L),
    abovebk3m      = if_else(zoneBasic == "Above the Break 3", 1L, 0L),
    rim_makes      = if_else(zoneBasic == "Restricted Area" & ac == "P2", 1L, 0L),
    midrange_makes = if_else(zoneBasic == "Mid-Range", 1L, 0L),
    fb_pts         = if_else(str_detect(tolower(txt), "fast"), as.integer(pts), 0L)
  ) %>%
  group_by(gameId, name, shortCode) %>%
  summarise(
    corner3m       = sum(corner3m,       na.rm = TRUE),
    abovebk3m      = sum(abovebk3m,      na.rm = TRUE),
    rim_makes      = sum(rim_makes,       na.rm = TRUE),
    midrange_makes = sum(midrange_makes,  na.rm = TRUE),
    fb_pts         = sum(fb_pts,          na.rm = TRUE),
    .groups = "drop"
  )

# --- 3b. Steals (player-level from PBP for completeness) -------------------
steal_stats <- pbp %>%
  filter(ac == "ST") %>%
  count(gameId, name, shortCode, name = "steals_pbp")

# --- 3c. Putbacks: offensive rebound → made shot by same player ≤3 events later
putback_stats <- pbp %>%
  filter(ac %in% c("REB", "P2", "P3")) %>%
  group_by(gameId) %>%
  mutate(row_n = row_number()) %>%
  ungroup()

orb_rows <- putback_stats %>%
  filter(ac == "REB", str_detect(tolower(txt), "offensive"), !is.na(pId)) %>%
  select(gameId, orb_row = row_n, pId, name, shortCode)

shot_rows <- putback_stats %>%
  filter(ac %in% c("P2", "P3"), made == "TRUE", !is.na(pId)) %>%
  select(gameId, shot_row = row_n, pId, shot_pts = pts)

putbacks <- orb_rows %>%
  left_join(shot_rows, by = c("gameId", "pId"),
            relationship = "many-to-many") %>%
  filter(shot_row > orb_row, shot_row <= orb_row + 3) %>%
  group_by(gameId, name, shortCode, orb_row) %>%
  slice_min(shot_row, n = 1) %>%
  ungroup() %>%
  group_by(gameId, name, shortCode) %>%
  summarise(putback_pts = sum(shot_pts, na.rm = TRUE), .groups = "drop")



# 4. BUILD ENRICHED PLAYER TABLE
# -----------------------------------------------------------------------------

enriched <- player_box %>%
  filter(MP >= 2) %>%
  left_join(id_to_date,    by = "gameId") %>%
  left_join(team_short_ref, by = c("gameId", "nationality")) %>%   
  left_join(game_result,   by = c("gameId", "shortCode")) %>%
  left_join(ortg_ref,      by = c("gameId", "shortCode")) %>%
  mutate(
    PM          = as.numeric(PM),
    off_delta   = ORTG - 100,
    def_delta   = 100 - DRTG,
    total_delta = abs(off_delta) + abs(def_delta),
    off_frac    = if_else(total_delta > 0, abs(off_delta) / total_delta, 0.5),
    off_net     = round(PM * off_frac,       1),
    def_net     = round(PM * (1 - off_frac), 1),
    wl_str      = if_else(wl_diff >= 0,
                          paste0("+", wl_diff),
                          as.character(wl_diff)),
    stocks      = ST + BS
  ) %>%
  left_join(shot_stats,  by = c("gameId", "name", "shortCode")) %>%
  left_join(steal_stats, by = c("gameId", "name", "shortCode")) %>%
  left_join(putbacks,    by = c("gameId", "name", "shortCode")) %>%
  mutate(across(c(corner3m, abovebk3m, rim_makes, midrange_makes,
                  fb_pts, steals_pbp, putback_pts),
                ~ replace_na(.x, 0L))) %>%
  select(
    date, gameId, name, team = shortCode, nationality,
    PTS, REB, OR, DR, AS, ST, BS, PF, FD, FTM, FGA, FGM, FG3M, TO,  # FIX: AS added
    MP, PM, off_net, def_net, stocks, starter = Starter,
    WL = wl_str, WL_raw = wl_diff,
    corner3m, abovebk3m, rim_makes, midrange_makes,
    fb_pts, putback_pts
  )



# 5. DAILY AWARDS
# -----------------------------------------------------------------------------

compute_award <- function(day_df, stat_expr, best = TRUE,
                          min_val = NULL, bench_only = FALSE,
                          min_fga = NULL) {
  
  df <- day_df
  
  if (bench_only)  df <- df %>% filter(starter == FALSE)
  if (!is.null(min_fga)) df <- df %>% filter(FGA >= min_fga)
  
  df <- df %>%
    mutate(.val = {{ stat_expr }}) %>%
    filter(!is.na(.val))
  
  if (!is.null(min_val)) df <- df %>% filter(.val >= min_val)
  
  if (nrow(df) == 0) return(tibble(name = NA_character_, team = NA_character_,
                                   gameId = NA_integer_,  stat_val = NA_real_))
  
  if (best) df <- df %>% arrange(desc(.val)) else df <- df %>% arrange(.val)
  
  df %>%
    slice(1) %>%
    transmute(name, team, gameId = as.integer(gameId), stat_val = .val)
}

award_defs <- list(
  list(id = "MVP",            emoji = "🏆",  desc = "Highest total Net Pts",          expr = quote(PM),            best = TRUE),
  list(id = "LVP",            emoji = "💀",  desc = "Lowest total Net Pts",           expr = quote(PM),            best = FALSE),
  list(id = "Heater",         emoji = "🔥",  desc = "Highest Offensive Net Pts",      expr = quote(off_net),       best = TRUE),
  list(id = "Off Night",      emoji = "🌑",  desc = "Lowest Offensive Net Pts",       expr = quote(off_net),       best = FALSE),
  list(id = "Stopper",        emoji = "🛡️", desc = "Highest Defensive Net Pts",      expr = quote(def_net),       best = TRUE),
  list(id = "BBQ",            emoji = "🔥🥩",desc = "Lowest Defensive Net Pts",       expr = quote(def_net),       best = FALSE),
  list(id = "Spark Plug",     emoji = "⚡",  desc = "Best Net Pts off bench",         expr = quote(PM),            best = TRUE,  bench_only = TRUE),
  list(id = "Ice Cold",       emoji = "🧊",  desc = "Lowest FG% (min 4 att)",         expr = quote(FGM / FGA),     best = FALSE, min_fga = 4),
  list(id = "Rain Maker",     emoji = "🌧️", desc = "Most above-break 3s made",       expr = quote(abovebk3m),     best = TRUE),
  list(id = "Corner Pocket",  emoji = "📐",  desc = "Most corner 3s made",            expr = quote(corner3m),      best = TRUE),
  list(id = "Juggernaut",     emoji = "🚂",  desc = "Most shots made at rim",         expr = quote(rim_makes),     best = TRUE),
  list(id = "Surgical",       emoji = "🔬",  desc = "Most mid-range makes",           expr = quote(midrange_makes),best = TRUE),
  list(id = "Speed Demon",    emoji = "💨",  desc = "Most fast-break points",         expr = quote(fb_pts),        best = TRUE),
  list(id = "Glass Cleaner",  emoji = "🪟",  desc = "Most total rebounds",            expr = quote(REB),           best = TRUE),
  list(id = "Ball Hawk",      emoji = "🦅",  desc = "Most steals",                    expr = quote(ST),            best = TRUE),
  list(id = "Cleanup Crew",   emoji = "🧹",  desc = "Most putback points",            expr = quote(putback_pts),   best = TRUE),
  list(id = "Contact Artist", emoji = "🎯",  desc = "Most fouls drawn",               expr = quote(FD),            best = TRUE),
  list(id = "Hacker",         emoji = "🪓",  desc = "Most personal fouls committed",  expr = quote(PF),            best = TRUE, min_val = 1),
  list(id = "Facilitator",    emoji = "🎁",  desc = "Most assists",                   expr = quote(AS),            best = TRUE),   
  list(id = "Hot Potato",     emoji = "🥔",  desc = "Most turnovers",                 expr = quote(TO),            best = TRUE, min_val = 1)
)

# Compute awards per game day
all_dates <- enriched %>% distinct(date) %>% pull(date)

daily_awards <- map_dfr(all_dates, function(d) {
  day_df <- enriched %>% filter(date == d)
  
  map_dfr(award_defs, function(def) {
    result <- compute_award(
      day_df     = day_df,
      stat_expr  = !!def$expr,
      best       = def$best,
      bench_only = isTRUE(def$bench_only),
      min_fga    = def$min_fga,
      min_val    = def$min_val
    )
    
    tibble(
      date     = d,
      award    = def$id,
      emoji    = def$emoji,
      desc     = def$desc,
      name     = result$name,
      team     = result$team,
      gameId   = result$gameId,
      stat_val = result$stat_val
    )
  })
})



# 6. WRITE OUTPUTS
# -----------------------------------------------------------------------------
write_csv(enriched,
          paste0(data_dir, competition, " - player enriched.csv"))

write_csv(daily_awards,
          paste0(data_dir, competition, " - daily awards.csv"))

message("dashboard_prep.R complete.")
message("   → ", nrow(enriched),     " enriched player-game rows")
message("   → ", nrow(daily_awards), " daily award rows (",
        n_distinct(daily_awards$date), " dates × ", length(award_defs), " awards)")



# 7. BUILD SELF-CONTAINED HTML DASHBOARD
# -----------------------------------------------------------------------------

library(jsonlite)

template_path <- "dashboard_template.html"
output_path   <- paste0(competition, "/", competition, " - dashboard.html")

if (!file.exists(template_path)) {
  warning(
    "Template not found at '", template_path, "' — skipping HTML output.\n",
    "Save your dashboard HTML as dashboard_template.html and rerun."
  )
} else {
  
  template <- readLines(template_path, encoding = "UTF-8", warn = FALSE)
  
  start_line <- which(trimws(template) == "// %%DATA_START%%")
  end_line   <- which(trimws(template) == "// %%DATA_END%%")
  
  if (length(start_line) != 1 || length(end_line) != 1) {
    stop(
      "Could not find exactly one %%DATA_START%% and one %%DATA_END%% marker ",
      "in the template. Add them around the JS data constants block."
    )
  }
  
  # game_details: columns the dashboard needs
  gd_js <- game_details %>%
    select(gameId, date, home_team, home_short, home_score,
           away_team, away_short, away_score, round, game_link, competition)
  
  # adv box: columns the dashboard uses
  adv_js <- team_adv %>%
    select(gameId, shortCode, ORTG, DRTG,
           `EFG%`, `TO/Poss`, `DRB rt`, `AST/FG%`)
  
  # Qualifiers: top-4 teams by win total
  qualifiers <- bind_rows(
    game_details %>% select(team = home_team, score = home_score, opp = away_score),
    game_details %>% select(team = away_team, score = away_score, opp = home_score)
  ) %>%
    mutate(win = as.integer(score) > as.integer(opp)) %>%
    group_by(team) %>%
    summarise(wins = sum(win), .groups = "drop") %>%
    arrange(desc(wins)) %>%
    slice_head(n = 4) %>%     # change 4 if a different number qualify
    pull(team)
  
  # Helper: serialize one R object to a JS const declaration
  to_js <- function(var_name, obj) {
    paste0("const ", var_name, " = ", toJSON(obj, auto_unbox = TRUE), ";")
  }
  
  # FLAG MAP — short code → ISO 3166-1 alpha-2
  # To add a new country: "XYZ" = "xx"  (Google "ISO 3166 [country name]" if unsure)
  flag_map <- c(
    "AUS" = "au", "CAN" = "ca", "JPN" = "jp", "HUN" = "hu",
    "TUR" = "tr", "ARG" = "ar", "USA" = "us", "FRA" = "fr",
    "ESP" = "es", "GBR" = "gb", "GER" = "de", "BRA" = "br",
    "CHN" = "cn", "KOR" = "kr", "NGA" = "ng", "BEL" = "be",
    "CZE" = "cz", "NED" = "nl", "POL" = "pl", "SWE" = "se",
    "ITA" = "it", "SRB" = "rs", "LAT" = "lv", "LTU" = "lt",
    "NZL" = "nz", "PUR" = "pr", "MEX" = "mx", "COL" = "co",
    "GRE" = "gr", "CRO" = "hr", "SLO" = "si", "SVK" = "sk",
    "VEN" = "ve", "DOM" = "do", "PAR" = "py", "CIV" = "ci",
    "EGY" = "eg"
  )
  
  flag_map_list <- as.list(flag_map)
  
  data_block <- c(
    "// %%DATA_START%%",
    to_js("GAME_DETAILS", gd_js),
    to_js("ADV",          adv_js),
    to_js("PLAYER_DATA",  enriched),
    to_js("QUALIFIERS",   qualifiers),
    # Cut line on the Qualification board, and the build time the page stamps
    # in its header so readers can see how fresh the numbers are.
    to_js("QUALIFY_SPOTS", length(qualifiers)),
    to_js("GENERATED_AT",  format(Sys.time(), "%Y-%m-%dT%H:%M:%S%z")),
    to_js("FLAG_MAP",     flag_map_list),
    "// %%DATA_END%%"
  )
  
  final_html <- c(
    template[seq_len(start_line - 1)],
    data_block,
    template[seq(end_line + 1, length(template))]
  )
  
  writeLines(final_html, output_path, useBytes = TRUE)
  message("Dashboard written → ", output_path)
}




#### Commented this out but Can walk you through it if you want. Basically this pushes to tableau so you can work with the datasets there. These scripts contain some API KEY info so i have left out.
# ## PUBLISH ALL DATASOURCES TO TABLEAU
# reticulate::py_require("pandas", action = "add")
# reticulate::py_require("pantab", action = "add")
# reticulate::py_require("tableauserverclient", action = "add")
# reticulate::source_python('python/publish_fiba_competition_data.py')

## Commented out but I used a gmail package from a dummy email to let coaches / staff know when data is updated and ready. Simple to use for communicating updates.
# # Send Email
# source('send emails.R')
