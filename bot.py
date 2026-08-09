#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bot Discord: monitoruje nowe ogloszenia wynajmu mieszkan we Wroclawiu
(OLX + Otodom) i wysyla je na kanal Discord przez webhook.

Kryteria (domyslne, do zmiany w config.json):
  - dzielnice: Srodmiescie, Stare Miasto, Olbin, Plac Grunwaldzki, Nadodrze + okolice PWR
  - BEZ Psiego Pola
  - min. 40 m2, 2 lub 3 pokoje
  - najem od 2500 do 3000 zl, z czynszem do 4000 zl
  - tylko CALE mieszkania (pokoje/stancje/wspollokatorzy odrzucane)
  - odrzuca oferty wspominajace tylko wanne (bez info o lazience -> przepuszcza)
  - bez powtorek: pomija oferty odswiezone/wystawione ponownie (nawet z nowym id)

Zero zaleznosci - dziala na czystym Pythonie (>=3.8). Wystarczy wkleic webhook do config.json.
"""

import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
SEEN_PATH = os.path.join(HERE, "seen.json")

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# OLX (Wroclaw / mieszkania na wynajem)
OLX_API = "https://www.olx.pl/api/v1/offers/"
OLX_CATEGORY_ID = 15       # mieszkania - wynajem
OLX_CITY_ID = 19701        # Wroclaw
OLX_REGION_ID = 3          # Dolnoslaskie

# Otodom (Wroclaw / wynajem mieszkania)
OTODOM_URL = "https://www.otodom.pl/pl/wyniki/wynajem/mieszkanie/dolnoslaskie/wroclaw/wroclaw/wroclaw"

DEFAULT_CONFIG = {
    "discord_webhook": "WKLEJ_TUTAJ_URL_WEBHOOKA",
    "bot_name": "bot mieszkania",
    "sources": {"olx": True, "otodom": True},

    "min_area": 40,               # minimalny metraz [m2]
    "min_price": 2500,            # min. cena najmu (bez czynszu) [zl]; 0 = wylaczone
    "max_price": 3000,            # maks. cena najmu (bez czynszu) [zl]
    "max_total": 4000,            # maks. cena + czynsz [zl]; 0 = wylaczone
    "rooms": [2, 3],              # dozwolona liczba pokoi

    # Filtr geograficzny.
    # Dla ofert z dokladna lokalizacja GPS (OLX) liczymy odleglosc od punktu center.
    "center_lat": 51.1130,
    "center_lon": 17.0470,
    "radius_km": 2.5,
    # Dla ofert bez GPS uzywamy dzielnicy administracyjnej (OLX/Otodom).
    "district_allowlist": ["Srodmiescie", "Stare Miasto"],
    # Dzielnice odrzucane zawsze (nawet gdyby GPS pasowal).
    "district_blocklist": ["Psie Pole"],
    # Slowa (tytul/osiedle/ulica), ktore od razu kwalifikuja oferte.
    "title_keywords": ["olbin", "nadodrze", "plac grunwaldzki", "grunwaldzki",
                        "politechnik", "pwr", "stare miasto", "srodmiesci",
                        "kleczkow", "ostrow tumski"],

    # Weryfikacja prysznica (brak osobnego pola - szukamy w opisie):
    #   "required" | "exclude_bath_only" (domyslne) | "off"
    "shower_filter": "exclude_bath_only",
    "shower_keywords": ["prysznic", "natrysk"],
    "bath_keywords": ["wann"],

    # --- Tylko cale mieszkania (bez pokoi / stancji / wspollokatorow) ---
    "whole_flat_only": True,
    # Zwroty jednoznaczne dla wynajmu POKOJU - sprawdzane w tytule i w opisie.
    # (porownanie bez polskich znakow i wielkosci liter)
    "room_keywords": [
        "wynajme pokoj", "wynajmie pokoj", "wynajem pokoju", "wynajem pokoi",
        "do wynajecia pokoj", "pokoj do wynajecia", "pokoje do wynajecia",
        "pokoj do wynajmu", "pokoje do wynajmu", "pokoj na wynajem",
        "pokoj dla studenta", "pokoj dla studentki", "pokoj dla pary",
        "pokoj dla jednej osoby", "pokoj dla dwoch",
        "pokoj jednoosobowy", "pokoj dwuosobowy", "pokoj 1-osobowy", "pokoj 2-osobowy",
        "pokoj 1 osobowy", "pokoj 2 osobowy", "pokoj z lozkiem",
        "miejsce w pokoju", "lozko w pokoju", "stancja", "stancje",
        "kwatera pracownicza", "kwatery pracownicze", "pokoje pracownicze",
        "miejsca noclegowe", "wspollokator", "wspolokator", "wspollokatork",
        "wspolne mieszkanie", "mieszkanie dzielone", "dzielone mieszkanie",
        "do wspoldzielenia", "wspolzamieszkanie", "wynajem wspolny",
        "room for rent", "rooms for rent", "private room", "single room",
        "shared flat", "shared apartment", "flatmate", "roommate",
        "coliving", "co-living",
    ],
    # Zwroty sprawdzane TYLKO w tytule - w opisie calego mieszkania moglyby
    # wystapic niewinnie (np. "media ok. 100 zl za osobe").
    "room_title_keywords": [
        "pokoj w mieszkaniu", "pokoj w apartamencie", "pokoj w centrum",
        "za osobe", "od osoby", "na osobe", "za os", "kwatera", "kwatery",
        "hostel", "akademik",
    ],

    # --- Anty-duplikaty ---
    # Odrzuca oferty "odswiezone"/wystawione ponownie: liczy sie data PIERWSZEJ
    # publikacji, wiec podbite stare ogloszenie nie wraca jako nowe. 0 = wylaczone.
    "max_offer_age_hours": 72,
    # Oprocz ID porownuje tez trescia (tytul + metraz + pokoje + dzielnica) i
    # zdjeciem - to lapie oferte wystawiona ponownie z NOWYM id (tez miedzy OLX/Otodom).
    "dedup_by_content": True,
    # Jak dlugo pamietac wyslane oferty (dni).
    "seen_retention_days": 60,

    # Ping roli, gdy znajdzie nowe oferty (ID roli @mieszkania; puste = bez pingu).
    "role_id": "",
    # Gdy w danym sprawdzeniu nie ma nowych ofert - domyslnie NIC nie pisz (cisza).
    "notify_when_empty": False,
    "not_found_text": "not found",

    "poll_interval_seconds": 60,   # co ile sekund sprawdzac (60 = 1 min)
    "first_run_posts": 0,          # ile aktualnych ofert wyslac przy pierwszym starcie
                                   # (0 = cisza: bot tylko zapamietuje stan, zeby po
                                   #  utracie cache nie wysylac drugi raz tego samego)
    "max_posts_per_run": 25,       # bezpiecznik przed zalaniem kanalu
    "run_once": False              # True = jedno sprawdzenie i koniec
}


# --- Konfiguracja / stan -------------------------------------------------------
def load_config():
    file_exists = os.path.exists(CONFIG_PATH)
    if file_exists:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, v)
    else:
        cfg = dict(DEFAULT_CONFIG)   # tryb chmurowy: ustawienia z DEFAULT + zmiennych srodowiskowych
    # Nadpisania ze zmiennych srodowiskowych (GitHub Actions / hosting - sekrety).
    cfg["discord_webhook"] = os.environ.get("DISCORD_WEBHOOK", cfg["discord_webhook"])
    if os.environ.get("ROLE_ID"):
        cfg["role_id"] = os.environ["ROLE_ID"]
    if os.environ.get("RUN_ONCE") == "1":
        cfg["run_once"] = True
    if not cfg["discord_webhook"] or "WKLEJ" in cfg["discord_webhook"]:
        if not file_exists:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
            print("Utworzono config.json. Wklej URL webhooka w 'discord_webhook' i uruchom ponownie.")
            sys.exit(0)
        print("BLAD: brak webhooka Discorda (config.json lub sekret DISCORD_WEBHOOK).")
        sys.exit(1)
    return cfg


# Pamiec wyslanych ofert: {klucz: znacznik_czasu}. Kluczy na oferte jest kilka
# (id, odcisk tresci, zdjecie) - wystarczy trafienie jednego, zeby uznac za znana.
def load_seen():
    if not os.path.exists(SEEN_PATH):
        return {}
    try:
        with open(SEEN_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    ts = time.time()
    if isinstance(data, list):                      # stary format: lista id
        return {"id:" + str(k): ts for k in data}
    if isinstance(data, dict):
        keys = data.get("keys", data)
        out = {}
        if isinstance(keys, dict):
            for k, v in keys.items():
                try:
                    out[str(k)] = float(v)
                except (TypeError, ValueError):
                    out[str(k)] = ts
        return out
    return {}


def save_seen(seen, cfg=None):
    days = (cfg or {}).get("seen_retention_days", 60)
    cutoff = time.time() - float(days) * 86400
    items = sorted(((k, v) for k, v in seen.items() if v >= cutoff), key=lambda kv: kv[1])
    del items[:-60000]                              # twardy limit rozmiaru pliku
    seen.clear()
    seen.update(items)                              # trzymamy tez pamiec RAM przycieta
    tmp = SEEN_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": 2, "keys": seen}, f)
    os.replace(tmp, SEEN_PATH)                      # zapis atomowy - nie zgubimy pliku


# --- HTTP ----------------------------------------------------------------------
def http_get(url, as_json=False):
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept-Language": "pl-PL,pl;q=0.9",
        "Accept": "application/json" if as_json else "text/html",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode("utf-8", "replace")
    return json.loads(raw) if as_json else raw


# --- Narzedzia -----------------------------------------------------------------
_PL = str.maketrans("ąćęłńóśżźĄĆĘŁŃÓŚŻŹ", "acelnoszzACELNOSZZ")


def strip_pl(s):
    return (s or "").translate(_PL).lower()


def to_number(s):
    if s is None:
        return None
    m = re.search(r"\d[\d\s.,]*", str(s).replace("\xa0", " "))
    if not m:
        return None
    num = m.group(0).replace(" ", "").replace(",", ".")
    if num.count(".") > 1:
        parts = num.split(".")
        num = "".join(parts[:-1]) + "." + parts[-1]
    try:
        return float(num)
    except ValueError:
        return None


def iso(dt):
    return (dt or "").replace(" ", "T")


# --- Daty ----------------------------------------------------------------------
PL_TZ = timezone(timedelta(hours=2))     # daty bez strefy (Otodom) traktujemy jak czas PL
_DT_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})"
                    r"(?:\.\d+)?\s*(Z|[+-]\d{2}:?\d{2})?")


def parse_dt(value):
    m = _DT_RE.search(str(value or ""))
    if not m:
        return None
    y, mo, d, h, mi, s = (int(x) for x in m.groups()[:6])
    off = m.group(7)
    if not off:
        tz = PL_TZ
    elif off == "Z":
        tz = timezone.utc
    else:
        off = off.replace(":", "")
        sign = -1 if off[0] == "-" else 1
        tz = timezone(sign * timedelta(hours=int(off[1:3]), minutes=int(off[3:5])))
    try:
        return datetime(y, mo, d, h, mi, s, tzinfo=tz)
    except ValueError:
        return None


def age_hours(offer):
    """Wiek oferty liczony od PIERWSZEJ publikacji (nie od odswiezenia)."""
    dt = parse_dt(offer.get("created_first") or offer.get("created"))
    if not dt:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0


# --- Anty-duplikaty ------------------------------------------------------------
def _norm_text(s):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", strip_pl(s))).strip()


def content_key(offer):
    """Odcisk tresci - ta sama oferta wystawiona ponownie ma inne id, ale to samo
    ogloszenie (tytul + metraz + pokoje + dzielnica)."""
    title = _norm_text(offer.get("title"))
    if len(title) < 8:
        return None
    parts = [title,
             "{:g}".format(offer["area"]) if offer.get("area") else "?",
             str(offer.get("rooms") or "?"),
             _norm_text(offer.get("district"))]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def photo_key(offer):
    """Identyfikator zdjecia z CDN - przy ponownym wystawieniu zdjecia sa te same
    (OLX i Otodom trzymaja je na tym samym CDN, wiec lapie tez duble miedzy serwisami)."""
    url = (offer.get("photo") or "").split("?")[0].split(";")[0]
    for seg in reversed([s for s in url.split("/") if s]):
        seg = re.sub(r"\.(jpe?g|png|webp)$", "", seg.split(":")[0], flags=re.I)
        if seg.lower() in ("image", "images", "files", "v1", "photo", "photos"):
            continue
        if re.match(r"^[A-Za-z0-9_-]{8,}$", seg):
            return seg.lower()
    return None


def offer_keys(offer, cfg):
    keys = ["id:" + offer["id"]]
    if cfg.get("dedup_by_content", True):
        ck = content_key(offer)
        if ck:
            keys.append("fp:" + ck)
        pk = photo_key(offer)
        if pk:
            keys.append("img:" + pk)
    return keys


def is_seen(offer, seen, cfg):
    return any(k in seen for k in offer_keys(offer, cfg))


def mark_seen(offer, seen, cfg):
    ts = time.time()
    for k in offer_keys(offer, cfg):
        seen.setdefault(k, ts)


# --- Zrodlo: OLX ---------------------------------------------------------------
def olx_url(cfg, limit=50):
    params = {
        "offset": 0, "limit": limit,
        "category_id": OLX_CATEGORY_ID, "city_id": OLX_CITY_ID, "region_id": OLX_REGION_ID,
        "filter_float_price:to": cfg["max_price"],
        "filter_float_m:from": cfg["min_area"],
        "sort_by": "created_at:desc",
    }
    if cfg.get("min_price"):
        params["filter_float_price:from"] = cfg["min_price"]
    query = urllib.parse.urlencode(params, safe=":")
    room_map = {1: "one", 2: "two", 3: "three", 4: "four"}
    parts = ["filter_enum_rooms[%d]=%s" % (i, room_map[r])
             for i, r in enumerate(cfg["rooms"]) if r in room_map]
    if parts:
        query += "&" + "&".join(parts)
    return OLX_API + "?" + query


def parse_olx(o):
    p = {x.get("key"): x.get("value", {}) for x in o.get("params", [])}
    price_v = p.get("price", {})
    price = price_v.get("value") if isinstance(price_v, dict) else None
    if price is None:
        price = to_number(price_v.get("label") if isinstance(price_v, dict) else price_v)
    rent = to_number((p.get("rent", {}) or {}).get("key") or (p.get("rent", {}) or {}).get("label"))
    area = to_number((p.get("m", {}) or {}).get("label") or (p.get("m", {}) or {}).get("key"))
    rooms = {"one": 1, "two": 2, "three": 3, "four": 4}.get((p.get("rooms", {}) or {}).get("key"))
    mp = o.get("map", {}) or {}
    photos = o.get("photos") or []
    photo = photos[0].get("link", "").replace("{width}", "800").replace("{height}", "600") if photos else None
    return {
        "id": "olx:" + str(o.get("id")),
        "source": "OLX",
        "title": (o.get("title") or "").strip(),
        "url": o.get("url"),
        "desc": o.get("description") or "",
        "price": price,
        "rent": rent,
        "total": (price + (rent or 0)) if price is not None else None,
        "area": area,
        "rooms": rooms,
        "floor": (p.get("floor_select", {}) or {}).get("label"),
        "furniture": (p.get("furniture", {}) or {}).get("label"),
        "district": (o.get("location", {}).get("district") or {}).get("name"),
        "osiedle": None,
        "street": None,
        "lat": mp.get("lat"),
        "lon": mp.get("lon"),
        "detailed": bool(mp.get("show_detailed")),
        "photo": photo,
        "created": iso(o.get("created_time")),
        # data pierwszej publikacji - "odswiezenie"/podbicie jej nie zmienia
        "created_first": iso(o.get("created_time")),
        "refreshed": iso(o.get("last_refresh_time") or o.get("pushup_time")),
    }


def fetch_olx(cfg):
    data = http_get(olx_url(cfg), as_json=True)
    return [parse_olx(o) for o in data.get("data", [])]


# --- Zrodlo: Otodom ------------------------------------------------------------
def otodom_url(cfg):
    room_map = {1: "ONE", 2: "TWO", 3: "THREE", 4: "FOUR"}
    rooms = [room_map[r] for r in cfg["rooms"] if r in room_map]
    params = {
        "viewType": "listing", "limit": 36, "by": "LATEST", "direction": "DESC",
        "priceMax": cfg["max_price"], "areaMin": cfg["min_area"],
        "roomsNumber": "[%s]" % ",".join(rooms),
    }
    if cfg.get("min_price"):
        params["priceMin"] = cfg["min_price"]
    return OTODOM_URL + "?" + urllib.parse.urlencode(params)


def parse_otodom(o):
    loc = o.get("location", {}) or {}
    rc = (loc.get("reverseGeocoding") or {}).get("locations", []) or []
    names = {l.get("locationLevel"): l.get("name") for l in rc}
    addr = loc.get("address", {}) or {}
    street = (addr.get("street") or {}).get("name") if addr.get("street") else None
    price = (o.get("totalPrice") or {}).get("value")     # cena najmu
    rent = (o.get("rentPrice") or {}).get("value")       # czynsz administracyjny
    area = o.get("areaInSquareMeters")
    rooms = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5}.get(o.get("roomsNumber"))
    floor = {"GROUND": "parter", "FIRST": "1", "SECOND": "2", "THIRD": "3", "FOURTH": "4",
             "FIFTH": "5", "SIXTH": "6", "SEVENTH": "7", "EIGHTH": "8", "NINTH": "9",
             "TENTH": "10", "ABOVE_TENTH": "10+", "CELLAR": "suterena",
             "GARRET": "poddasze"}.get(o.get("floorNumber"))
    slug = o.get("slug")
    imgs = o.get("images") or []
    photo = imgs[0].get("medium") or imgs[0].get("large") if imgs else None
    return {
        "id": "oto:" + str(o.get("id")),
        "source": "Otodom",
        "title": (o.get("title") or "").strip(),
        "url": ("https://www.otodom.pl/pl/oferta/" + slug) if slug else None,
        "desc": o.get("shortDescription") or o.get("title") or "",
        "price": price,
        "rent": rent,
        "total": (price + (rent or 0)) if price is not None else None,
        "area": area,
        "rooms": rooms,
        "floor": floor,
        "furniture": None,
        "district": names.get("district"),
        "osiedle": names.get("residential") or names.get("subdistrict"),
        "street": street,
        "lat": None, "lon": None, "detailed": False,
        "estate": o.get("estate"),          # FLAT = mieszkanie; ROOM = pokoj
        "photo": photo,
        "created": iso(o.get("dateCreated")),
        # dateCreatedFirst = pierwsza publikacja; dateCreated skacze przy wznowieniu
        "created_first": iso(o.get("dateCreatedFirst") or o.get("dateCreated")),
        "refreshed": iso(o.get("pushedUpAt") or o.get("dateCreated")),
    }


def fetch_otodom(cfg):
    html = http_get(otodom_url(cfg))
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return []
    items = json.loads(m.group(1))["props"]["pageProps"]["data"]["searchAds"].get("items", [])
    # 'items' potrafi zawierac tez rekordy promowane/deweloperskie - bierzemy te z id i cena.
    return [parse_otodom(o) for o in items if o.get("id") and o.get("estate") == "FLAT"]


# --- Filtry --------------------------------------------------------------------
def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) *
         math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


def geo_match(offer, cfg):
    dist_norm = strip_pl(offer.get("district"))
    if dist_norm in [strip_pl(d) for d in cfg.get("district_blocklist", [])]:
        return False
    hay = strip_pl(" ".join(filter(None, [offer.get("title"), offer.get("osiedle"),
                                          offer.get("street")])))
    if any(strip_pl(kw) in hay for kw in cfg["title_keywords"]):
        return True
    if offer.get("detailed") and offer.get("lat") and offer.get("lon"):
        d = haversine_km(cfg["center_lat"], cfg["center_lon"], offer["lat"], offer["lon"])
        offer["_dist_km"] = round(d, 2)
        return d <= cfg["radius_km"]
    return dist_norm in [strip_pl(d) for d in cfg["district_allowlist"]]


# "pokoj"/"pokoik" w liczbie pojedynczej (NIE lapie "2 pokoje", "3-pokojowe", "pokoi").
_ROOM_WORD_RE = re.compile(r"(?<![a-z0-9])(pokoj|pokoju|pokojek|pokoik|pokoiczek)(?![a-z0-9])")
# Liczebnik tuz przed slowem "pokoj" -> to metraz mieszkania, nie oferta pokoju.
_ROOM_COUNT_RE = re.compile(r"(\d|jeden|jedno|dwa|trzy|cztery|piec)\s*[-]?\s*$")
# Tytul zaczynajacy sie od "Pokoje ..." = oferta pokoi; mieszkanie ma z przodu
# liczbe ("2 pokoje...") albo slowo "mieszkanie".
_ROOM_TITLE_START_RE = re.compile(
    r"^(pokoj|pokoje|pokoju|pokojek|pokoi|pokoik|pokoiki)(?![a-z0-9])")


def room_reason(offer, cfg):
    """Zwraca powod, dla ktorego oferta wyglada na wynajem POKOJU (albo None)."""
    if not cfg.get("whole_flat_only", True):
        return None
    # Otodom ma osobny typ ogloszenia dla pokoi - cokolwiek innego niz mieszkanie odpada.
    if offer.get("estate") and offer["estate"] != "FLAT":
        return "typ ogloszenia: %s" % offer["estate"]

    title = strip_pl(offer.get("title")).strip(" \t-*!.,:;\"'|/()[]")
    hay = title + " \n " + strip_pl(offer.get("desc"))
    for kw in cfg.get("room_keywords", []):
        k = strip_pl(kw)
        if k and k in hay:
            return kw
    for kw in cfg.get("room_title_keywords", []):
        k = strip_pl(kw)
        if k and k in title:
            return kw + " (tytul)"
    if _ROOM_TITLE_START_RE.match(title):
        return "tytul zaczyna sie od 'pokoj...'"
    # Samo "pokoj" w tytule, bez liczebnika przed nim: "Pokoj 18 m2 Olbin".
    for m in _ROOM_WORD_RE.finditer(title):
        if not _ROOM_COUNT_RE.search(title[:m.start()][-12:]):
            return "'%s' w tytule" % m.group(0)
    return None


def shower_status(offer, cfg):
    desc = strip_pl(offer.get("desc", ""))
    if any(strip_pl(k) in desc for k in cfg["shower_keywords"]):
        return "shower"
    if any(strip_pl(k) in desc for k in cfg["bath_keywords"]):
        return "bath"
    return "unknown"


def matches(offer, cfg):
    # Oferta "odswiezona"/wznowiona: nowa na liscie, ale opublikowana dawno temu.
    max_age = cfg.get("max_offer_age_hours") or 0
    if max_age:
        age = age_hours(offer)
        if age is not None and age > float(max_age):
            return False
    # Wynajem pokoju / stancja / wspollokator - chcemy tylko cale mieszkania.
    if room_reason(offer, cfg):
        return False
    if offer["area"] is None or offer["area"] < cfg["min_area"]:
        return False
    if offer["rooms"] not in cfg["rooms"]:
        return False
    if offer["price"] is None or offer["price"] > cfg["max_price"]:
        return False
    if cfg.get("min_price") and offer["price"] < cfg["min_price"]:
        return False
    if cfg.get("max_total") and offer["total"] and offer["total"] > cfg["max_total"]:
        return False
    if not geo_match(offer, cfg):
        return False
    mode = cfg.get("shower_filter", "off")
    if mode != "off":
        st = shower_status(offer, cfg)
        if mode == "required" and st != "shower":
            return False
        if mode == "exclude_bath_only" and st == "bath":
            return False
    return True


# --- Discord -------------------------------------------------------------------
def zl(n):
    return "?" if n is None else "{:,.0f} zl".format(n).replace(",", " ")


def to_embed(offer, cfg):
    lines = []
    price_line = "**{}**".format(zl(offer["price"]))
    if offer["rent"]:
        price_line += "  + czynsz {}  =  **{} / mies.**".format(zl(offer["rent"]), zl(offer["total"]))
    lines.append(price_line)

    facts = []
    if offer["area"]:
        facts.append("{:g} m2".format(offer["area"]))
    if offer["rooms"]:
        facts.append("{} pok.".format(offer["rooms"]))
    if offer["floor"]:
        facts.append("pietro {}".format(offer["floor"]))
    if offer["furniture"]:
        facts.append("umeblowane: {}".format(offer["furniture"]))
    if facts:
        lines.append(" • ".join(facts))

    st = shower_status(offer, cfg)
    lines.append({"shower": "\U0001f6bf Prysznic: tak (wg opisu)",
                  "bath": "\U0001f6c1 Uwaga: w opisie tylko wanna",
                  "unknown": "❔ Lazienka nie opisana - dopytaj o prysznic"}[st])

    loc = " / ".join(filter(None, [offer.get("district"), offer.get("osiedle"),
                                   offer.get("street")])) or "Wroclaw"
    if offer.get("_dist_km") is not None:
        loc += "  (~{} km od centrum)".format(offer["_dist_km"])
    lines.append("\U0001f4cd " + loc)

    embed = {
        "title": (offer["title"] or "Ogloszenie")[:250],
        "url": offer["url"],
        "description": "\n".join(lines),
        "color": 0x2ecc71 if offer["source"] == "OLX" else 0x9b59b6,
        "footer": {"text": offer["source"]},
    }
    if offer.get("created"):
        embed["timestamp"] = offer["created"]
    if offer.get("photo"):
        embed["image"] = {"url": offer["photo"]}
    return embed


def post_to_discord(webhook, embeds, username, content=None, allowed_mentions=None):
    payload = {"username": username, "embeds": embeds}
    if content:
        payload["content"] = content
    # Domyslnie nie pinguj nikogo; ping tylko gdy jawnie podamy allowed_mentions.
    payload["allowed_mentions"] = allowed_mentions or {"parse": []}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(webhook, data=data, headers={
        "Content-Type": "application/json", "User-Agent": USER_AGENT})
    for _ in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                r.read()
            return True
        except urllib.error.HTTPError as e:
            if e.code == 429:
                try:
                    retry = json.loads(e.read().decode("utf-8")).get("retry_after", 2)
                except Exception:
                    retry = 2
                time.sleep(float(retry) + 0.5)
                continue
            print("Discord HTTP %s: %s" % (e.code, e.reason))
            return False
        except Exception as e:
            print("Blad wysylki na Discord: %s" % e)
            time.sleep(2)
    return False


# --- Petla ---------------------------------------------------------------------
def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def collect_offers(cfg):
    offers = []
    if cfg["sources"].get("olx"):
        try:
            offers += fetch_olx(cfg)
        except Exception as e:
            print("[%s] Blad OLX: %s" % (now(), e))
    if cfg["sources"].get("otodom"):
        try:
            offers += fetch_otodom(cfg)
        except Exception as e:
            print("[%s] Blad Otodom: %s" % (now(), e))
    # Dedup w obrebie jednego sprawdzenia: po id (Otodom zwraca te sama oferte 2x:
    # promowana + zwykla) oraz po tresci/zdjeciu (ta sama oferta na OLX i Otodom).
    uniq, keys = [], set()
    for o in offers:
        ok = offer_keys(o, cfg)
        if any(k in keys for k in ok):
            continue
        keys.update(ok)
        uniq.append(o)
    return uniq


def run_once(cfg, seen, first_run):
    offers = collect_offers(cfg)
    if not offers:
        print("[%s] Brak danych z zrodel (chwilowy problem sieci?)." % now())
        return
    name = cfg["bot_name"]
    # Najpierw sprawdzamy, potem od razu oznaczamy - dzieki temu duble w jednej
    # paczce (i oferta wystawiona ponownie) odpadaja przed wyslaniem.
    fresh, skipped_rooms = [], 0
    for o in offers:
        was_seen = is_seen(o, seen, cfg)
        mark_seen(o, seen, cfg)
        if was_seen:
            continue
        why_room = room_reason(o, cfg)
        if why_room:
            skipped_rooms += 1
            print("   - pomijam (pokoj, nie cale mieszkanie: %s) %s" % (why_room, o["title"][:55]))
            continue
        if matches(o, cfg):
            fresh.append(o)

    if first_run:
        matching = [o for o in offers if matches(o, cfg)]
        limit = int(cfg.get("first_run_posts", 0) or 0)
        post_to_discord(cfg["discord_webhook"], [{
            "title": "✅ Bot uruchomiony",
            "description": ("Monitoruje OLX + Otodom (Wroclaw).\n"
                            "Kryteria: cale mieszkania (bez pokoi), {}+ m2, {} pok., najem {}, z czynszem do {}, wybrane dzielnice (bez Psiego Pola).\n"
                            "Aktualnie pasujacych: **{}**. Bede wysylac tylko *nowe* (bez odswiezonych i powtorek).".format(
                                cfg["min_area"], "/".join(map(str, cfg["rooms"])),
                                ("od %s do %s" % (zl(cfg["min_price"]), zl(cfg["max_price"])))
                                if cfg.get("min_price") else ("do " + zl(cfg["max_price"])),
                                zl(cfg["max_total"]), len(matching))),
            "color": 0x3498db}], username=name)
        # limit = 0 -> nie wysylamy nic, tylko zapamietujemy stan (bez powtorek
        # po utracie cache seen.json w chmurze).
        newest = sorted(matching, key=lambda x: x.get("created") or "")[-limit:] if limit else []
        for o in newest:
            post_to_discord(cfg["discord_webhook"], [to_embed(o, cfg)], username=name)
            time.sleep(1)
        save_seen(seen, cfg)
        print("[%s] Pierwszy start: pobrano %d, pasujacych %d (wyslano %d)."
              % (now(), len(offers), len(matching), limit))
        return

    if not fresh:
        print("[%s] Brak nowych pasujacych ogloszen (sprawdzono %d, pokoi odrzucono %d)."
              % (now(), len(offers), skipped_rooms))
        if cfg.get("notify_when_empty"):
            post_to_discord(cfg["discord_webhook"], [], username=name,
                            content=cfg.get("not_found_text", "not found"))
        save_seen(seen, cfg)
        return

    fresh.sort(key=lambda o: o.get("created") or "")
    fresh = fresh[-cfg["max_posts_per_run"]:]
    print("[%s] Nowe pasujace: %d" % (now(), len(fresh)))
    # Zapis PRZED wysylka: gdy przebieg padnie/zostanie ubity w polowie paczki,
    # po restarcie nie wysle tych samych ofert jeszcze raz.
    save_seen(seen, cfg)

    # Ping roli @mieszkania jednym komunikatem, potem oferty.
    rid = str(cfg.get("role_id") or "").strip()
    if rid.isdigit():
        post_to_discord(cfg["discord_webhook"], [], username=name,
                        content="<@&%s> 🔔 %d nowych ofert!" % (rid, len(fresh)),
                        allowed_mentions={"roles": [rid]})
    for i, o in enumerate(fresh):
        if i:
            time.sleep(0.3)   # odstep tylko MIEDZY wysylkami (limit Discorda);
                              # pierwsza oferta leci natychmiast, bez czekania
        post_to_discord(cfg["discord_webhook"], [to_embed(o, cfg)], username=name)
        print("   + [%s] %s | %s | %s" % (o["source"], zl(o["price"]),
              o.get("district"), o["title"][:55]))
    save_seen(seen, cfg)


def run_test(cfg):
    """Test na zadanie (TEST_PING=1): odpytuje zrodla, przepuszcza oferty przez
    filtry i wysyla raport na Discorda. NIE dotyka seen.json, wiec nie "zjada"
    zadnej oferty - normalny przebieg wysle ja pozniej jak zwykle."""
    name = cfg["bot_name"]
    print("[%s] TEST: odpytuje zrodla..." % now())
    offers = collect_offers(cfg)
    per_src = {}
    for o in offers:
        per_src[o["source"]] = per_src.get(o["source"], 0) + 1
    rooms = [o for o in offers if room_reason(o, cfg)]
    matching = [o for o in offers if matches(o, cfg)]

    src_txt = ", ".join("%s: %d" % (k, v) for k, v in sorted(per_src.items())) or "brak danych"
    lines = [
        "Pobrane oferty - %s" % src_txt,
        "Odrzucone jako pokoje/stancje: **%d**" % len(rooms),
        "Pasujace do kryteriow: **%d**" % len(matching),
        "",
        "Kryteria: cale mieszkania, {}+ m2, {} pok., najem {}-{}, z czynszem do {}, do {} h od publikacji.".format(
            cfg["min_area"], "/".join(map(str, cfg["rooms"])),
            zl(cfg.get("min_price")), zl(cfg["max_price"]), zl(cfg["max_total"]),
            cfg.get("max_offer_age_hours") or "bez limitu"),
    ]
    if not offers:
        lines.append("\n⚠️ Zadne zrodlo nie odpowiedzialo - sprawdz log przebiegu.")

    ok = post_to_discord(cfg["discord_webhook"], [{
        "title": "🧪 Test polaczenia - bot dziala",
        "description": "\n".join(lines),
        "color": 0xf1c40f}], username=name)

    # Przykladowa oferta - pokazuje, ze formatowanie i zdjecia tez dzialaja.
    if ok and matching:
        newest = sorted(matching, key=lambda x: x.get("created") or "")[-1]
        post_to_discord(cfg["discord_webhook"], [to_embed(newest, cfg)], username=name,
                        content="Przyklad najnowszej pasujacej oferty (test, nie jest to nowe ogloszenie):")

    print("[%s] TEST: pobrano %d (%s), pokoi %d, pasujacych %d, wysylka na Discord: %s"
          % (now(), len(offers), src_txt, len(rooms), len(matching), "OK" if ok else "BLAD"))
    if not ok:
        sys.exit(1)


def main():
    cfg = load_config()
    if os.environ.get("TEST_PING") == "1":
        run_test(cfg)
        return
    seen = load_seen()
    first_run = len(seen) == 0
    print("Bot '%s' wystartowal. Sprawdzanie co %d s. Ctrl+C aby zatrzymac."
          % (cfg["bot_name"], cfg["poll_interval_seconds"]))
    run_once(cfg, seen, first_run)
    if cfg["run_once"]:
        return
    # W chmurze (GitHub Actions) job ma limit czasu - konczymy sami, zeby zdazyc
    # zapisac cache seen.json; kolejne uruchomienie startuje z crona.
    max_minutes = to_number(os.environ.get("MAX_MINUTES"))
    deadline = (time.time() + max_minutes * 60) if max_minutes else None
    while True:
        try:
            if deadline and time.time() + cfg["poll_interval_seconds"] > deadline:
                print("[%s] Limit czasu uruchomienia - koniec." % now())
                break
            time.sleep(cfg["poll_interval_seconds"])
            run_once(cfg, seen, first_run=False)
        except KeyboardInterrupt:
            print("\nZatrzymano.")
            break
        except Exception as e:
            print("[%s] Blad w petli: %s" % (now(), e))
            time.sleep(30)


if __name__ == "__main__":
    main()
