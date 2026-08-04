# Bot Discord — mieszkania Wrocław (OLX + Otodom)

Monitoruje nowe ogłoszenia **wynajmu mieszkań we Wrocławiu** na **OLX i Otodom** i wysyła je
na Twój kanał Discord. Sprawdza co minutę i wrzuca **tylko nowe** oferty.

**Domyślne kryteria** (do zmiany w `config.json`):
- Dzielnice: **Śródmieście, Stare Miasto, Ołbin, Plac Grunwaldzki, Nadodrze + okolice PWR**
- **Bez Psiego Pola** (i innych dzielnic spoza listy)
- Min. **40 m²**, **2 lub 3 pokoje**
- Najem do **3000 zł**, łącznie z czynszem do **4000 zł**
- **Prysznic**: odrzuca oferty, które w opisie mówią *tylko o wannie*
  (oferty bez info o łazience przechodzą — oznaczone ❔, dopytaj właściciela)

Bot nie ma żadnych zależności — działa na czystym Pythonie. Nic nie trzeba instalować.

---

## Status: już działa i sam się włącza ✅

Bot jest zarejestrowany jako zadanie Windows **„MieszkaniaBot"**, które:
- **startuje automatycznie przy każdym zalogowaniu**,
- chodzi **w tle, bez widocznego okna**,
- co minutę sprawdza OLX + Otodom i wrzuca nowe oferty na Discorda.

> ⚠️ „Online" = działa, gdy komputer jest włączony i jesteś zalogowany. Gdy komputer śpi
> lub jest wyłączony, bot też śpi. Żeby chodził **naprawdę 24/7**, trzeba go postawić na
> serwerze/VPS działającym cały czas (mogę pomóc to ustawić — napisz).

### Sterowanie autostartem
- **Wyłączyć / zatrzymać:** uruchom `autostart-wylacz.bat`
- **Włączyć ponownie:** uruchom `autostart-wlacz.bat`
- **Ręczne uruchomienie z podglądem w oknie:** `start.bat` (nie odpalaj razem z zadaniem,
  żeby nie mieć dwóch instancji = podwójnych wiadomości)

---

## Konfiguracja (`config.json`)

| Pole | Znaczenie |
|------|-----------|
| `discord_webhook` | URL webhooka Discorda |
| `bot_name` | Nazwa, pod którą bot pisze na kanale |
| `sources` | Które serwisy sprawdzać, np. `{"olx": true, "otodom": true}` |
| `min_area` | Minimalny metraż (m²) |
| `max_price` | Maks. cena najmu bez czynszu (zł) |
| `max_total` | Maks. cena **z czynszem** (zł); `0` = wyłącz |
| `rooms` | Lista dozwolonej liczby pokoi, np. `[2, 3]` |
| `radius_km` | Promień wokół centrum (dla ofert OLX z dokładną lokalizacją GPS) |
| `center_lat` / `center_lon` | Punkt centralny (domyślnie okolice pl. Grunwaldzkiego / PWR) |
| `district_allowlist` | Dzielnice akceptowane (gdy brak GPS): domyślnie Śródmieście, Stare Miasto |
| `district_blocklist` | Dzielnice **zawsze odrzucane**: domyślnie `["Psie Pole"]` |
| `title_keywords` | Słowa (tytuł / osiedle / ulica), które od razu kwalifikują ofertę |
| `shower_filter` | `"exclude_bath_only"` (domyślne), `"required"`, lub `"off"` |
| `poll_interval_seconds` | Co ile sekund sprawdzać (60 = 1 min) |
| `max_offer_age_hours` | Maks. wiek oferty liczony od **pierwszej** publikacji (domyślnie 72 h); `0` = wyłącz |
| `dedup_by_content` | `true` = rozpoznaje tę samą ofertę po treści i zdjęciu, nie tylko po ID |
| `seen_retention_days` | Ile dni pamiętać wysłane oferty (domyślnie 60) |
| `first_run_posts` | Ile ofert wysłać przy pierwszym starcie (domyślnie `0` = cisza) |
| `max_posts_per_run` | Limit ogłoszeń na jedno sprawdzenie |
| `run_once` | `true` = jedno sprawdzenie i koniec |

