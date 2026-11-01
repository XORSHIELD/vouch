import json, os

raw = json.load(open("_barcodes_raw.json", encoding="utf-8"))
KEEP = ("product_name", "generic_name", "brands", "categories", "quantity",
        "ingredients_text", "allergens", "countries", "image_front_small_url",
        "labels", "manufacturing_places", "nutriscore_grade")

products = {}
sources = {
    "5010162000164": ("openfoodfacts", "https://world.openfoodfacts.org"),
    "6151100034823": ("openfoodfacts", "https://world.openfoodfacts.org"),
    "6156000261201": ("openfoodfacts", "https://world.openfoodfacts.org"),
    "0110800126258": ("openfoodfacts", "https://world.openfoodfacts.org"),
    "4005900376978": ("openbeautyfacts", "https://world.openbeautyfacts.org"),
}
for code, (src, base) in sources.items():
    p = raw.get(code) or {}
    d = {k: (p.get(k) or "") for k in KEEP}
    d["_source"] = src
    d["_base"] = base
    products[code] = d

demos = [
    {
        "key": "drink", "chip": "A malt drink",
        "barcode": "5010162000164",
        "title": "A can of malt drink",
        "note": "The barcode resolves to the product on the can. Food and drink are not in the NAFDAC public register, so the barcode is the identity check.",
        "pack": {"barcode": "5010162000164",
                 "product_name": "Malta Gold Non-alcoholic Can 330ml",
                 "manufacturer": "Malta Guinness", "batch": "MG2401A",
                 "exp_date": "EXP 06/2027", "mfg_date": "MFG 06/2026"},
        "expect": "clear",
    },
    {
        "key": "noodles", "chip": "Instant noodles",
        "barcode": "0110800126258",
        "title": "A pack of instant noodles",
        "note": "Same rail, a different category. The check is the same shape: does the code on the pack resolve to the thing on the pack?",
        "pack": {"barcode": "0110800126258",
                 "product_name": "Indomie Instant Noodles",
                 "manufacturer": "", "batch": "IND2409",
                 "exp_date": "EXP 03/2027", "mfg_date": "MFG 09/2025"},
        "expect": "clear",
    },
    {
        "key": "water", "chip": "Sachet water",
        "barcode": "6156000261201",
        "title": "A bottle of water",
        "note": "Short product names are where naive string matching accuses honest packs. Vouch treats a name contained in the pack's name as a match.",
        "pack": {"barcode": "6156000261201",
                 "product_name": "Cway Drinking Water",
                 "manufacturer": "Cway", "batch": "CW2406",
                 "exp_date": "EXP 12/2026", "mfg_date": "MFG 06/2026"},
        "expect": "clear",
    },
    {
        "key": "cosmetic", "chip": "A body lotion",
        "barcode": "4005900376978",
        "title": "A cosmetic pack",
        "note": "Cosmetics run through Open Beauty Facts. NAFDAC regulates them; this is what can be checked without a public register.",
        "pack": {"barcode": "4005900376978",
                 "product_name": "NIVEA Pearl & Beauty Antiperspirant",
                 "manufacturer": "NIVEA", "batch": "NPB2403",
                 "exp_date": "EXP 09/2029", "mfg_date": "MFG 09/2026"},
        "expect": "clear",
    },
    {
        "key": "copied_barcode", "chip": "The code is for another product",
        "barcode": "6151100034823",
        "title": "A pack whose barcode belongs to something else",
        "note": "The drink equivalent of a copied NAFDAC number: the barcode on the pack resolves to a different product entirely.",
        "pack": {"barcode": "6151100034823",
                 "product_name": "Malta Guinness Malt Drink",
                 "manufacturer": "Malta Guinness", "batch": "MG2401A",
                 "exp_date": "EXP 06/2027", "mfg_date": "MFG 06/2026"},
        "expect": "high_risk",
    },
    {
        "key": "unknown_barcode", "chip": "A code nobody has seen",
        "barcode": "6151100099990",
        "title": "A valid barcode that is in no database",
        "note": "A valid check digit, no record. That is not proof of anything: a new product, a shop's own repack and a falsified pack all look like this.",
        "pack": {"barcode": "6151100099990",
                 "product_name": "Golden Penny Semovita",
                 "manufacturer": "Golden Penny", "batch": "GP2405",
                 "exp_date": "EXP 02/2027", "mfg_date": "MFG 08/2025"},
        "expect": "review",
    },
    {
        "key": "bad_barcode", "chip": "A barcode that cannot exist",
        "barcode": "6151100099997",
        "title": "A barcode whose own check digit fails",
        "note": "EAN/UPC codes carry their own arithmetic. If the check digit fails, the number as printed cannot be a real barcode — most often it is a mistyped digit.",
        "pack": {"barcode": "6151100099997",
                 "product_name": "Cway Drinking Water",
                 "manufacturer": "Cway", "batch": "CW2406",
                 "exp_date": "EXP 12/2026", "mfg_date": "MFG 06/2026"},
        "expect": "review",
    },
    {
        "key": "expired_drink", "chip": "A drink past its date",
        "barcode": "5010162000164",
        "title": "A drink whose best-before has passed",
        "note": "The same expiry arithmetic applies to a drink. It is its own finding and needs no claim about what is inside.",
        "pack": {"barcode": "5010162000164",
                 "product_name": "Malta Gold Non-alcoholic Can 330ml",
                 "manufacturer": "Malta Guinness", "batch": "MG1902A",
                 "exp_date": "EXP 01/2026", "mfg_date": "MFG 01/2025"},
        "expect": "high_risk",
    },
]

out = {
    "_note": ("Products below are real records from Open Food Facts / Open Beauty "
              "Facts, trimmed. Demo packs are labelled rehearsals: the barcode is "
              "real, the pack text around it is constructed."),
    "products": products,
    "demos": demos,
}
json.dump(out, open("fixtures/barcodes.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("products", len(products), "demos", len(demos))
for d in demos:
    print(f"  {d['key']:18} {d['barcode']:14} {d['expect']}")
