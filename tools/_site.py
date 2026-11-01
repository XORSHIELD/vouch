import urllib.request, re
UA={"User-Agent":"Mozilla/5.0"}
for url in ("https://greenbook.nafdac.gov.ng/",):
    try:
        req=urllib.request.Request(url, headers=UA)
        html=urllib.request.urlopen(req, timeout=40).read().decode("utf-8","replace")
        print(url, len(html))
        for m in re.findall(r"<a[^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>", html, re.S)[:60]:
            t=re.sub(r"<[^>]+>","",m[1]).strip()[:40]
            if t: print("  ", m[0][:70], "|", t)
        for kw in ("food","beverage","cosmetic","water","category","select"):
            idx=html.lower().find(kw)
            print(f"  [{kw}] at {idx}")
    except Exception as e:
        print(url, "ERR", e)
