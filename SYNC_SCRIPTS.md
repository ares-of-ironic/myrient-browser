# Skrypty synchronizacji i czyszczenia

Zestaw skryptów do synchronizacji pobranych plików na NAS oraz czyszczenia lokalnego folderu downloads.

## sync_to_nas.sh

Synchronizuje pliki z lokalnego folderu `downloads` na zdalny NAS przez rsync over SSH.

### Wymagania

- `rsync` zainstalowany lokalnie
- Dostęp SSH do NAS-a (klucz SSH skonfigurowany)
- NAS z rsync (QNAP, Synology, itp.)

### Konfiguracja

Edytuj zmienne na początku skryptu:

```bash
# Połączenie SSH
NAS_HOST="192.168.100.120"    # Adres IP NAS-a
NAS_USER="admin"              # Użytkownik SSH
NAS_PORT="22"                 # Port SSH
SSH_KEY=""                    # Ścieżka do klucza (puste = ssh-agent)

# Ścieżki
LOCAL_DIR="$HOME/projekty/myrient.erista.me/downloads/"
REMOTE_DIR="/share/Archiwum/MYRIENT"

# Wydajność
BANDWIDTH_LIMIT=""            # Limit KB/s (puste = bez limitu)
COMPRESS="no"                 # Kompresja (wyłącz dla szybkiego LAN)
CHECKSUM="no"                 # Weryfikacja sumą kontrolną
PARTIAL="yes"                 # Wznawianie przerwanych transferów

# Filtry rozmiaru
MIN_SIZE=""                   # Min rozmiar (np. "100M", "1G")
MAX_SIZE=""                   # Max rozmiar (np. "4G")
```

### Użycie

```bash
# Podgląd (dry-run) - pokaże co zostanie zsynchronizowane
./sync_to_nas.sh -n

# Normalna synchronizacja
./sync_to_nas.sh

# Z verbose output
./sync_to_nas.sh -v

# Usuń pliki na NAS których nie ma lokalnie
./sync_to_nas.sh -d

# Tylko pliki >= 100 MB
./sync_to_nas.sh --min-size 100M

# Tylko pliki <= 1 GB
./sync_to_nas.sh --max-size 1G

# Kombinacja opcji
./sync_to_nas.sh -n -v --min-size 50M --max-size 700M
```

### Opcje

| Opcja | Opis |
|-------|------|
| `-n, --dry-run` | Podgląd bez zmian |
| `-d, --delete` | Usuń pliki na NAS których nie ma lokalnie |
| `-v, --verbose` | Szczegółowy output |
| `-q, --quiet` | Cichy tryb |
| `--min-size SIZE` | Minimalny rozmiar pliku (np. `100M`, `1G`) |
| `--max-size SIZE` | Maksymalny rozmiar pliku |
| `-h, --help` | Pomoc |

### Pomijane pliki

Skrypt automatycznie pomija:
- `*.part` - niedokończone pobierania
- `*.tmp` - pliki tymczasowe
- `*.seg*` - segmenty z równoległego pobierania
- `.DS_Store`, `._*` - metadane macOS
- `Thumbs.db` - miniatury Windows

---

## cleanup_downloads.sh

Usuwa ukończone pobierania z lokalnego folderu, zachowując:
- Wszystkie katalogi (puste i niepuste)
- Pliki częściowe/niedokończone
- Pliki nowsze niż określony czas
- Pliki poza zakresem rozmiaru

### Konfiguracja

```bash
# Folder do czyszczenia
DOWNLOADS_DIR="$HOME/projekty/myrient.erista.me/downloads"

# Minimalny wiek pliku w minutach
MIN_AGE_MINUTES=30

# Filtry rozmiaru
MIN_SIZE=""                   # Min rozmiar do usunięcia
MAX_SIZE=""                   # Max rozmiar do usunięcia
```

### Użycie

```bash
# Podgląd (dry-run) - pokaże co zostanie usunięte
./cleanup_downloads.sh -n

# Podgląd z listą każdego pliku
./cleanup_downloads.sh -n -v

# Faktyczne usunięcie
./cleanup_downloads.sh

# Usuń pliki starsze niż 60 minut
./cleanup_downloads.sh -a 60

# Usuń tylko pliki >= 100 MB
./cleanup_downloads.sh --min-size 100M

# Usuń tylko pliki <= 500 MB
./cleanup_downloads.sh --max-size 500M

# Kombinacja
./cleanup_downloads.sh -n -v --min-size 50M --max-size 1G -a 15
```

### Opcje

| Opcja | Opis |
|-------|------|
| `-n, --dry-run` | Podgląd bez usuwania |
| `-v, --verbose` | Pokaż każdy przetwarzany plik |
| `-a, --age MINUTES` | Minimalny wiek pliku (domyślnie: 30) |
| `--min-size SIZE` | Minimalny rozmiar do usunięcia |
| `--max-size SIZE` | Maksymalny rozmiar do usunięcia |
| `-h, --help` | Pomoc |

### Pomijane pliki

- `*.part` - niedokończone pobierania
- `*.tmp` - pliki tymczasowe  
- `*.seg*` - segmenty z równoległego pobierania

---

## Zalecany workflow

1. **Synchronizuj na NAS:**
   ```bash
   ./sync_to_nas.sh -n          # Sprawdź co zostanie zsynchronizowane
   ./sync_to_nas.sh             # Wykonaj synchronizację
   ```

2. **Zweryfikuj na NAS-ie:**
   ```bash
   ssh admin@192.168.100.120 "ls -la /share/Archiwum/MYRIENT/ | head -20"
   ```

3. **Wyczyść lokalne pliki:**
   ```bash
   ./cleanup_downloads.sh -n -v  # Sprawdź co zostanie usunięte
   ./cleanup_downloads.sh        # Wykonaj czyszczenie
   ```

4. **Wyczyść kolejkę w aplikacji:**
   - Klawisz `k` w zakładce Downloads - usuwa ukończone z kolejki
   - Lub `X` - czyści całą kolejkę

---

## Formaty rozmiaru

Oba skrypty akceptują rozmiary w formacie:

| Suffix | Znaczenie | Przykład |
|--------|-----------|----------|
| `K` | Kilobajty | `500K` = 500 KB |
| `M` | Megabajty | `100M` = 100 MB |
| `G` | Gigabajty | `1G` = 1 GB |
| (brak) | Bajty | `1048576` = 1 MB |

---

## Rozwiązywanie problemów

### Błąd "rsync: unrecognized option"

NAS ma starszą wersję rsync. Skrypt używa tylko kompatybilnych opcji.

### Błąd połączenia SSH

1. Sprawdź czy klucz SSH jest dodany do ssh-agent:
   ```bash
   ssh-add -l
   ```

2. Przetestuj połączenie:
   ```bash
   ssh admin@192.168.100.120 "echo OK"
   ```

3. Sprawdź czy NAS akceptuje klucz:
   ```bash
   ssh -v admin@192.168.100.120
   ```

### Ostrzeżenie "post-quantum key exchange"

To tylko ostrzeżenie - połączenie działa normalnie. Można je zignorować lub zaktualizować OpenSSH na serwerze.

### Transfer jest wolny

1. Wyłącz kompresję dla szybkiego LAN: `COMPRESS="no"`
2. Sprawdź limit przepustowości: `BANDWIDTH_LIMIT=""`
3. Użyj HTTP/1.1 zamiast HTTP/2 w downloaderze
