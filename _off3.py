import json, time, urllib.request

UA = {"User-Agent": "Vouch/0.4 (hackathon)"}

def get(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            last = e; time.sleep(2 + i * 2)
    raise last

codes = []
for page in (1, 2, 3):
    u = ("https://world.openfoodfacts.org/cgi/search.pl?search_terms=&"
         "tagtype_0=countries&tag_contains_0=contains&tag_0=nigeria"
         f"&json=1&page_size=50&page={page}&fields=code,product_name,brands,categories")
    d = get(u)
    for p in d.get("products") or []:
        c = p.get("code")
        if c and c not in codes:
            codes.append(c)
    print("page", page, "codes", len(codes))
    time.sleep(1)

print("total codes", len(codes))
out = []
for c in codes[:40]:
    try:
        d = get(f"https://world.openfoodfacts.org/api/v2/product/{c}.json?fields=code,product_name,brands,categories,quantity,ingredients_text,allergens,countries,image_front_small_url,nutriscore_grade,labels")
        p = d.get("product") or {}
        out.append(p)
        print(f"  {c:16} {str(p.get('product_name'))[:38]:40} {str(p.get('brands'))[:20]:22} {str(p.get('categories'))[:46]}")
    except Exception as e:
        print("  ", c, "ERR", e)
    time.sleep(0.4)

json.dump(out, open("_off_samples.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("saved", len(out))
