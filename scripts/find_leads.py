#!/usr/bin/env python3
"""Шукає стоматології без сайту, пише leads.csv (name;phone;address;city;dental;slug)
і генерує для кожної клініки окрему демо-сторінку sites/<slug>.html з шаблону sites/dental-demo.html.
Змінні оточення: CITY (через кому; порожньо = cities.txt), DISTRICTS, SOURCE (osm|google), MAX_REVIEWS, RESET."""
import html, json, os, re, socket, sys, time, urllib.request, urllib.error

# GitHub-раннери часто не мають IPv6: використовуємо лише IPv4
_gai = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [x for x in _gai(*a, **k) if x[0] == socket.AF_INET] or _gai(*a, **k)

CITIES = [c.strip() for c in os.environ.get("CITY", "").split(",") if c.strip()]
if not CITIES and os.path.exists("cities.txt"):
    CITIES = [l.strip() for l in open("cities.txt", encoding="utf-8") if l.strip() and not l.startswith("#")]
DISTRICTS = [d.strip() for d in os.environ.get("DISTRICTS", "").split(",") if d.strip()]
SOURCE = (os.environ.get("SOURCE") or "osm").strip().lower()
MAX_REVIEWS = int(os.environ.get("MAX_REVIEWS") or 100)
RESET = os.environ.get("RESET", "false").lower() == "true"
OUT, TEMPLATE, SITES = os.environ.get("OUT", "leads.csv"), "sites/dental-demo.html", "sites"
KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")
SOCIAL = re.compile(r"(facebook|instagram|t\.me|linktr\.ee|taplink|olx|google\.com/maps|goo\.gl)", re.I)
TR = dict(zip("абвгґдеєжзиіїйклмнопрстуфхцчшщьюя", ["a","b","v","h","g","d","e","ie","zh","z","y","i","i","i","k","l","m","n","o","p","r","s","t","u","f","kh","ts","ch","sh","shch","","iu","ia"]))

def digits(p): return re.sub(r"\D", "", p or "")
def norm_phone(p):
    d = digits(p)
    if len(d) == 10 and d.startswith("0"): d = "38" + d
    if len(d) == 9: d = "380" + d
    return d if len(d) == 12 and d.startswith("380") else ""
def clean(t): return re.sub(r"[;\r\n]+", " ", (t or "")).strip()
def has_real_site(u): return bool(u) and not SOCIAL.search(u)
def slugify(city, name, ph):
    base = "".join(TR.get(ch, ch) for ch in (city + " " + name).lower())
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")[:40].strip("-")
    return "%s-%s" % (base or "clinic", ph[-4:])

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
    if pl.get("businessStatus", "OPERATIONAL") != "OPERATIONAL" or has_real_site(pl.get("websiteUri", "")): return None
    if pl.get("userRatingCount", 0) > MAX_REVIEWS: return None
    ph = norm_phone(pl.get("internationalPhoneNumber") or pl.get("nationalPhoneNumber"))
    if not ph: return None
    return clean(pl.get("displayName", {}).get("text")), ph, clean(pl.get("formattedAddress"))

def google(city):
    if not KEY: sys.exit("Немає GOOGLE_MAPS_API_KEY. Виберіть source=osm.")
    mask = ("places.displayName,places.formattedAddress,places.nationalPhoneNumber,places.internationalPhoneNumber,"
            "places.websiteUri,places.userRatingCount,places.businessStatus,nextPageToken")
    hdr = {"Content-Type": "application/json", "X-Goog-Api-Key": KEY, "X-Goog-FieldMask": mask}
    found = []
    for q in ["стоматологія, %s" % city] + ["стоматологія, %s, %s" % (d, city) for d in DISTRICTS]:
        token = None
        for _ in range(3):
            body = {"textQuery": q, "languageCode": "uk", "regionCode": "UA", "pageSize": 20}
            if token: body["pageToken"] = token
            try: res = post("https://places.googleapis.com/v1/places:searchText", json.dumps(body).encode(), hdr)
            except RuntimeError as e: sys.exit("Google API: %s" % e)
            found += [r for r in map(parse_google, res.get("places", [])) if r]
            token = res.get("nextPageToken")
            if not token: break
            time.sleep(1.5)
    return found

MIRRORS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
           "https://overpass.openstreetmap.fr/api/interpreter", "https://maps.mail.ru/osm/tools/overpass/api/interpreter"]

