#!/usr/bin/env python3
"""Пошук СТО (OpenStreetMap) -> сайти -> нагадування у Telegram у випадкові години. Нічого не шле клієнтам сам."""
import json, os, re, random, html, time, urllib.request, urllib.parse, urllib.error
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Kyiv")
UA = "sto-outreach/1.0 (personal lead tool)"
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter", "https://overpass.private.coffee/api/interpreter"]
MOBILE = {"039","050","063","066","067","068","073","091","092","093","094","095","096","097","098","099"}
TOKEN, CHAT = os.environ.get("BOT_TOKEN"), os.environ.get("CHAT_ID")
MARK = "/* ===== далі код чіпати не потрібно ===== */"

def jload(p, d):
    try: return json.load(open(p, encoding="utf-8"))
    except FileNotFoundError: return d
def jsave(p, o): json.dump(o, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
def http(url, data=None, timeout=90):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
    return json.load(urllib.request.urlopen(req, timeout=timeout))

def tg(method, payload):
    if not TOKEN:
        print("[DRY]", method, json.dumps(payload, ensure_ascii=False)[:600]); return
    req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/{method}",
        json.dumps(payload).encode(), {"Content-Type": "application/json"})
    try: urllib.request.urlopen(req, timeout=30).read()
    except urllib.error.HTTPError as e:
        print("TELEGRAM ERROR", e.code, e.read().decode("utf-8", "ignore")); raise

def norm(p):
    for part in re.split(r"[;,/]", p or ""):
        d = re.sub(r"\D", "", part)
        n = d if (d.startswith("380") and len(d) == 12) else ("38" + d if (d.startswith("0") and len(d) == 10) else None)
        if n and n[2:5] in MOBILE: return n
    return None

def norm_any(p):
    d = re.sub(r"\D", "", p or "")
    if d.startswith("380") and len(d) == 12: n = d
    elif d.startswith("0") and len(d) == 10: n = "38" + d
    else: return None
    return n, n[2:5] in MOBILE

def import_csv(st):
    try: lines = open("leads.csv", encoding="utf-8-sig").read().splitlines()
    except FileNotFoundError: return
    known = {l["phone"] for l in st["leads"].values()}
    for ln in lines:
        ln = ln.strip()
        if not ln or ln.startswith("#"): continue
        p = [x.strip() for x in re.split(r"\t|;", ln)]
        r_ = norm_any(p[1]) if len(p) > 1 else None
        if not r_ or r_[0] in known: continue
        known.add(r_[0])
        st["leads"]["c" + r_[0]] = {"name": p[0], "phone": r_[0], "mobile": r_[1],
            "address": p[2] if len(p) > 2 else "", "city": p[3] if len(p) > 3 else "", "status": "new"}

def gpost(key, body):
    req = urllib.request.Request("https://places.googleapis.com/v1/places:searchText", json.dumps(body).encode(),
        {"Content-Type": "application/json", "X-Goog-Api-Key": key,
         "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,places.internationalPhoneNumber,places.nationalPhoneNumber,places.websiteUri,places.userRatingCount,places.businessStatus,nextPageToken"})
    return json.load(urllib.request.urlopen(req, timeout=40))

def google_fill(cfg, st):
    key = os.environ.get("GOOGLE_API_KEY")
    if not key or not cfg.get("google_queries"): return
    need = cfg["per_day"] * 3
    known = {l["phone"] for l in st["leads"].values()}
    done = st.setdefault("g_done", []); calls = 0; maxc = cfg.get("google_max_calls_per_run", 6)
    for item in cfg["google_queries"]:
        q, city = item["q"], item.get("city", "")
        if q in done: continue
        if calls >= maxc or sum(1 for l in st["leads"].values() if l["status"] == "new") >= need: break
        token = None; tot = ph = add = 0; ok = True
        for _ in range(3):
            if calls >= maxc: break
            body = {"textQuery": q, "languageCode": "uk", "regionCode": "UA", "pageSize": 20}
            if token: body["pageToken"] = token
            try: data = gpost(key, body)
            except urllib.error.HTTPError as e:
                msg = e.read().decode("utf-8", "ignore")[:300]; print("GOOGLE ERROR", e.code, msg)
                st["report"].append(f"Google: помилка {e.code}. {msg[:160]}"); ok = False; break
            except Exception as e:
                print("GOOGLE FAIL", e); st["report"].append("Google: немає відповіді"); ok = False; break
            calls += 1
            for p in data.get("places", []):
                tot += 1
                name = (p.get("displayName") or {}).get("text")
                if p.get("businessStatus") not in (None, "OPERATIONAL"): continue
                raw = p.get("internationalPhoneNumber") or p.get("nationalPhoneNumber")
                if raw: ph += 1
                r_ = norm_any(raw)
                if not name or not r_ or r_[0] in known: continue
                if cfg.get("google_mobile_only", True) and not r_[1]: continue
                if cfg.get("google_only_without_site", False) and p.get("websiteUri"): continue
                if (p.get("userRatingCount") or 0) < cfg.get("google_min_reviews", 0): continue
                known.add(r_[0]); add += 1
                st["leads"]["g" + r_[0]] = {"name": name, "phone": r_[0], "mobile": r_[1],
                    "address": (p.get("formattedAddress") or "").replace(", Україна", ""), "city": city, "status": "new"}
            token = data.get("nextPageToken")
            if not token: break
        if not ok: return
        done.append(q); st["report"].append(f"Google «{q}»: знайдено {tot}, з телефоном {ph}, додано {add}")
    print("\n".join(st["report"]))

