import json, time, urllib.request, urllib.parse
UA={"User-Agent":"Vouch/0.4 (hackathon)"}
def get(url, tries=4):
    last=None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers=UA),timeout=40) as r:
                return json.loads(r.read().decode("utf-8","replace"))
        except Exception as e:
            last=e; time.sleep(5+i*5)
    raise last
for term in ("indomie","maggi"):
    u=("https://world.openfoodfacts.org/cgi/search.pl?search_terms="+urllib.parse.quote(term)+
       "&tagtype_0=countries&tag_contains_0=contains&tag_0=nigeria&json=1&page_size=12"
       "&fields=code,product_name,brands,categories,countries")
    try:
        d=get(u)
        print("==",term,d.get("count"))
        for p in (d.get("products") or [])[:12]:
            print("  ",p.get("code"),"|",str(p.get("product_name"))[:44],"|",str(p.get("brands"))[:20],"|",str(p.get("categories"))[:40])
    except Exception as e:
        print(term,"ERR",str(e)[:70])
    time.sleep(4)