def osm(city):
    q = ('[out:json][timeout:90];area["name"="%s"]["boundary"="administrative"]->.a;('
         'nwr["amenity"="dentist"](area.a);nwr["healthcare"="dentist"](area.a);nwr["healthcare:speciality"="dentistry"](area.a);'
         'nwr["amenity"~"clinic|doctors"]["name"~"стомат|dent|зуб",i](area.a););out center tags;') % city.replace('"', "")
    res = None
    for url in MIRRORS:
        for _ in range(2):
            try:
                res = post(url, ("data=" + urllib.request.quote(q)).encode(), {"Content-Type": "application/x-www-form-urlencoded"}, 120); break
            except RuntimeError as e:
                print("OSM %s: %s" % (url.split("/")[2], e)); time.sleep(3)
        if res: break
    if not res:
        print("Не вдалося підключитися до OSM для міста %s" % city); return []
    out = []
    for el in res.get("elements", []):
        t = el.get("tags", {})
        if has_real_site(t.get("website") or t.get("contact:website") or t.get("url")): continue
        ph = norm_phone((t.get("phone") or t.get("contact:phone") or t.get("contact:mobile") or "").split(";")[0])
        if not ph: continue
        addr = ", ".join(x for x in [t.get("addr:street", ""), t.get("addr:housenumber", "")] if x)
        out.append((clean(t.get("name") or "Стоматологія"), ph, clean(addr)))
    print("%s: у OSM знайдено %d елементів, підійшло %d" % (city, len(res.get("elements", [])), len(out)))
    return out

def make_site(tpl, name, city, ph, addr, slug):
    tel = "+" + ph
    conf = 'var C={name:%s,city:%s,tel:%s,addr:%s,hours:"Пн–Пт 9:00–20:00"};' % tuple(json.dumps(x, ensure_ascii=False).replace("</", "<\\/") for x in (name, city, tel, addr or city))
    page, n = re.subn(r'var C=\{name:"[^"]*",city:"[^"]*",tel:"[^"]*",addr:"[^"]*",hours:"[^"]*"\};', lambda m: conf, tpl, count=1)
    if not n: return False
    e = html.escape
    title = "%s — запис онлайн" % name
    og = ('<title>%s</title>\n<meta property="og:title" content="%s">\n<meta property="og:description" content="Запис на прийом онлайн: лікування, гігієна, імпланти, елайнери. %s">\n<meta property="og:type" content="website">'
          % (e(title), e(title), e(city)))
    page = page.replace("<title>Стоматологія</title>", og, 1)
    os.makedirs(SITES, exist_ok=True)
    open(os.path.join(SITES, slug + ".html"), "w", encoding="utf-8").write(page)
    return True

def main():
    if not CITIES: sys.exit("Не вказано місто (CITY) і немає cities.txt")
    existing, rows = set(), []
    if os.path.exists(OUT) and not RESET:
        for line in open(OUT, encoding="utf-8").read().splitlines():
            p = line.split(";")
            if len(p) >= 2 and norm_phone(p[1]): existing.add(norm_phone(p[1])[-9:]); rows.append(line)
    tpl = open(TEMPLATE, encoding="utf-8").read() if os.path.exists(TEMPLATE) else None
    if tpl is None: print("УВАГА: немає %s, окремі сторінки не створюються" % TEMPLATE)
    new = pages = 0
    for i, city in enumerate(CITIES):
        if i: time.sleep(10)
        for name, ph, addr in (google(city) if SOURCE == "google" else osm(city)):
            if ph[-9:] in existing: continue
            existing.add(ph[-9:]); slug = slugify(city, name, ph)
            ok = tpl is not None and make_site(tpl, name, city, ph, addr, slug)
            pages += bool(ok)
            rows.append(";".join([name, ph, addr, city, "dental", slug if ok else ""])); new += 1
    open(OUT, "w", encoding="utf-8", newline="\n").write("\n".join(rows) + ("\n" if rows else ""))
    msg = "Нових клінік: %d, створено сторінок: %d. Усього в leads.csv: %d." % (new, pages, len(rows))
    print(msg)
    if os.environ.get("GITHUB_STEP_SUMMARY"): open(os.environ["GITHUB_STEP_SUMMARY"], "a").write(msg + "\n")

if __name__ == "__main__": main()
