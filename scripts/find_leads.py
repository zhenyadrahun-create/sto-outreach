#!/usr/bin/env python3
"""Збирає стоматології без сайту в leads.csv (формат CRM: name;phone;address;city;dental).
Джерела: google (Places API New, потрібен ключ) або osm (безкоштовно, без ключа, дані неповні).
Параметри беруться зі змінних оточення (їх задає workflow): CITY, DISTRICTS, SOURCE, MAX_REVIEWS, RESET."""
import csv, io, json, os, re, sys, time, urllib.request, urllib.error

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
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        sys.exit("HTTP %s: %s" % (e.code, e.read().decode("utf-8", "ignore")[:400]))

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
            res = post("https://places.googleapis.com/v1/places:searchText", json.dumps(body).encode(), hdr)
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
    res = post("https://overpass-api.de/api/interpreter", ("data=" + urllib.request.quote(q)).encode(),
               {"Content-Type": "application/x-www-form-urlencoded"}, 120)
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
