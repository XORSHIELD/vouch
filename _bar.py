import json, time, urllib.request

UA = {"User-Agent": "Vouch/0.4 (hackathon)"}

def get(url, tries=4):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            last = e; time.sleep(4 + i * 4)
    raise last

FIELDS = ("code,product_name,brands,categories,quantity,ingredients_text,"
          "allergens,countries,image_front_small_url,nutriscore_grade,labels,"
          "manufacturing_places,generic_name")

wanted = {
  "5010162000164": "malt drink",
  "6151100034823": "milo sachet",
  "6156000261201": "sachet water",
  "10340006": "noodles",
}
out = {}
for code, label in wanted.items():
    try:
        d = get(f"https://world.openfoodfacts.org/api/v2/product/{code}.json?fields={FIELDS}")
        p = d.get("product") or {}
        out[code] = p
        print(f"{label:14} {d.get('status')} {str(p.get('product_name'))[:34]:36} {str(p.get('brands'))[:18]:20} {str(p.get('quantity'))[:12]}")
    except Exception as e:
        print(label, "ERR", str(e)[:80])
    time.sleep(1.5)

# beauty facts
try:
    d = get("https://world.openbeautyfacts.org/cgi/search.pl?search_terms=&"
            "tagtype_0=countries&tag_contains_0=contains&tag_0=nigeria&json=1"
            "&page_size=20&fields=code,product_name,brands,categories")
    print("\nbeauty count", d.get("count"))
    for p in (d.get("products") or [])[:12]:
        print("  ", p.get("code"), "|", str(p.get("product_name"))[:40], "|", str(p.get("brands"))[:20], "|", str(p.get("categories"))[:40])
except Exception as e:
    print("beauty ERR", str(e)[:100])

json.dump(out, open("_barcodes_raw.json","w",encoding="utf-8"), ensure_ascii=False, indent=1)
print("\nsaved", len(out))
