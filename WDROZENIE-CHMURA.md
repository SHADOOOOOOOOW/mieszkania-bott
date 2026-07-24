# Wdrożenie w chmurze (24/7, komputer wyłączony)

Bot będzie się uruchamiał **na serwerach GitHuba co 15 minut** — Twój PC może być wyłączony.
Za darmo. Poniżej 6 kroków (~5 minut).

---

## 1. Konto GitHub
Jeśli nie masz — załóż darmowe na <https://github.com/signup>.

## 2. Nowe repozytorium
Wejdź na <https://github.com/new> i:
- **Repository name:** `mieszkania-bot`
- Ustaw na **Public** ← ważne, żeby GitHub Actions były darmowe bez limitu
  (kod jest jawny, ale **webhook NIE jest w kodzie** — trzymamy go w sekretach)
- **NIE** zaznaczaj „Add a README"
- Kliknij **Create repository**

## 3. Wyślij kod do repozytorium
Otwórz PowerShell w folderze bota i wpisz (podmień `TWOJA-NAZWA` na swój login GitHub):

```powershell
cd C:\Users\borys\discord-mieszkania-bot
git branch -M main
git remote add origin https://github.com/TWOJA-NAZWA/mieszkania-bot.git
git push -u origin main
```

Przy `git push` otworzy się okno logowania do GitHuba — zaloguj się (to jednorazowe).

## 4. Dodaj sekrety (webhook + rola)
W repozytorium: **Settings → Secrets and variables → Actions → New repository secret**.
Dodaj **dwa** sekrety:

| Name | Secret (wartość) |
|------|------------------|
| `DISCORD_WEBHOOK` | Twój URL webhooka Discorda |
| `ROLE_ID` | `1530308471394664498` |

> 💡 Skoro webhook był wklejany na czacie — najpierw **zresetuj go** w Discordzie
> (Integracje → Webhooki → nowy URL) i użyj świeżego jako `DISCORD_WEBHOOK`.

## 5. Włącz i przetestuj
- Wejdź w zakładkę **Actions**. Jeśli pojawi się prośba — kliknij, że rozumiesz i włącz workflowy.
- Wybierz workflow **„mieszkania-bot"** → **Run workflow** → **Run** (ręczne uruchomienie).
- Po chwili na Discordzie pojawi się **„✅ Bot uruchomiony"** + kilka aktualnych ofert.
- Od tej pory bot chodzi **sam co 15 minut** — nic nie musisz robić.

## 6. Wyłącz wersję lokalną (żeby nie było podwójnych wiadomości)
Na komputerze uruchom **`autostart-wylacz.bat`**. Od teraz wszystko robi chmura.

---

## Jak to się zachowuje
- **Znajdzie nowe oferty** → pinguje rolę `@mieszkania` i wrzuca ogłoszenia.
- **Nie znajdzie nic nowego** → pisze `not found` (potwierdzenie, że sprawdził).

## Dostrajanie
- **Za dużo „not found"?** W pliku `bot.py` w sekcji `DEFAULT_CONFIG` ustaw
  `"notify_when_empty": False`, zrób `git commit -am "cisza" && git push`.
- **Rzadsze sprawdzanie?** W `.github/workflows/bot.yml` zmień `*/15` na `*/30` (co 30 min).
- **Inne kryteria** (cena, dzielnice)? Edytuj `DEFAULT_CONFIG` w `bot.py`, potem commit + push.

## Gdyby oferty przestały przychodzić
Portale (zwł. Otodom) czasem blokują ruch z serwerowni. Bot łapie błąd źródła i próbuje
dalej przy kolejnym uruchomieniu. Jeśli jedno źródło pada na stałe z chmury — daj znać,
przełączę na tańszy VPS z innym adresem IP.
