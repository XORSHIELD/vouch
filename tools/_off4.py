import json, os, time, urllib.request

UA = {"User-Agent": "Vouch/0.4 (hackathon; contact vouch@example.com)"}

def get(url, tries=5):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            last = e
            time.sleep(5 + i * 5)
    raise last

codes_path = "_codes.json"
if os.path.exists(codes_path):
    codes = json.load(open(codes_path))
else:
    codes = []
u = ("https://world.openfoodfacts.org/cgi/search.pl?search_terms=&"
     "tagtype_0=countries&tag_contains_0=contains&tag_0=nigeria"
     "&json=1&page_size=80&fields=code,product_name,brands")
d = get(u)
for p in d.get("products") or []:
    c = p.get("code")
    if c and c not in codes:
        codes.append(c)
json.dump(codes, open(codes_path, "w"))
print("codes", len(codes))

samples = []
if os.path.exists("_off_samples.json"):
    samples = json.load(open("_off_samples.json", encoding="utf-8"))
have = {p.get("code") for p in samples}
for c in codes:
    if c in have:
        continue
    if len(samples) >= 25:
        break
    try:
        d = get(f"https://world.openfoodfacts.org/api/v2/product/{c}.json?fields=code,product_name,brands,categories,quantity,ingredients_text,allergens,countries,image_front_small_url,nutriscore_grade,labels")
        p = d.get("product") or {}
        if p.get("product_name"):
            samples.append(p); have.add(c)
            print(f"  {c:16} {str(p.get('product_name'))[:36]:38} {str(p.get('brands'))[:18]:20} {str(p.get('categories'))[:44]}")
            json.dump(samples, open("_off_samples.json","w",encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception as e:
        print("  ", c, "ERR", str(e)[:60])
    time.sleep(1.5)
print("saved", len(samples))
