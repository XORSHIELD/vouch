import json, time
import ai, barcode as B, engine, vision

print("ai:", ai.status())
print("barcode:", B.capabilities())

for d in B.demos():
    p = d["pack"]
    pack = engine.PackInput(barcode=p["barcode"], product_name=p["product_name"],
                            manufacturer=p["manufacturer"], batch=p["batch"],
                            exp_date=p["exp_date"], mfg_date=p["mfg_date"])
    a = engine.assess(pack)
    got = a.band
    flag = "OK " if got == d["expect"] else "MISMATCH "
    print(f"{flag}{d['key']:16} expect={d['expect']:9} got={got:9} kind={a.kind:9} rail={a.rail}")
    if got != d["expect"]:
        for c in a.checks:
            print("      ", c.id, c.state, c.evidence[:100])

print()
a = engine.assess(engine.PackInput(barcode="5010162000164",
                                   product_name="Malta Gold Non-alcoholic Can 330ml",
                                   manufacturer="Malta Guinness",
                                   exp_date="EXP 06/2027"))
print("EXPLAIN:", ai.explain(a)[:600])
print()
print("ASK fake:", ai.answer("is it fake?", a)[:400])
print()
print("SAFETY:", ai.answer("can i drink it?", a)[:400])
print()
print("violations:", ai.violations(ai.explain(a), a))

t = "NAFDAC REG NO: A11-0551\nBATCH: ABC123\nEXP 03/2027\nBARCODE 5010162000164\nMalta Gold"
ex = vision.parse_label_text(t)
print("\nvision barcode:", ex.barcode, "| nafdac:", ex.nafdac_number, "| name:", ex.product_name)
t2 = "PARACETAMOL 500MG\nNAFDAC 04-0271\nBATCH B40262\nEXP 01/2027\n8901234567890"
ex2 = vision.parse_label_text(t2)
print("vision bare:", ex2.barcode, "| nafdac:", ex2.nafdac_number)
t3 = "MFG 01/01/2027\nEXP 01/01/2029\nNAFDAC 04-0858"
print("vision none:", repr(vision.parse_label_text(t3).barcode))
