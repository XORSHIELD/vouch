import json, time, urllib.request
UA={"User-Agent":"Vouch/0.4 (hackathon)"}
def get(url, tries=4):
    last=None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers=UA),timeout=40) as r:
                return json.loads(r.read().decode("utf-8","replace"))
        except Exception as e:
            last=e; time.sleep(4+i*4)
    raise last
FIELDS=("code,product_name,brands,categories,quantity,ingredients_text,allergens,"
        "countries,image_front_small_url,labels,manufacturing_places,generic_name")
out=json.load(open("_barcodes_raw.json",encoding="utf-8"))
for code in ("8710447362945","4005900376978","8710908777769"):
    try:
        d=get(f"https://world.openbeautyfacts.org/api/v2/product/{code}.json?fields={FIELDS}")
        p=d.get("product") or {}
        out[code]=p
        print(code, d.get("status"), "|", p.get("product_name"), "|", p.get("brands"), "|", p.get("categories"), "|", (p.get("countries") or "")[:60])
    except Exception as e:
        print(code,"ERR",str(e)[:70])
    time.sleep(1.5)
json.dump(out,open("_barcodes_raw.json","w",encoding="utf-8"),ensure_ascii=False,indent=1)

def ean13(body12):
    s=sum((1 if i%2==0 else 3)*int(c) for i,c in enumerate(body12))
    return body12+str((10-s%10)%10)
for b in ("615110009999","615600009999","615110012345"):
    print("ean13", b, "->", ean13(b))
