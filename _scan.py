import json, collections, registry as R

# Raw keys, first record
p = R.search("", length=3)
print("keys:", sorted(p["data"][0].keys()))
print()
print(json.dumps(p["data"][0], indent=1)[:900])

print("\n=== form_name values over the whole register ===")
forms = collections.Counter()
cats = collections.Counter()
start = 0
total = p["recordsTotal"]
while start < total:
    payload = R.search("", start=start, length=1000)
    data = payload.get("data") or []
    if not data: break
    for d in data:
        forms[(d.get("form_name") or "").strip()] += 1
        cats[str(d.get("marketing_category_id"))] += 1
    start += len(data)
print("total", total, "fetched", start)
for k, v in forms.most_common(40):
    print(f"  {k!r:40} {v}")
print("\ncategories:", cats.most_common(20))
