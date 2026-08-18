#### Auto-discover game links for a FIBA competition ####
# Instead of pasting every game URL by hand, point this at an event page and it
# reads the full schedule out of the same hydration blob the game scraper uses.
#
#   game_links <- get_game_links("https://www.fiba.basketball/en/events/fiba-u17-womens-basketball-world-cup-2026")
#
# Returns played games only by default (played_only = TRUE), because the scraper
# needs games to be FINAL before their box scores exist.

# Walk an arbitrarily nested list and return the first node holding a 'gameId'.
# The site's React tree shifts between pages/seasons, so search for the data
# rather than hardcoding a path into it.
find_games_node <- function(x, depth = 0) {
  if (depth > 15 || !is.list(x)) return(NULL)
  nm <- names(x)
  if (!is.null(nm) && 'gameId' %in% nm) return(x)
  kids <- if (is.null(nm)) seq_along(x) else nm
  for (k in kids) {
    hit <- find_games_node(x[[k]], depth + 1)
    if (!is.null(hit)) return(hit)
  }
  NULL
}

get_game_links <- function(event_url, played_only = TRUE) {
  event_url <- sub('/+$', '', event_url)
  event_url <- sub('/games$', '', event_url)
  games_url <- paste0(event_url, '/games')

  message('Reading schedule from ', games_url)

  # Same extraction trick as the game scraper: the longest <script> node holds
  # the JSON payload, wrapped in a couple of layers of escaping.
  html <- read_html(games_url)
  text <- html %>% html_nodes('script') %>% html_text()
  text <- text[which.max(nchar(text))]

  temp <- substr(text, 20, nchar(text) - 1) %>% jsonlite::fromJSON()
  data <- jsonlite::fromJSON(substr(temp[[2]], 4, nchar(temp[[2]])))

  games <- find_games_node(data)
  if (is.null(games)) {
    stop('Could not find the games list on ', games_url,
         ' — the page structure may have changed.')
  }

  schedule <- tibble(
    gameId     = games$gameId,
    home       = games$teamA$code,
    away       = games$teamB$code,
    homeScore  = games$teamAScore,
    awayScore  = games$teamBScore,
    date       = as.Date(games$gameDateTime),
    round      = games$round$roundName,
    statStatus = games$gameStatisticStatusCode,
    isLive     = games$isLive
  ) %>%
    # Enforces the "wait until games are FINAL" rule from the scraper header.
    # gameStatisticStatusCode flips EMPTY -> VALID once stats exist, isLive
    # marks a game in progress, and a knockout slot has no team code until the
    # bracket fills in. Checking the score is not enough: a live game already
    # shows a running score, and scraping it would freeze partial stats in.
    mutate(
      played = !is.na(home) & !is.na(away) &
        home != '' & away != '' &
        statStatus == 'VALID' &
        !coalesce(isLive, FALSE)
    )

  n_pending <- sum(!schedule$played)
  if (played_only) {
    schedule <- schedule %>% filter(played)
    if (n_pending > 0) {
      message('Skipping ', n_pending, ' game(s) not yet final.')
    }
  }

  schedule <- schedule %>% arrange(date, gameId)

  message('Found ', nrow(schedule), ' game(s) across ',
          n_distinct(schedule$round), ' round(s): ',
          paste(unique(schedule$round), collapse = ', '))

  paste0(event_url, '/games/', schedule$gameId, '-', schedule$home, '-', schedule$away)
}
