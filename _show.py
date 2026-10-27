import json
raw = json.load(open("_barcodes_raw.json", encoding="utf-8"))
for code, p in raw.items():
    print(code)
    for k in ("product_name","brands","categories","quantity","countries","image_front_small_url","ingredients_text","allergens","labels"):
        v = p.get(k)
        print(f"   {k:22} {str(v)[:110]}")
    print()
