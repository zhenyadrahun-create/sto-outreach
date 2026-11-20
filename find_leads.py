#!/usr/bin/env python3
"""Збирає стоматології без сайту в leads.csv (формат CRM: name;phone;address;city;dental).
Джерела: google (Places API New, потрібен ключ) або osm (безкоштовно, без ключа, дані неповні).
Параметри беруться зі змінних оточення (їх задає workflow): CITY, DISTRICTS, SOURCE, MAX_REVIEWS, RESET."""
import json, os, re, socket, sys, time, urllib.request, urllib.error

# GitHub-раннери часто не мають IPv6: використовуємо лише IPv4 (інакше "Network is unreachable")
_gai = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [x for x in _gai(*a, **k) if x[0] == socket.AF_INET] or _gai(*a, **k)

CITY = os.environ.get("CITY", "Львів").strip()
DISTRICTS = [d.strip() for d in os.environ.get("DISTRICTS", "").split(",") if d.strip()]
SOURCE = os.environ.get("SOURCE", "google").strip().lower()
MAX_REVIEWS = int(os.environ.get("MAX_REVIEWS", "100") or 100)
RESET = os.environ.get("RESET", "false").lower() == "true"
OUT = os.environ.get("OUT", "leads.csv")
KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")
SOCIAL = re.compile(r"(facebook|instagram|t\.me|linktr\.ee|taplink|olx|google\.com/maps|goo\.gl)", re.I)

def digits(p): return re.sub(r"\D", "", p or "")

def norm_phone(p):
    d = digits(p)
    if len(d) == 10 and d.startswith("0"): d = "38" + d
    if len(d) == 9: d = "380" + d
    return d if len(d) == 12 and d.startswith("380") else ""

def clean(t): return re.sub(r"[;\r\n]+", " ", (t or "")).strip()

def has_real_site(url): return bool(url) and not SOCIAL.search(url)

def post(url, data, headers, timeout=60):
    headers = dict(headers); headers.setdefault("User-Agent", "sto-outreach-leadfinder/1.0 (github actions)")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError("HTTP %s: %s" % (e.code, e.read().decode("utf-8", "ignore")[:300]))
    except Exception as e:
        raise RuntimeError(str(e))

def parse_google(pl):
    """Повертає (name, phone, address) або None, якщо клініка не підходить."""
    if pl.get("businessStatus", "OPERATIONAL") != "OPERATIONAL": return None
    if has_real_site(pl.get("websiteUri", "")): return None
    if pl.get("userRatingCount", 0) > MAX_REVIEWS: return None
    ph = norm_phone(pl.get("internationalPhoneNumber") or pl.get("nationalPhoneNumber"))
    if not ph: return None
    return clean(pl.get("displayName", {}).get("text")), ph, clean(pl.get("formattedAddress"))

def google():
    if not KEY: sys.exit("Немає GOOGLE_MAPS_API_KEY. Додайте ключ у Settings > Secrets або виберіть source=osm.")
    mask = ("places.displayName,places.formattedAddress,places.nationalPhoneNumber,places.internationalPhoneNumber,"
            "places.websiteUri,places.userRatingCount,places.businessStatus,nextPageToken")
    hdr = {"Content-Type": "application/json", "X-Goog-Api-Key": KEY, "X-Goog-FieldMask": mask}
    queries = ["стоматологія, %s" % CITY] + ["стоматологія, %s, %s" % (d, CITY) for d in DISTRICTS]
    found, seen = [], 0
    for q in queries:
        token = None
        for _ in range(3):
            body = {"textQuery": q, "languageCode": "uk", "regionCode": "UA", "pageSize": 20}
            if token: body["pageToken"] = token
            try: res = post("https://places.googleapis.com/v1/places:searchText", json.dumps(body).encode(), hdr)
            except RuntimeError as e: sys.exit("Google API: %s" % e)
            for pl in res.get("places", []):
                seen += 1
                r = parse_google(pl)
                if r: found.append(r)
            token = res.get("nextPageToken")
            if not token: break
            time.sleep(1.5)
    print("Google: переглянуто %d, підійшло %d" % (seen, len(found)))
    return found

def osm():
    q = ('[out:json][timeout:90];area["name"="%s"]["boundary"="administrative"]->.a;'
         '(nwr["amenity"="dentist"](area.a);nwr["healthcare"="dentist"](area.a););out center tags;') % CITY.replace('"', "")
    MIRRORS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
               "https://overpass.openstreetmap.fr/api/interpreter", "https://maps.mail.ru/osm/tools/overpass/api/interpreter"]
    res = None
    for url in MIRRORS:
        for attempt in range(2):
            try:
                res = post(url, ("data=" + urllib.request.quote(q)).encode(), {"Content-Type": "application/x-www-form-urlencoded"}, 120)
                break
            except RuntimeError as e:
                print("OSM %s: %s" % (url.split("/")[2], e)); time.sleep(3)
        if res: break
    if not res: sys.exit("Не вдалося підключитися до жодного сервера OSM. Спробуйте запустити ще раз за кілька хвилин.")
    found, seen = [], 0
    for el in res.get("elements", []):
        t = el.get("tags", {}); seen += 1
        if has_real_site(t.get("website") or t.get("contact:website")): continue
        ph = norm_phone((t.get("phone") or t.get("contact:phone") or "").split(";")[0])
        if not ph: continue
        addr = ", ".join(x for x in [t.get("addr:street", ""), t.get("addr:housenumber", "")] if x)
        found.append((clean(t.get("name") or "Стоматологія"), ph, clean(addr)))
    print("OSM: переглянуто %d, підійшло %d (є телефон і немає сайту)" % (seen, len(found)))
    return found

def main():
    existing, rows = set(), []
    if os.path.exists(OUT) and not RESET:
        for line in open(OUT, encoding="utf-8").read().splitlines():
            p = line.split(";")
            if len(p) >= 2 and norm_phone(p[1]): existing.add(norm_phone(p[1])[-9:]); rows.append(line)
    new = 0
    for name, ph, addr in (google() if SOURCE == "google" else osm()):
        if ph[-9:] in existing: continue
        existing.add(ph[-9:]); rows.append(";".join([name, ph, addr, CITY, "dental"])); new += 1
    open(OUT, "w", encoding="utf-8", newline="\n").write("\n".join(rows) + ("\n" if rows else ""))
    msg = "Нових клінік: %d. Усього в leads.csv: %d." % (new, len(rows))
    print(msg)
    if os.environ.get("GITHUB_STEP_SUMMARY"): open(os.environ["GITHUB_STEP_SUMMARY"], "a").write(msg + "\n")

if __name__ == "__main__": main()