def gather(cfg, st):
    st["report"] = []
    import_csv(st)
    google_fill(cfg, st)
    if cfg.get("use_osm", False): refill(cfg, st)

def refill(cfg, st):
    need = cfg["per_day"] * 3
    known = {l["phone"] for l in st["leads"].values()}
    failed = []
    while sum(1 for l in st["leads"].values() if l["status"] == "new") < need and len(failed) < 2:
        todo = [c for c in cfg["cities"] if c["name"] not in st["fetched"] and c["name"] not in failed]
        if not todo: break
        c = todo[0]; els = None
        try:
            g = http("https://nominatim.openstreetmap.org/search?format=json&limit=1&q=" + urllib.parse.quote(c["name"] + ", Україна"))[0]
            lat, lon = g["lat"], g["lon"]; time.sleep(1.5)
            q = (f'[out:json][timeout:25];(nwr["shop"="car_repair"](around:{c["radius"]},{lat},{lon});'
                 f'nwr["amenity"="car_repair"](around:{c["radius"]},{lat},{lon}););out center tags;')
            for attempt in range(2):
                for ep in OVERPASS:
                    try: els = http(ep, urllib.parse.urlencode({"data": q}).encode(), 45)["elements"]; break
                    except Exception as e: print("overpass fail", ep, e)
                if els is not None: break
                time.sleep(8)
        except Exception as e:
            print("city fail", c["name"], e)
        if els is None:
            failed.append(c["name"]); st["report"].append(f'{c["name"]}: сервер OpenStreetMap не відповів'); continue
        st["fetched"].append(c["name"])
        n_ph = n_mob = n_add = 0
        for e in els:
            t = e.get("tags", {}); name = t.get("name")
            raw = t.get("phone") or t.get("contact:phone")
            if raw: n_ph += 1
            ph = norm(raw)
            if not name or not ph: continue
            n_mob += 1
            if ph in known: continue
            if cfg.get("only_without_site", True) and (t.get("website") or t.get("contact:website") or t.get("url")): continue
            addr = ", ".join(x for x in [t.get("addr:street"), t.get("addr:housenumber")] if x)
            lid = e["type"][0] + str(e["id"]); known.add(ph); n_add += 1
            st["leads"][lid] = {"name": name, "phone": ph, "mobile": True, "address": addr,
                "city": t.get("addr:city") or c["name"], "status": "new"}
        st["report"].append(f'{c["name"]}: знайдено СТО {len(els)}, з телефоном {n_ph}, з мобільним {n_mob}, додано {n_add}')
    print("\n".join(st["report"]))

def fmt_phone(n): return f"+{n[:3]} {n[3:5]} {n[5:8]} {n[8:10]} {n[10:]}"

def make_site(cfg, lid, l):
    tpl = open("template.html", encoding="utf-8").read()
    data = {"name": l["name"], "owner": "", "email": "", "city": l["city"], "address": l["address"],
        "phone": fmt_phone(l["phone"]), "whatsapp": l["phone"] if l.get("mobile", True) else "", "telegram": "", "viber": l.get("mobile", True),
        "open": None, "accent": cfg.get("accent", "#f24848"), "mode": cfg.get("form_mode", "viber") if l.get("mobile", True) else "endpoint",
        "reviews": [], "brands": ["Toyota","Volkswagen","Skoda","Renault","Hyundai","Kia","Ford","BMW","Mercedes","Nissan","Opel","Mazda"],
        "symptoms": [["Стукіт у підвісці","Ремонт підвіски"],["Не заводиться","Діагностика"],["Скрип гальм","Гальмівна система"],["Горить Check Engine","Діагностика"],["Пора міняти олію","Заміна оливи та фільтрів"]],
        "services": [["Діагностика","Комп'ютерна та ходової частини",None,"search"],["Заміна оливи та фільтрів","Олива, масляний і повітряний фільтри",None,"oil"],
            ["Ремонт підвіски","Важелі, стійки, сайлентблоки",None,"susp"],["Гальмівна система","Колодки, диски, прокачування",None,"brake"],["Шиномонтаж","Зняття, балансування, встановлення",None,"tire"]],
        "faq": [["Скільки коштує ремонт?","Назвемо вартість після огляду або діагностики, до початку робіт."],["Як записатися?","Залиште заявку на сайті або зателефонуйте нам."],["Чи можна привезти свої запчастини?","Так, уточніть це під час запису."]]}
    js = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    tpl = tpl.replace('" — СТО, "+C.city', '" — СТО"+(C.city?", "+C.city:"")').replace('", "+C.city+": ремонт', '(C.city?", "+C.city:"")+": ремонт')
    page = tpl.replace(MARK, "Object.assign(C," + js + ");\n" + MARK, 1)
    slug = "sto-" + lid
    os.makedirs(f"sites/{slug}", exist_ok=True)
    open(f"sites/{slug}/index.html", "w", encoding="utf-8").write(page)
    return f'{cfg["pages_base"].rstrip("/")}/sites/{slug}/'

