packages <- c('pacman', 'tidyverse', 'rvest', 'jsonlite', 'httr', 'reticulate')

installed_packages <- packages %in% rownames(installed.packages())

if (any(installed_packages == FALSE)) {
  install.packages(packages[!installed_packages])
}

pacman::p_load('tidyverse', 'rvest', 'jsonlite', 'httr', 'reticulate')

print('Packages Loaded')
print(packages)

rm(list = c('packages', 'installed_packages'))

