#!/usr/bin/env python3
"""Збирає стоматології для холодного outreach і пише leads.csv:
name;phone;address;city;dental;slug;social;email;site;audit
Кого беремо: (1) без сайту, (2) зі слабким сайтом (перевіряємо: адаптація під телефон, https, онлайн-запис).
Контакт: телефон АБО соцмережа АБО email. Для кожної клініки створюється sites/<slug>.html з шаблону.
Змінні: CITY (через кому), BATCH (скільки міст з cities.txt за запуск, 0 = усі), SOURCE, DISTRICTS, MAX_REVIEWS, MAX_AUDIT, RESET."""
import datetime, html, json, os, re, socket, sys, time, urllib.request, urllib.error, zlib

_gai = socket.getaddrinfo   # лише IPv4: на GitHub-раннерах IPv6 недоступний
socket.getaddrinfo = lambda *a, **k: [x for x in _gai(*a, **k) if x[0] == socket.AF_INET] or _gai(*a, **k)

ENV = os.environ.get
CITIES = [c.strip() for c in (ENV("CITY") or "").split(",") if c.strip()]
BATCH = int(ENV("BATCH") or 0)
DISTRICTS = [d.strip() for d in (ENV("DISTRICTS") or "").split(",") if d.strip()]
SOURCE = (ENV("SOURCE") or "osm").strip().lower()
MAX_REVIEWS = int(ENV("MAX_REVIEWS") or 100)
MAX_AUDIT = int(ENV("MAX_AUDIT") or 150)
RESET = (ENV("RESET") or "false").lower() == "true"
OUT, TEMPLATE, SITES = ENV("OUT", "leads.csv"), "sites/dental-demo.html", "sites"
KEY = ENV("GOOGLE_MAPS_API_KEY", "")
SOCIAL = re.compile(r"(facebook|instagram|t\.me|linktr\.ee|taplink|olx|google\.com/maps|goo\.gl|youtube|tiktok)", re.I)
TR = dict(zip("абвгґдеєжзиіїйклмнопрстуфхцчшщьюя", ["a","b","v","h","g","d","e","ie","zh","z","y","i","i","i","k","l","m","n","o","p","r","s","t","u","f","kh","ts","ch","sh","shch","","iu","ia"]))
BOOK = re.compile(r"онлайн[\s-]*запис|записатись|записатися|записаться|запис на прийом|запис до лікаря|book|appointment|calendly", re.I)

def digits(p): return re.sub(r"\D", "", p or "")
def norm_phone(p):
    d = digits(p)
    if len(d) == 10 and d.startswith("0"): d = "38" + d
    if len(d) == 9: d = "380" + d
    return d if len(d) == 12 and d.startswith("380") else ""
def clean(t): return re.sub(r"[;\r\n]+", " ", (t or "")).strip()
def has_real_site(u): return bool(u) and not SOCIAL.search(u)
def first(*vals):
    for v in vals:
        if v and v.strip(): return v.split(";")[0].strip()
    return ""
def social_url(t):
    v = first(t.get("contact:instagram"), t.get("instagram"), t.get("contact:facebook"), t.get("facebook"), t.get("contact:telegram"))
    if not v: return ""
    if v.startswith("http"): return clean(v)
    h = v.lstrip("@").strip("/")
    kind = "instagram.com" if (t.get("contact:instagram") or t.get("instagram")) else ("t.me" if t.get("contact:telegram") else "facebook.com")
    return clean("https://%s/%s" % (kind, h))
def slugify(city, name, ph, extra=""):
    base = "".join(TR.get(ch, ch) for ch in (city + " " + name).lower())
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")[:40].strip("-")
    tail = ph[-4:] if ph else "%04x" % (zlib.crc32((name + city + extra).encode("utf-8")) & 0xffff)
    return "%s-%s" % (base or "clinic", tail)

def post(url, data, headers, timeout=60):
    headers = dict(headers); headers.setdefault("User-Agent", "sto-outreach-leadfinder/2.0 (github actions)")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError("HTTP %s: %s" % (e.code, e.read().decode("utf-8", "ignore")[:300]))
    except Exception as e:
        raise RuntimeError(str(e))

