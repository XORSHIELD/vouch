import json, time, urllib.request
UA={"User-Agent":"Vouch/0.4 (hackathon)"}
FIELDS=("code,product_name,generic_name,brands,categories,quantity,ingredients_text,"
        "allergens,countries,image_front_small_url,labels,manufacturing_places,nutriscore_grade")
def get(url):
    with urllib.request.urlopen(urllib.request.Request(url,headers=UA),timeout=40) as r:
        return json.loads(r.read().decode("utf-8","replace"))
raw=json.load(open("_barcodes_raw.json",encoding="utf-8"))
for code in ("0110800126258",):
    d=get(f"https://world.openfoodfacts.org/api/v2/product/{code}.json?fields={FIELDS}")
    p=d.get("product") or {}
    raw[code]=p
    print(d.get("status"), p.get("product_name"), "|", p.get("brands"), "|", p.get("categories"), "|", p.get("quantity"), "|", p.get("countries"))
json.dump(raw,open("_barcodes_raw.json","w",encoding="utf-8"),ensure_ascii=False,indent=1)
