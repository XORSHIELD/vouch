"""
Published-list importer tests: NAFDAC bulletins become seed entries.

A published list is weaker provenance than a portal reply, so entries
say so and every answer cites the list by name. No network: list
bodies are inline fixtures.

Run:  python test_lists.py
"""

from __future__ import annotations

import os
import tempfile

os.environ.pop("MODEL_API_KEY", None)
os.environ.pop("META_API_KEY", None)

import agent
import lists
import napams

agent.API_KEY = ""

# Hermetic NAPAMS memory: neither cache nor seed may leak in.
_napams_real_cache = napams.CACHE_FILE
_napams_real_seed = napams.SEED_FILE
_napams_tmp = tempfile.NamedTemporaryFile(
    prefix="lists_cache_", suffix=".json", delete=False)
_napams_tmp.close()
_napams_seed = tempfile.NamedTemporaryFile(
    prefix="lists_seed_", suffix=".json", delete=False)
_napams_seed.close()
napams.CACHE_FILE = _napams_tmp.name
napams.SEED_FILE = _napams_seed.name

fails = 0
LABEL = "Test Bulletin, 2026-10-01"

CSV = """number,product,maker,category,approved,expiry,status
79-2468,LIST TEST DRINK,List Works Ltd,Beverage,2024-02-01,2029-01-31,active
79-2469,BARE LIST WATER,,,,,
,NO NUMBER ROW,Someone,,,,,
79-2470,,,,,,
79-2471,OLD LIST TONIC,Old Ltd,Beverage,2020-01-01,2022-01-01,expired
"""


def check(label: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        fails += 1


print("csv parsing")
entries, errors = lists.load_csv(CSV, LABEL)
check("full row parsed",
      entries.get("792468", {}).get("product") == "LIST TEST DRINK"
      and entries["792468"]["maker"] == "List Works Ltd"
      and entries["792468"]["category"] == "Beverage"
      and entries["792468"]["approved"] == "2024-02-01"
      and entries["792468"]["expiry"] == "2029-01-31"
      and entries["792468"]["verdict"] == napams.REGISTERED
      and entries["792468"]["first_seen"] == "2026-10-01"
      and entries["792468"]["hits"] == 1
      and entries["792468"]["source"] == "list"
      and entries["792468"]["list"] == LABEL,
      repr(entries.get("792468"))[:300])
check("minimal row defaults cleanly",
      entries.get("792469", {}).get("product") == "BARE LIST WATER"
      and entries["792469"]["maker"] == ""
      and entries["792469"]["verdict"] == napams.REGISTERED,
      repr(entries.get("792469"))[:200])
check("bad rows rejected with line numbers",
      len(errors) == 2 and "line 4" in errors[0]
      and "line 5" in errors[1],
      repr(errors)[:200])
check("expired status mapped",
      entries.get("792471", {}).get("verdict") == napams.EXPIRED,
      repr(entries.get("792471"))[:150])

print("name hygiene")
entries2, _ = lists.load_csv(
    "number,product\n79-2472,TEST WATER (79-2472)(79-2472)\n", LABEL)
check("number suffix stripped from product",
      entries2.get("792472", {}).get("product") == "TEST WATER",
      repr(entries2.get("792472"))[:150])

print("json parsing")
entries3, errors3 = lists.load_json(
    '[{"number": "79-2473", "product": "JSON LIST JUICE",'
    ' "manufacturer": "Json Ltd", "status": "registered"},'
    ' {"product": "missing number"}]', LABEL)
check("json rows parsed, bad row reported",
      entries3.get("792473", {}).get("product") == "JSON LIST JUICE"
      and entries3["792473"]["maker"] == "Json Ltd"
      and len(errors3) == 1,
      repr((entries3, errors3))[:250])

print("merge")
counts = lists.merge(_napams_seed.name, entries)
check("fresh entries added",
      counts == {"added": 3, "kept": 0, "updated": 0}, repr(counts))
counts = lists.merge(_napams_seed.name, entries)
check("same list re-imported without dupes",
      counts == {"added": 0, "kept": 0, "updated": 3}, repr(counts))
other, _ = lists.load_csv("number,product\n79-2468,OTHER NAME\n",
                          "Other Bulletin, 2026-11-01")
counts = lists.merge(_napams_seed.name, other)
check("rival list never clobbers",
      counts == {"added": 0, "kept": 1, "updated": 0}
      and napams.known("79-2468")["product"] == "LIST TEST DRINK",
      repr(counts))

print("list provenance in answers")
ans = agent.chat("79-2468", [])["answer"]
check("chat cites the published list",
      "NAFDAC's published Test Bulletin, 2026-10-01" in ans
      and "LIST TEST DRINK" in ans and "not a live lookup" in ans
      and "NAPAMS portal" not in ans, ans[:500])
import sms
reply = sms.handle_inbound("NAFDAC 79-2468")
check("sms cites the published list",
      "Test Bulletin" in reply and "LIST TEST DRINK" in reply
      and "NAPAMS lists" not in reply, reply[:250])
import engine as E
rep = E.assess(E.PackInput(nafdac_number="79-2468"), offline=True)
reg_chk = next(c for c in rep.checks if c.id == "registry")
check("engine evidence cites the published list",
      reg_chk.state == E.ATTENTION
      and "published Test Bulletin, 2026-10-01" in reg_chk.evidence
      and "LIST TEST DRINK" in reg_chk.evidence,
      reg_chk.evidence[:300])

napams.CACHE_FILE = _napams_real_cache
napams.SEED_FILE = _napams_real_seed
os.unlink(_napams_tmp.name)
os.unlink(_napams_seed.name)

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
