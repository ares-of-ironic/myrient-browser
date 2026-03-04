# Przewodnik po GitHub - na przykładzie myrient-browser

Ten przewodnik pokazuje podstawy pracy z Git i GitHub na przykładzie projektu `myrient-browser`.

## Spis treści

1. [Instalacja i konfiguracja](#instalacja-i-konfiguracja)
2. [Tworzenie repozytorium](#tworzenie-repozytorium)
3. [Podstawowe operacje](#podstawowe-operacje)
4. [Praca z plikami](#praca-z-plikami)
5. [Przeglądanie historii](#przeglądanie-historii)
6. [Praca z gałęziami](#praca-z-gałęziami)
7. [Współpraca z innymi](#współpraca-z-innymi)
8. [Typowe scenariusze](#typowe-scenariusze)

---

## Instalacja i konfiguracja

### Instalacja Git

```bash
# macOS (przez Homebrew)
brew install git

# Ubuntu/Debian
sudo apt install git

# Sprawdź wersję
git --version
```

### Instalacja GitHub CLI (gh)

```bash
# macOS
brew install gh

# Ubuntu/Debian
sudo apt install gh
```

### Konfiguracja Git (jednorazowo)

```bash
# Ustaw swoją tożsamość
git config --global user.name "Twoje Imię"
git config --global user.email "twoj@email.com"

# Ustaw domyślną gałąź na 'main'
git config --global init.defaultBranch main

# Sprawdź konfigurację
git config --list
```

### Logowanie do GitHub CLI

```bash
gh auth login
```

Pojawi się interaktywny kreator:
1. Wybierz `GitHub.com`
2. Wybierz `HTTPS`
3. Wybierz `Login with a web browser`
4. Skopiuj kod i otwórz przeglądarkę
5. Zaloguj się i wklej kod

Sprawdź status:
```bash
gh auth status
```

---

## Tworzenie repozytorium

### Sposób 1: Lokalne repo → GitHub (tak zrobiliśmy z myrient-browser)

```bash
# 1. Wejdź do folderu projektu
cd /Users/ares/projekty/myrient.erista.me

# 2. Zainicjuj git
git init

# 3. Dodaj wszystkie pliki
git add -A

# 4. Utwórz pierwszy commit
git commit -m "Initial commit: opis projektu"

# 5. Utwórz repo na GitHub i wypchnij
gh repo create myrient-browser --public --source=. --remote=origin --push
```

### Sposób 2: Najpierw GitHub, potem klonowanie

```bash
# 1. Utwórz puste repo na GitHub
gh repo create moja-aplikacja --public

# 2. Sklonuj na dysk
git clone https://github.com/TWOJ-LOGIN/moja-aplikacja.git

# 3. Wejdź do folderu
cd moja-aplikacja

# 4. Dodaj pliki, commituj, wypychaj...
```

### Sposób 3: Przez stronę github.com

1. Wejdź na https://github.com/new
2. Wypełnij formularz
3. Sklonuj: `git clone https://github.com/TWOJ-LOGIN/nazwa-repo.git`

---

## Podstawowe operacje

### Sprawdzanie stanu

```bash
# Co się zmieniło?
git status

# Skrócona wersja
git status -s
```

### Dodawanie plików do śledzenia (staging)

```bash
# Dodaj konkretny plik
git add plik.txt

# Dodaj wszystkie pliki w folderze
git add src/

# Dodaj wszystkie zmiany
git add -A
# lub
git add .

# Dodaj interaktywnie (wybierz co dodać)
git add -p
```

### Commitowanie (zapisywanie zmian)

```bash
# Prosty commit
git commit -m "Dodano nową funkcję"

# Commit z dłuższym opisem
git commit -m "Tytuł commita" -m "Dłuższy opis zmian..."

# Commit z edytorem (otworzy vim/nano)
git commit
```

### Wypychanie na GitHub

```bash
# Wypchnij zmiany
git push

# Pierwszy push nowej gałęzi
git push -u origin main

# Wypchnij konkretną gałąź
git push origin nazwa-galezi
```

### Pobieranie zmian z GitHub

```bash
# Pobierz i scal zmiany
git pull

# Tylko pobierz (bez scalania)
git fetch
```

---

## Praca z plikami

### .gitignore - ignorowanie plików

Plik `.gitignore` określa co NIE ma być śledzone. Przykład z myrient-browser:

```gitignore
# Python
__pycache__/
*.py[cod]
*.egg-info/

# Środowisko wirtualne
.venv/
venv/

# IDE
.idea/
.vscode/

# Pliki projektu (prywatne/duże)
config.toml      # konfiguracja użytkownika
state.json       # stan aplikacji
logs/            # logi
downloads/       # pobrane pliki
directory/       # duże pliki indeksu

# System
.DS_Store
```

### Dodawanie screenshotów/obrazków

```bash
# 1. Utwórz folder
mkdir -p docs/screenshots

# 2. Skopiuj obrazki
cp ~/Desktop/screenshot.png docs/screenshots/

# 3. Dodaj do git
git add docs/screenshots/
git commit -m "Add screenshots"
git push
```

W README.md odwołuj się tak:
```markdown
![Opis obrazka](docs/screenshots/screenshot.png)
```

### Usuwanie plików

```bash
# Usuń plik z git i z dysku
git rm plik.txt

# Usuń tylko z git (zostaw na dysku)
git rm --cached plik.txt

# Usuń folder
git rm -r folder/
```

### Przenoszenie/zmiana nazwy

```bash
git mv stara-nazwa.txt nowa-nazwa.txt
git commit -m "Rename file"
```

---

## Przeglądanie historii

### Log commitów

```bash
# Pełna historia
git log

# Skrócona (jedna linia na commit)
git log --oneline

# Z grafem gałęzi
git log --oneline --graph

# Ostatnie 5 commitów
git log -5

# Historia konkretnego pliku
git log -- src/myrient_browser/tui.py

# Kto co zmienił (blame)
git blame plik.py
```

### Różnice (diff)

```bash
# Co się zmieniło (niezacommitowane)
git diff

# Co jest w staging (do commita)
git diff --staged

# Różnica między commitami
git diff abc123 def456

# Różnica z poprzednim commitem
git diff HEAD~1
```

### Cofanie zmian

```bash
# Cofnij zmiany w pliku (przed staging)
git checkout -- plik.txt
# lub nowsza składnia:
git restore plik.txt

# Usuń plik ze staging (ale zachowaj zmiany)
git reset HEAD plik.txt
# lub:
git restore --staged plik.txt

# Cofnij ostatni commit (zachowaj zmiany)
git reset --soft HEAD~1

# Cofnij ostatni commit (usuń zmiany) - OSTROŻNIE!
git reset --hard HEAD~1
```

---

## Praca z gałęziami

Gałęzie pozwalają pracować nad różnymi funkcjami równolegle.

### Podstawowe operacje

```bash
# Lista gałęzi
git branch

# Utwórz nową gałąź
git branch nowa-funkcja

# Przełącz się na gałąź
git checkout nowa-funkcja
# lub nowsza składnia:
git switch nowa-funkcja

# Utwórz i przełącz (skrót)
git checkout -b nowa-funkcja
# lub:
git switch -c nowa-funkcja

# Usuń gałąź
git branch -d nowa-funkcja
```

### Scalanie gałęzi (merge)

```bash
# 1. Przełącz na gałąź docelową
git checkout main

# 2. Scal inną gałąź
git merge nowa-funkcja

# 3. Wypchnij
git push
```

### Przykład: dodanie nowej funkcji

```bash
# 1. Utwórz gałąź
git checkout -b feature/dark-mode

# 2. Wprowadź zmiany...
# (edycja plików)

# 3. Commituj
git add -A
git commit -m "Add dark mode support"

# 4. Wypchnij gałąź
git push -u origin feature/dark-mode

# 5. Utwórz Pull Request na GitHub
gh pr create --title "Add dark mode" --body "Opis zmian..."

# 6. Po zaakceptowaniu PR, wróć do main
git checkout main
git pull
```

---

## Współpraca z innymi

### Klonowanie cudzego repo

```bash
git clone https://github.com/user/repo.git
cd repo
```

### Fork (kopia repo na swoje konto)

```bash
# Przez gh CLI
gh repo fork user/repo

# Sklonuj swojego forka
git clone https://github.com/TWOJ-LOGIN/repo.git
```

### Pull Request (propozycja zmian)

```bash
# 1. Utwórz gałąź ze zmianami
git checkout -b moja-poprawka

# 2. Wprowadź zmiany i commituj
git add -A
git commit -m "Fix bug in download manager"

# 3. Wypchnij
git push -u origin moja-poprawka

# 4. Utwórz PR
gh pr create --title "Fix download bug" --body "Opis..."
```

### Przeglądanie PR

```bash
# Lista PR w repo
gh pr list

# Szczegóły PR
gh pr view 123

# Sprawdź PR lokalnie
gh pr checkout 123
```

---

## Typowe scenariusze

### Scenariusz 1: Codzienna praca

```bash
# Rano - pobierz najnowsze zmiany
git pull

# Pracuj nad kodem...

# Sprawdź co się zmieniło
git status
git diff

# Dodaj i commituj
git add -A
git commit -m "Opis zmian"

# Wypchnij na koniec dnia
git push
```

### Scenariusz 2: "Ups, zapomniałem dodać plik do commita"

```bash
# Dodaj brakujący plik
git add zapomniany-plik.txt

# Dołącz do ostatniego commita
git commit --amend --no-edit

# Jeśli już wypchnąłeś (OSTROŻNIE!)
git push --force
```

### Scenariusz 3: "Chcę cofnąć ostatnie zmiany"

```bash
# Jeśli NIE commitowałeś
git checkout -- .
# lub:
git restore .

# Jeśli commitowałeś, ale NIE wypchnąłeś
git reset --soft HEAD~1  # zachowaj zmiany
git reset --hard HEAD~1  # usuń zmiany

# Jeśli wypchnąłeś - utwórz "odwracający" commit
git revert HEAD
git push
```

### Scenariusz 4: "Mam konflikt przy merge/pull"

```bash
# 1. Git pokaże pliki z konfliktem
git status

# 2. Otwórz plik - znajdź znaczniki:
<<<<<<< HEAD
twoja wersja
=======
ich wersja
>>>>>>> branch-name

# 3. Edytuj plik - zostaw właściwą wersję

# 4. Oznacz jako rozwiązany
git add plik-z-konfliktem.txt

# 5. Dokończ merge
git commit
```

### Scenariusz 5: Dodanie screenshotów do myrient-browser

```bash
cd /Users/ares/projekty/myrient.erista.me

# 1. Utwórz folder
mkdir -p docs/screenshots

# 2. Skopiuj screenshoty
cp ~/Desktop/browser.png docs/screenshots/
cp ~/Desktop/downloads.png docs/screenshots/

# 3. Dodaj do README.md
# ![Browser](docs/screenshots/browser.png)

# 4. Commituj i wypchnij
git add docs/ README.md
git commit -m "Add screenshots to documentation"
git push
```

---

## Przydatne komendy gh CLI

```bash
# Repozytorium
gh repo create nazwa --public       # utwórz publiczne repo
gh repo clone user/repo             # sklonuj
gh repo view                        # info o repo
gh repo view --web                  # otwórz w przeglądarce

# Pull Requests
gh pr create                        # utwórz PR
gh pr list                          # lista PR
gh pr view 123                      # szczegóły PR
gh pr checkout 123                  # pobierz PR lokalnie
gh pr merge 123                     # scal PR

# Issues
gh issue create                     # utwórz issue
gh issue list                       # lista issues
gh issue view 123                   # szczegóły

# Inne
gh auth status                      # status logowania
gh browse                           # otwórz repo w przeglądarce
```

---

## Dobre praktyki

1. **Commituj często** - małe, logiczne zmiany
2. **Pisz dobre opisy commitów** - co i dlaczego
3. **Używaj .gitignore** - nie commituj śmieci
4. **Nie commituj sekretów** - hasła, klucze API
5. **Rób pull przed push** - unikaj konfliktów
6. **Używaj gałęzi** - dla nowych funkcji
7. **Rób code review** - przez Pull Requests

---

## Linki

- [Dokumentacja Git](https://git-scm.com/doc)
- [GitHub Docs](https://docs.github.com)
- [gh CLI Manual](https://cli.github.com/manual/)
- [Git Cheat Sheet](https://education.github.com/git-cheat-sheet-education.pdf)
