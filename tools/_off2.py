import json, time, urllib.request, urllib.parse

UA = {"User-Agent": "Vouch/0.4 (hackathon)"}

def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

urls = [
  "https://world.openfoodfacts.org/api/v2/search?countries_tags=nigeria&page_size=10&fields=code,product_name,brands,categories",
  "https://world.openfoodfacts.org/cgi/search.pl?search_terms=&tagtype_0=countries&tag_contains_0=contains&tag_0=nigeria&json=1&page_size=10&fields=code,product_name,brands,categories",
  "https://ng.openfoodfacts.org/api/v2/search?page_size=10&fields=code,product_name,brands,categories",
]
for u in urls:
    for attempt in range(2):
        try:
            d = get(u)
            print("\nOK", u[:80])
            print(" count", d.get("count"))
            for p in (d.get("products") or [])[:10]:
                print("  ", p.get("code"), "|", (p.get("product_name") or "")[:40], "|", (p.get("brands") or "")[:25], "|", (p.get("categories") or "")[:45])
            break
        except Exception as e:
            print("ERR", attempt, u[:60], e)
            time.sleep(2)