def fetch_site(url):
    """Повертає (кінцева_адреса, html) або кидає RuntimeError('open'/'blocked')."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; sto-outreach-audit/2.0)"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.geturl(), r.read(400000).decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        raise RuntimeError("blocked" if e.code in (401, 403, 406, 429) else "open")
    except Exception:
        raise RuntimeError("open")

def audit(url):
    """None = не вдалося оцінити; [] = сайт нормальний; список = що не так."""
    if not url.startswith("http"): url = "http://" + url
    try: final, page = fetch_site(url)
    except RuntimeError as e:
        return None if str(e) == "blocked" else ["сайт не відкривається"]
    low, issues = page.lower(), []
    if not re.search(r"""name=["']viewport["']""", low): issues.append("не адаптований під телефон")
    if final.startswith("http://"): issues.append("без захищеного з’єднання (https)")
    if not BOOK.search(low): issues.append("немає онлайн-запису")
    return issues

def google(city):
    if not KEY: sys.exit("Немає GOOGLE_MAPS_API_KEY. Виберіть source=osm.")
    mask = ("places.displayName,places.formattedAddress,places.nationalPhoneNumber,places.internationalPhoneNumber,"
            "places.websiteUri,places.userRatingCount,places.businessStatus,nextPageToken")
    hdr = {"Content-Type": "application/json", "X-Goog-Api-Key": KEY, "X-Goog-FieldMask": mask}
    out = []
    for q in ["стоматологія, %s" % city] + ["стоматологія, %s, %s" % (d, city) for d in DISTRICTS]:
        token = None
        for _ in range(3):
            body = {"textQuery": q, "languageCode": "uk", "regionCode": "UA", "pageSize": 20}
            if token: body["pageToken"] = token
            try: res = post("https://places.googleapis.com/v1/places:searchText", json.dumps(body).encode(), hdr)
            except RuntimeError as e: sys.exit("Google API: %s" % e)
            for pl in res.get("places", []):
                if pl.get("businessStatus", "OPERATIONAL") != "OPERATIONAL" or pl.get("userRatingCount", 0) > MAX_REVIEWS: continue
                out.append({"name": clean(pl.get("displayName", {}).get("text")), "phone": norm_phone(pl.get("internationalPhoneNumber") or pl.get("nationalPhoneNumber")),
                            "addr": clean(pl.get("formattedAddress")), "site": pl.get("websiteUri", ""), "social": "", "email": ""})
            token = res.get("nextPageToken")
            if not token: break
            time.sleep(1.5)
    return out

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
        addr = ", ".join(x for x in [t.get("addr:street", ""), t.get("addr:housenumber", "")] if x)
        out.append({"name": clean(t.get("name") or "Стоматологія"), "phone": norm_phone(first(t.get("phone"), t.get("contact:phone"), t.get("contact:mobile"))),
                    "addr": clean(addr), "site": first(t.get("website"), t.get("contact:website"), t.get("url")),
                    "social": social_url(t), "email": clean(first(t.get("email"), t.get("contact:email")))})
    return out

def make_site(tpl, name, city, ph, addr, slug):
    conf = 'var C={name:%s,city:%s,tel:%s,addr:%s,hours:"Пн–Пт 9:00–20:00"};' % tuple(
        json.dumps(x, ensure_ascii=False).replace("</", "<\\/") for x in (name, city, ("+" + ph) if ph else "", addr or city))
    page, n = re.subn(r'var C=\{name:"[^"]*",city:"[^"]*",tel:"[^"]*",addr:"[^"]*",hours:"[^"]*"\};', lambda m: conf, tpl, count=1)
    if not n: return False
    e = html.escape; title = "%s — запис онлайн" % name
    og = ('<title>%s</title>\n<meta property="og:title" content="%s">\n<meta property="og:description" content="Запис на прийом онлайн: лікування, гігієна, імпланти, елайнери. %s">\n<meta property="og:type" content="website">' % (e(title), e(title), e(city)))
    page = page.replace("<title>Стоматологія</title>", og, 1)
    os.makedirs(SITES, exist_ok=True)
    open(os.path.join(SITES, slug + ".html"), "w", encoding="utf-8").write(page)
    return True

def pick_cities():
    if CITIES: return CITIES
    allc = [l.strip() for l in open("cities.txt", encoding="utf-8") if l.strip() and not l.startswith("#")] if os.path.exists("cities.txt") else []
    if BATCH and BATCH < len(allc):   # ротація: кожного дня наступна порція міст
        start = (datetime.date.today().toordinal() * BATCH) % len(allc)
        return [allc[(start + i) % len(allc)] for i in range(BATCH)]
    return allc

def row_key(p):
    ph = norm_phone(p[1]) if len(p) > 1 else ""
    return ph[-9:] if ph else "u:" + (p[5] if len(p) > 5 else p[0])

def main():
    cities = pick_cities()
    if not cities: sys.exit("Не вказано місто (CITY) і немає cities.txt")
    print("Міста в цьому запуску: %s" % ", ".join(cities))
    existing, rows = set(), []
    if os.path.exists(OUT) and not RESET:
        for line in open(OUT, encoding="utf-8").read().splitlines():
            p = line.split(";")
            if len(p) >= 2 and p[0] != "name": existing.add(row_key(p)); rows.append(line)
    tpl = open(TEMPLATE, encoding="utf-8").read() if os.path.exists(TEMPLATE) else None
    if tpl is None: print("УВАГА: немає %s, окремі сторінки не створюються" % TEMPLATE)
    new = pages = weak = nosite = skipped = audits = 0
    for i, city in enumerate(cities):
        if i: time.sleep(10)
        for c in (google(city) if SOURCE == "google" else osm(city)):
            if not (c["phone"] or c["social"] or c["email"]): skipped += 1; continue
            real = has_real_site(c["site"]); note = ""
            if real:
                if audits >= MAX_AUDIT: continue
                audits += 1; res = audit(c["site"])
                if not res: continue
                note = clean(", ".join(res)); weak += 1
            else: nosite += 1
            slug = slugify(city, c["name"], c["phone"], c["addr"])
            key = c["phone"][-9:] if c["phone"] else "u:" + slug
            if key in existing: continue
            existing.add(key)
            ok = tpl is not None and make_site(tpl, c["name"], city, c["phone"], c["addr"], slug)
            pages += bool(ok)
            rows.append(";".join(map(clean, [c["name"], c["phone"], c["addr"], city, "dental", slug if ok else "", c["social"], c["email"], c["site"] if real else "", note]))); new += 1
    open(OUT, "w", encoding="utf-8", newline="\n").write("\n".join(rows) + ("\n" if rows else ""))
    msg = "Нових: %d (без сайту: %d, зі слабким сайтом: %d), сторінок створено: %d, без жодного контакту пропущено: %d. Усього в leads.csv: %d." % (new, nosite, weak, pages, skipped, len(rows))
    print(msg)
    if ENV("GITHUB_STEP_SUMMARY"): open(ENV("GITHUB_STEP_SUMMARY"), "a").write(msg + "\n")

if __name__ == "__main__": main()
