# Myrient Browser

Konsolowe narzędzie CLI/TUI do przeszukiwania i pobierania plików z publicznego repozytorium HTTP na podstawie lokalnego indeksu ścieżek.

## ⚠️ Ostrzeżenie

To narzędzie służy wyłącznie do pobierania treści, do których użytkownik ma prawa. Upewnij się, że przestrzegasz wszystkich obowiązujących przepisów i warunków użytkowania.

## Funkcje

- **Interaktywny TUI** - przeglądanie drzewa katalogów, fuzzy search, zaznaczanie wielu elementów
- **Rozmiary plików** - wyświetlanie rozmiarów plików i folderów (wymaga indeksu JSON)
- **Fuzzy search** - szybkie wyszukiwanie z obsługą operatora OR (`|`)
- **Kolejka pobrań** - równoległe pobieranie z wznawianiem (resume), retry z backoff
- **Eksport** - eksport zaznaczonych ścieżek do pliku (txt/urls/json)
- **Persystencja** - stan kolejki zapisywany na dysk, możliwość wznowienia po restarcie
- **Tryb wsadowy** - komendy CLI do automatyzacji

## Wymagania

- Python 3.11+
- macOS lub Linux

## Instalacja

```bash
# Klonowanie repozytorium
git clone https://github.com/ares-of-ironic/myrient-browser.git
cd myrient-browser

# Instalacja z pip (zalecane użycie venv)
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Lub z uv
uv pip install -e ".[dev]"
```

## Konfiguracja

Skopiuj przykładowy plik konfiguracyjny:

```bash
cp config.example.toml config.toml
```

Edytuj `config.toml` według potrzeb:

```toml
[server]
base_url = "https://myrient.erista.me/files"

[download]
concurrency = 4
retries = 3

[index]
# Użyj JSON dla rozmiarów plików, lub TXT dla samych ścieżek
index_file = "directory/all_paths.json"
watch_enabled = false
watch_interval = 60
```

### Generowanie indeksu z rclone

Aby mieć widoczne rozmiary plików, wygeneruj indeks w formacie JSON:

```bash
# Najpierw skonfiguruj rclone
rclone config create Myrient http url https://myrient.erista.me/files/

# Wygeneruj indeks JSON z rozmiarami
rclone lsjson --recursive Myrient: > directory/all_paths.json

# Alternatywnie, prosty indeks tekstowy (bez rozmiarów)
rclone lsf --recursive Myrient: > directory/all_paths.txt
```

Możesz też użyć zmiennych środowiskowych:

```bash
export MYRIENT_BASE_URL="https://example.com/files"
export MYRIENT_CONCURRENCY=8
```

## Użycie

### Tryb interaktywny (TUI)

```bash
myrient
```

#### Skróty klawiszowe

| Klawisz | Akcja |
|---------|-------|
| `/` | Fokus na wyszukiwaniu |
| `Enter` | Wejście do katalogu |
| `Backspace` | Powrót do katalogu nadrzędnego |
| `Space` | Zaznacz/odznacz element |
| `a` | Zaznacz wszystko w widoku |
| `c` | Wyczyść zaznaczenia |
| `d` | Dodaj zaznaczone do kolejki pobrań |
| `e` | Eksportuj zaznaczone |
| `r` | Przeładuj indeks |
| `m` | Przełącz filtr "tylko brakujące" |
| `q` | Wyjście |
| `Escape` | Wyczyść wyszukiwanie |

### Tryb wsadowy (CLI)

#### Wyszukiwanie

```bash
# Proste wyszukiwanie
myrient search "commodore"

# Wyszukiwanie z OR
myrient search "c64|commodore|amiga"

# Wyświetl URL zamiast ścieżek
myrient search "pacman" --print-urls

# Tylko pliki
myrient search "bios" --files-only

# Tylko katalogi
myrient search "Nintendo" --dirs-only
```

#### Eksport

```bash
# Eksport ścieżek do pliku
myrient export "c64" --out selection.txt

# Eksport jako URL
myrient export "c64" --out selection.txt --urls

# Eksport jako JSON
myrient export "c64" --out selection.json --json

# Podgląd bez zapisu
myrient export "c64" --dry-run
```

#### Kolejka pobrań

```bash
# Dodaj z pliku selekcji
myrient queue --from-selection selection.txt

# Dodaj konkretne ścieżki
myrient queue --paths "MAME/ROMs/pacman.zip" --paths "MAME/ROMs/galaga.zip"

# Podgląd bez dodawania
myrient queue --from-selection selection.txt --dry-run
```

#### Pobieranie

```bash
# Pobierz wszystko z kolejki
myrient download --all-queued

# Ponów nieudane
myrient download --retry-failed

# Status kolejki
myrient download --status
```

#### Status

```bash
myrient status
```

## Struktura projektu

```
myrient.erista.me/
├── src/
│   └── myrient_browser/
│       ├── __init__.py
│       ├── cli.py          # Interfejs CLI
│       ├── config.py       # Konfiguracja
│       ├── downloader.py   # Pobieranie plików
│       ├── exporter.py     # Eksport selekcji
│       ├── indexer.py      # Parsowanie indeksu
│       ├── state.py        # Persystencja stanu
│       └── tui.py          # Interfejs TUI
├── tests/
│   ├── test_config.py
│   ├── test_downloader.py
│   ├── test_exporter.py
│   ├── test_indexer.py
│   └── test_state.py
├── directory/
│   └── all_paths.txt       # Indeks ścieżek
├── downloads/              # Pobrane pliki
├── exports/                # Wyeksportowane selekcje
├── logs/                   # Logi aplikacji
├── config.toml             # Konfiguracja
├── config.example.toml     # Przykładowa konfiguracja
├── pyproject.toml
├── state.json              # Stan kolejki
└── README.md
```

## Testy

```bash
# Uruchom wszystkie testy
pytest

# Z pokryciem kodu
pytest --cov=myrient_browser

# Konkretny plik testowy
pytest tests/test_indexer.py -v
```

## Formaty eksportu

### Ścieżki (domyślny)

```
MAME/ROMs/pacman.zip
MAME/ROMs/galaga.zip
```

### URL

```
https://myrient.erista.me/files/MAME/ROMs/pacman.zip
https://myrient.erista.me/files/MAME/ROMs/galaga.zip
```

### JSON

```json
[
  {
    "path": "MAME/ROMs/pacman.zip",
    "url": "https://myrient.erista.me/files/MAME/ROMs/pacman.zip",
    "is_dir": false,
    "expanded_from_dir": "MAME/ROMs",
    "local_target": "downloads/MAME/ROMs/pacman.zip"
  }
]
```

## Bezpieczeństwo

- Walidacja ścieżek (ochrona przed path traversal)
- Brak opcji "download all" - wymaga świadomej selekcji
- Konfigurowalny rate limiting
- Logi operacji

## Licencja

MIT
