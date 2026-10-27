import json, collections, registry as R

cats = collections.Counter()
prod = collections.Counter()
examples = {}
start = 0
while True:
    p = R.search("", start=start, length=1000)
    data = p.get("data") or []
    if not data: break
    for d in data:
        cn = (d.get("category_name") or "").strip()
        pc = d.get("product_category") or {}
        if isinstance(pc, dict):
            pc = (pc.get("category_name") or pc.get("name") or str(pc))[:60]
        else:
            pc = str(pc)[:60]
        cats[cn] += 1
        prod[pc] += 1
        if cn not in examples:
            examples[cn] = (d.get("NAFDAC"), d.get("product_name"), d.get("form_name"), d.get("status"))
    start += len(data)
    if start >= p["recordsTotal"]: break

print("category_name:", cats.most_common())
print()
print("product_category:", prod.most_common(30))
print()
for k, v in examples.items():
    print(f"{k!r:26} -> {v}")
