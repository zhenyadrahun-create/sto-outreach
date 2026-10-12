#!/usr/bin/env python3
"""Пошук СТО (OpenStreetMap) -> сайти -> нагадування у Telegram у випадкові години. Нічого не шле клієнтам сам."""
import json, os, re, random, html, time, urllib.request, urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Kyiv")
UA = "sto-outreach/1.0 (personal lead tool)"
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
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
    urllib.request.urlopen(req, timeout=30).read()

def norm(p):
    for part in re.split(r"[;,/]", p or ""):
        d = re.sub(r"\D", "", part)
        n = d if (d.startswith("380") and len(d) == 12) else ("38" + d if (d.startswith("0") and len(d) == 10) else None)
        if n and n[2:5] in MOBILE: return n
    return None

def refill(cfg, st):
    need = cfg["per_day"] * 3
    known = {l["phone"] for l in st["leads"].values()}
    while sum(1 for l in st["leads"].values() if l["status"] == "new") < need:
        todo = [c for c in cfg["cities"] if c["name"] not in st["fetched"]]
        if not todo: break
        c = todo[0]; st["fetched"].append(c["name"])
        try:
            g = http("https://nominatim.openstreetmap.org/search?format=json&limit=1&q=" + urllib.parse.quote(c["name"] + ", Україна"))[0]
            lat, lon = g["lat"], g["lon"]; time.sleep(1.5)
            q = (f'[out:json][timeout:60];(nwr["shop"="car_repair"](around:{c["radius"]},{lat},{lon});'
                 f'nwr["amenity"="car_repair"](around:{c["radius"]},{lat},{lon}););out center tags;')
            els = None
            for ep in OVERPASS:
                try: els = http(ep, urllib.parse.urlencode({"data": q}).encode(), 120)["elements"]; break
                except Exception as e: print("overpass fail", ep, e)
            if els is None: st["fetched"].remove(c["name"]); break
        except Exception as e:
            print("city fail", c["name"], e); continue
        for e in els:
            t = e.get("tags", {}); name = t.get("name"); ph = norm(t.get("phone") or t.get("contact:phone"))
            if not name or not ph or ph in known: continue
            if cfg.get("only_without_site", True) and (t.get("website") or t.get("contact:website") or t.get("url")): continue
            addr = ", ".join(x for x in [t.get("addr:street"), t.get("addr:housenumber")] if x)
            lid = e["type"][0] + str(e["id"]); known.add(ph)
            st["leads"][lid] = {"name": name, "phone": ph, "address": addr,
                "city": t.get("addr:city") or c["name"], "status": "new"}

def fmt_phone(n): return f"+{n[:3]} {n[3:5]} {n[5:8]} {n[8:10]} {n[10:]}"

def make_site(cfg, lid, l):
    tpl = open("template.html", encoding="utf-8").read()
    data = {"name": l["name"], "owner": "", "email": "", "city": l["city"], "address": l["address"],
        "phone": fmt_phone(l["phone"]), "whatsapp": l["phone"], "telegram": "", "viber": True,
        "open": None, "accent": cfg.get("accent", "#f24848"), "mode": cfg.get("form_mode", "viber"),
        "reviews": [], "brands": ["Toyota","Volkswagen","Skoda","Renault","Hyundai","Kia","Ford","BMW","Mercedes","Nissan","Opel","Mazda"],
        "symptoms": [["Стукіт у підвісці","Ремонт підвіски"],["Не заводиться","Діагностика"],["Скрип гальм","Гальмівна система"],["Горить Check Engine","Діагностика"],["Пора міняти олію","Заміна оливи та фільтрів"]],
        "services": [["Діагностика","Комп'ютерна та ходової частини",None,"search"],["Заміна оливи та фільтрів","Олива, масляний і повітряний фільтри",None,"oil"],
            ["Ремонт підвіски","Важелі, стійки, сайлентблоки",None,"susp"],["Гальмівна система","Колодки, диски, прокачування",None,"brake"],["Шиномонтаж","Зняття, балансування, встановлення",None,"tire"]],
        "faq": [["Скільки коштує ремонт?","Назвемо вартість після огляду або діагностики, до початку робіт."],["Як записатися?","Залиште заявку на сайті або зателефонуйте нам."],["Чи можна привезти свої запчастини?","Так, уточніть це під час запису."]]}
    js = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
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
    tg("sendMessage", {"chat_id": CHAT, "parse_mode": "HTML", "disable_web_page_preview": True,
        "text": f"⏰ Час написати: <b>{e(l['name'])}</b>\n📞 {fmt_phone(l['phone'])}\n{e(l['address'] or l['city'])}\n\nТекст нижче (натисни, щоб скопіювати), потім відкрий чат:",
        "reply_markup": {"inline_keyboard": [
            [{"text": "💬 WhatsApp з текстом", "url": f"https://wa.me/{l['phone']}?text=" + urllib.parse.quote(text)}],
            [{"text": "✈️ Telegram", "url": f"https://t.me/+{l['phone']}"}, {"text": "🌐 Сайт", "url": url}]]}})
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
        refill(cfg, st)
        new = [k for k, l in st["leads"].items() if l["status"] == "new"]
        if new:
            k = new[0]; l = st["leads"][k]; notify(cfg, l, make_site(cfg, k, l)); l["status"] = "sent"
        else: tg("sendMessage", {"chat_id": CHAT, "text": "Нових контактів не знайшов. Додай міста в config.json."})
        jsave("state.json", st); return
    if now.weekday() not in cfg["days"]: return
    w0, w1 = cfg["window"][0] * 60, cfg["window"][1] * 60
    plan = st.get("plan")
    if not plan or plan["date"] != now.date().isoformat():
        start = max(w0, nm + 10); n = min(cfg["per_day"], (w1 - start) // 20)
        if n <= 0: jsave("state.json", st); return
        refill(cfg, st)
        new = [k for k, l in st["leads"].items() if l["status"] == "new"][:n]
        if not new:
            tg("sendMessage", {"chat_id": CHAT, "text": "Нових контактів немає. Додай міста в config.json."}); return
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