def compose(cfg, l, url):
    return random.choice(cfg["templates"]).format(greeting=random.choice(cfg["greetings"]), name=l["name"],
        url=url, me=cfg["my_name"], portfolio=cfg["portfolio"])

def notify(cfg, l, url):
    text = compose(cfg, l, url)
    e = html.escape
    wa = f"https://wa.me/{l['phone']}"
    wal = wa + "?text=" + urllib.parse.quote(text)
    if len(wal) > 1500: wal = wa          # Telegram не приймає занадто довгі посилання у кнопках
    mob = l.get("mobile", True)
    head = (f"⏰ Час написати: <b>{e(l['name'])}</b>\n📞 {fmt_phone(l['phone'])}" + ("" if mob else " (стаціонарний, краще зателефонувати)")
            + f"\n{e(l['address'] or l['city'])}\n\nТекст нижче (натисни, щоб скопіювати)" + (", потім відкрий чат:" if mob else ":"))
    kb = ([[{"text": "💬 WhatsApp", "url": wal}]] if mob else []) + [[{"text": "🌐 Сайт", "url": url}]]
    try:
        tg("sendMessage", {"chat_id": CHAT, "parse_mode": "HTML", "disable_web_page_preview": True, "text": head,
            "reply_markup": {"inline_keyboard": kb}})
    except Exception:
        tg("sendMessage", {"chat_id": CHAT, "parse_mode": "HTML", "disable_web_page_preview": True,
            "text": head + (f"\n\nWhatsApp: {wa}" if mob else "") + f"\nСайт: {url}"})
    tg("sendMessage", {"chat_id": CHAT, "parse_mode": "HTML", "text": f"<code>{e(text)}</code>"})

def times(n, w0, w1, gap=20):
    for _ in range(300):
        ts = sorted(random.sample(range(w0, w1), n))
        if all(b - a >= gap for a, b in zip(ts, ts[1:])): return ts
    step = (w1 - w0) // n
    return [w0 + i * step + random.randint(0, step // 2) for i in range(n)]

def main():
    cfg = jload("config.json", {}); assert "ТВІЙ" not in cfg["pages_base"], "Впиши pages_base у config.json"
    st = jload("state.json", {"leads": {}, "fetched": [], "plan": None})
    now = datetime.now(TZ); nm = now.hour * 60 + now.minute
    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":      # ручний тест: одразу один контакт
        gather(cfg, st)
        new = [k for k, l in st["leads"].items() if l["status"] == "new"]
        if new:
            k = new[0]; l = st["leads"][k]; notify(cfg, l, make_site(cfg, k, l)); l["status"] = "sent"
        else: tg("sendMessage", {"chat_id": CHAT, "text": "Нових контактів немає. Додай рядки у файл leads.csv: назва; телефон; адреса; місто.\n" + "\n".join(st.get("report", []))})
        jsave("state.json", st); return
    if now.weekday() not in cfg["days"]: return
    w0, w1 = cfg["window"][0] * 60, cfg["window"][1] * 60
    plan = st.get("plan")
    if not plan or plan["date"] != now.date().isoformat():
        start = max(w0, nm + 10); n = min(cfg["per_day"], (w1 - start) // 20)
        if n <= 0: jsave("state.json", st); return
        gather(cfg, st)
        new = [k for k, l in st["leads"].items() if l["status"] == "new"][:n]
        if not new:
            tg("sendMessage", {"chat_id": CHAT, "text": "Нових контактів немає. Додай рядки у файл leads.csv: назва; телефон; адреса; місто.\n" + "\n".join(st.get("report", []))}); jsave("state.json", st); return
        ts = times(len(new), start, w1)
        plan = {"date": now.date().isoformat(), "items": []}
        for k, t in zip(new, ts):
            l = st["leads"][k]; l["status"] = "planned"
            plan["items"].append({"id": k, "at": t, "url": make_site(cfg, k, l), "sent": False})
        st["plan"] = plan
        tg("sendMessage", {"chat_id": CHAT, "text": "📋 План на сьогодні: " + ", ".join(f"{t//60}:{t%60:02d}" for t in ts)})
    for it in plan["items"]:
        if not it["sent"] and it["at"] <= nm:
            l = st["leads"][it["id"]]; notify(cfg, l, it["url"]); it["sent"] = True; l["status"] = "sent"
    jsave("state.json", st)

if __name__ == "__main__": main()
