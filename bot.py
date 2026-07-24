#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bot Discord: monitoruje nowe ogloszenia wynajmu mieszkan we Wroclawiu
(OLX + Otodom) i wysyla je na kanal Discord przez webhook.

Kryteria (domyslne, do zmiany w config.json):
  - dzielnice: Srodmiescie, Stare Miasto, Olbin, Plac Grunwaldzki, Nadodrze + okolice PWR
  - BEZ Psiego Pola
  - min. 40 m2, 2 lub 3 pokoje
  - najem do 3000 zl, z czynszem do 4000 zl
  - odrzuca oferty wspominajace tylko wanne (bez info o lazience -> przepuszcza)

Zero zaleznosci - dziala na czystym Pythonie (>=3.8). Wystarczy wkleic webhook do config.json.
"""

import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from datetime import datetime

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
    "bot_name": "ukrainiec jebany",
    "sources": {"olx": True, "otodom": True},

    "min_area": 40,               # minimalny metraz [m2]
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

    # Ping roli, gdy znajdzie nowe oferty (ID roli @mieszkania; puste = bez pingu).
    "role_id": "",
    # Gdy w danym sprawdzeniu nie ma nowych ofert - domyslnie NIC nie pisz (cisza).
    "notify_when_empty": False,
    "not_found_text": "not found",

    "poll_interval_seconds": 900,  # co ile sekund sprawdzac (900 = 15 min)
    "first_run_posts": 5,          # ile aktualnych ofert wyslac przy pierwszym starcie
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


def load_seen():
    if os.path.exists(SEEN_PATH):
        try:
            with open(SEEN_PATH, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_seen(seen):
    with open(SEEN_PATH, "w", encoding="utf-8") as f:
        json.dump(list(seen)[-8000:], f)


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


# --- Zrodlo: OLX ---------------------------------------------------------------
def olx_url(cfg, limit=50):
    params = {
        "offset": 0, "limit": limit,
        "category_id": OLX_CATEGORY_ID, "city_id": OLX_CITY_ID, "region_id": OLX_REGION_ID,
        "filter_float_price:to": cfg["max_price"],
        "filter_float_m:from": cfg["min_area"],
        "sort_by": "created_at:desc",
    }
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
        "photo": photo,
        "created": iso(o.get("dateCreated")),
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


def shower_status(offer, cfg):
    desc = strip_pl(offer.get("desc", ""))
    if any(strip_pl(k) in desc for k in cfg["shower_keywords"]):
        return "shower"
    if any(strip_pl(k) in desc for k in cfg["bath_keywords"]):
        return "bath"
    return "unknown"


def matches(offer, cfg):
    if offer["area"] is None or offer["area"] < cfg["min_area"]:
        return False
    if offer["rooms"] not in cfg["rooms"]:
        return False
    if offer["price"] is None or offer["price"] > cfg["max_price"]:
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
    # Dedup po id (Otodom potrafi zwrocic te sama oferte 2x: promowana + zwykla).
    uniq, ids = [], set()
    for o in offers:
        if o["id"] in ids:
            continue
        ids.add(o["id"])
        uniq.append(o)
    return uniq


def run_once(cfg, seen, first_run):
    offers = collect_offers(cfg)
    if not offers:
        print("[%s] Brak danych z zrodel (chwilowy problem sieci?)." % now())
        return
    name = cfg["bot_name"]
    fresh = [o for o in offers if o["id"] not in seen and matches(o, cfg)]
    for o in offers:
        seen.add(o["id"])

    if first_run:
        matching = [o for o in offers if matches(o, cfg)]
        post_to_discord(cfg["discord_webhook"], [{
            "title": "✅ Bot uruchomiony",
            "description": ("Monitoruje OLX + Otodom (Wroclaw).\n"
                            "Kryteria: {}+ m2, {} pok., najem do {}, z czynszem do {}, wybrane dzielnice (bez Psiego Pola).\n"
                            "Aktualnie pasujacych: **{}**. Bede wysylac tylko *nowe*.".format(
                                cfg["min_area"], "/".join(map(str, cfg["rooms"])),
                                zl(cfg["max_price"]), zl(cfg["max_total"]), len(matching))),
            "color": 0x3498db}], username=name)
        for o in sorted(matching, key=lambda x: x.get("created") or "")[-cfg.get("first_run_posts", 5):]:
            post_to_discord(cfg["discord_webhook"], [to_embed(o, cfg)], username=name)
            time.sleep(1)
        save_seen(seen)
        print("[%s] Pierwszy start: pobrano %d, pasujacych %d." % (now(), len(offers), len(matching)))
        return

    if not fresh:
        print("[%s] Brak nowych pasujacych ogloszen (sprawdzono %d)." % (now(), len(offers)))
        if cfg.get("notify_when_empty"):
            post_to_discord(cfg["discord_webhook"], [], username=name,
                            content=cfg.get("not_found_text", "not found"))
        save_seen(seen)
        return

    fresh.sort(key=lambda o: o.get("created") or "")
    fresh = fresh[-cfg["max_posts_per_run"]:]
    print("[%s] Nowe pasujace: %d" % (now(), len(fresh)))

    # Ping roli @mieszkania jednym komunikatem, potem oferty.
    rid = str(cfg.get("role_id") or "").strip()
    if rid.isdigit():
        post_to_discord(cfg["discord_webhook"], [], username=name,
                        content="<@&%s> 🔔 %d nowych ofert!" % (rid, len(fresh)),
                        allowed_mentions={"roles": [rid]})
    for o in fresh:
        post_to_discord(cfg["discord_webhook"], [to_embed(o, cfg)], username=name)
        print("   + [%s] %s | %s | %s" % (o["source"], zl(o["price"]),
              o.get("district"), o["title"][:55]))
        time.sleep(1)
    save_seen(seen)


def main():
    cfg = load_config()
    seen = load_seen()
    first_run = len(seen) == 0
    print("Bot '%s' wystartowal. Sprawdzanie co %d s. Ctrl+C aby zatrzymac."
          % (cfg["bot_name"], cfg["poll_interval_seconds"]))
    run_once(cfg, seen, first_run)
    if cfg["run_once"]:
        return
    while True:
        try:
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
