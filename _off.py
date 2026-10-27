import json, urllib.request, urllib.parse

UA = {"User-Agent": "Vouch/0.4 (hackathon; contact: vouch@example.com)"}

def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

tests = ["5449000000996", "6151100000106", "8901058000999"]
for b in tests:
    try:
        d = get(f"https://world.openfoodfacts.org/api/v2/product/{b}.json?fields=product_name,brands,categories,ingredients_text,allergens,countries,quantity,image_front_small_url,nutriscore_grade")
        p = d.get("product") or {}
        print(b, d.get("status"), "|", p.get("product_name"), "|", p.get("brands"), "|", (p.get("categories") or "")[:60], "|", (p.get("countries") or "")[:40])
    except Exception as e:
        print(b, "ERR", e)

print()
q = "https://world.openfoodfacts.org/api/v2/search?countries_tags_en=nigeria&fields=code,product_name,brands,categories&page_size=8"
try:
    d = get(q)
    print("count", d.get("count"))
    for p in d.get("products", []):
        print(" ", p.get("code"), "|", p.get("product_name"), "|", p.get("brands"), "|", (p.get("categories") or "")[:50])
except Exception as e:
    print("search ERR", e)