**Uwaga:** po zmianie `config.json` zrestartuj bota (wyłącz/włącz autostart), żeby wczytał nowe ustawienia.

Przykłady:
- Chcesz też kawalerki: `"rooms": [1, 2, 3]`.
- Tylko oferty z jednoznacznym prysznicem: `"shower_filter": "required"`.
- Odrzucić też Krzyki i Fabryczną: `"district_blocklist": ["Psie Pole", "Krzyki", "Fabryczna"]`.
- Wyłączyć jedno źródło: `"sources": {"olx": true, "otodom": false}`.

---

## Bez powtórek i „odświeżonych" ofert

Ta sama oferta nie przyjdzie drugi raz — bot pilnuje tego na cztery sposoby:

1. **Wiek oferty** (`max_offer_age_hours`, domyślnie 72 h) — liczony od **pierwszej**
   publikacji (OLX `created_time`, Otodom `dateCreatedFirst`). Ogłoszenie „odświeżone"
   / podbite wraca na górę listy, ale jego pierwotna data się nie zmienia, więc bot
   je pomija.
2. **Odcisk treści** — tytuł + metraż + pokoje + dzielnica. Łapie ofertę **wystawioną
   ponownie z nowym ID** (skasowana i dodana od nowa, też po zmianie ceny).
3. **Zdjęcie** — identyfikator z CDN. OLX i Otodom trzymają zdjęcia na tym samym
   serwerze, więc to samo mieszkanie wystawione w obu serwisach leci tylko raz.
4. **Trwała pamięć** (`seen.json`) — zapis atomowy i **przed** wysyłką, z datami i
   czyszczeniem po `seen_retention_days`. Wcześniej plik potrafił zgubić część
   wpisów przy przycinaniu.

Dodatkowo `first_run_posts` domyślnie wynosi **0**: przy pierwszym starcie (albo gdy
w chmurze przepadnie cache `seen.json`) bot tylko zapamiętuje aktualny stan i nic nie
wysyła — właśnie to powodowało powtórne wysyłanie tych samych 5 ofert. Ustaw np.
`"first_run_posts": 3`, jeśli chcesz podgląd przy starcie.

Jeśli któraś oferta zostanie odrzucona za ostro (np. bot ma pokazywać też starsze
ogłoszenia): zwiększ `max_offer_age_hours` albo ustaw `"dedup_by_content": false`.

## Jak działa lokalizacja

- **OLX** podaje współrzędne GPS — bot liczy odległość od centrum (`radius_km`).
- **Otodom** podaje osiedle (np. Ołbin, Nadodrze, Szczepin) i dzielnicę — bot filtruje po nazwach.
- W obu źródłach: dzielnice z `district_blocklist` (Psie Pole) są zawsze pomijane,
  a nazwy z `title_keywords` (Ołbin, Nadodrze, PWR…) od razu kwalifikują ofertę.

## Uwagi

- **Czynsz:** „cena" to najem dla właściciela; czynsz administracyjny jest osobno.
  Bot pokazuje obie kwoty + sumę, a `max_total` pilnuje łącznego kosztu.
- **Prysznic:** żaden serwis nie ma osobnego pola, więc bot czyta to z opisu.
- **Bezpieczeństwo:** webhook siedzi w `config.json`. Jeśli komuś wyciekł, zresetuj go w
  Discordzie (Integracje → Webhooki → nowy URL) i podmień w pliku.

## Pliki

- `bot.py` — cały bot (OLX + Otodom).
- `config.json` — Twoje ustawienia.
- `seen.json` — pamięć wysłanych ogłoszeń (nie kasuj, żeby nie dostać duplikatów).
- `start.bat` — ręczne uruchomienie z oknem.
- `autostart-wlacz.bat` / `autostart-wylacz.bat` — włącz/wyłącz automatyczny start.
