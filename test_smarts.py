"""
Smarter-agent tests: tool registry, playbooks, knowledge retrieval,
and case-linked memory.

No server, no network, no model key: memory runs against a temporary
database and the planner is never called with a live key.

Run:  python test_smarts.py
"""

from __future__ import annotations

import os
import tempfile
import time

TMP = tempfile.mkdtemp(prefix="vouch-smarts-")
os.environ["VOUCH_DATA_DIR"] = TMP
os.environ.pop("MODEL_API_KEY", None)
os.environ.pop("META_API_KEY", None)
os.environ.pop("VOUCH_MAS_URL", None)

import agent
import cases
import knowledge
import napams
import playbooks
import recall
import registry as R
from engine import Assessment, PackInput

# Hermetic NAPAMS memory: the real cache must not sway these chats.
_napams_real_cache = napams.CACHE_FILE
_napams_tmp = tempfile.NamedTemporaryFile(
    prefix="napams_test_", suffix=".json", delete=False)
_napams_tmp.close()
napams.CACHE_FILE = _napams_tmp.name

agent.API_KEY = ""

fails = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        fails += 1


def _assess() -> Assessment:
    return Assessment(band="review", headline="test headline", checks=[],
                     actions=[], score=1.5, registry=None, input={})


def _pack(**kw) -> PackInput:
    base = {"nafdac_number": "A11-0551", "product_name": "Test Tablet",
            "batch": "B1", "seller": "Shop", "location": "Lagos",
            "price": "100", "exp_date": "EXP 2028"}
    base.update(kw)
    return PackInput(**base)


print("tool registry")
check("six tools registered",
      set(agent.TOOLS) == {"check_pack", "lookup_number", "verify_pin",
                           "verify_serial", "preview_document", "ask"},
      repr(sorted(agent.TOOLS)))
for _name, _entry in agent.TOOLS.items():
    check(f"{_name} carries runner + docs",
          callable(_entry.get("run")) and bool(_entry.get("args"))
          and bool(_entry.get("why")), repr(_entry)[:120])
check("prompt documents every tool",
      all(f'"tool": "{n}"' in agent.SYSTEM for n in agent.TOOLS))
_EXPECTED_LINES = [
    '{"thought": str, "tool": "check_pack", "args": {"pack": '
    '{nafdac_number, barcode, manufacturer, product_name, batch, serial, '
    'mfg_date, exp_date, seller, location}}}',
    '{"thought": str, "tool": "lookup_number", "args": {"number": str}}',
    '{"thought": str, "tool": "verify_pin", "args": {"pin": str, '
    '"shortcode": str}}',
    '{"thought": str, "tool": "verify_serial", "args": {"manufacturer": str, '
    '"serial": str}}',
    '{"thought": str, "tool": "preview_document", "args": {"kind": '
    '"nafdac_report | fccpc_complaint | seller_demand | adr_note"}}',
    '{"thought": str, "tool": "ask", "args": {"question": str}}',
]
for _line in _EXPECTED_LINES:
    check("planner contract line kept", _line in agent.SYSTEM, _line[:60])
check("envelope rule kept",
      "exactly one raw JSON object" in agent._system_prompt())

print("knowledge retrieval")
_hits = knowledge.retrieve("scratch pin sms 34488")
check("MAS section retrieved",
      any("34488" in h for h in _hits), repr(_hits)[:200])
check("number shapes retrieved",
      any("A4-0271" in h for h in
          knowledge.retrieve("nafdac number dash series")), "see retrieve")
check("unrelated query retrieves nothing",
      knowledge.retrieve("xyzzy plugh") == [])
check("retrieval capped",
      len(knowledge.retrieve("pack number pin batch", limit=1)) <= 1)
check("core knowledge untouched",
      "Vouch" in knowledge.KNOWLEDGE
      and 500 < len(knowledge.KNOWLEDGE) < 4000,
      str(len(knowledge.KNOWLEDGE)))

print("playbooks")
_m = playbooks.match("my scratch pin will not verify")
check("scratch playbook matched",
      bool(_m) and _m[0][0] == "scratch-pin", repr(_m)[:120])
_m2 = playbooks.match("how do I report this seller?")
check("complaint playbook matched",
      bool(_m2) and _m2[0][0] == "file-complaint", repr(_m2)[:120])
check("no match on small talk", playbooks.match("hello there") == [])
check("briefing empty without match",
      playbooks.briefing("hello there") == "")
_b = playbooks.briefing(
    "pin batch report verify complain nafdac fccpc scratch")
check("briefing capped",
      len(_b) <= playbooks.MAX_BRIEF, str(len(_b)))
check("briefing repeats the honesty rule",
      "never declare" in playbooks.briefing(
          "is this pin real").lower())
check("planner sees the playbook",
      "scratch" in agent._system_prompt("my scratch pin").lower())
check("empty prompt keeps rules + facts",
      "never declare" in agent._system_prompt().lower()
      and "34488" in agent._system_prompt()
      and "A11-0551" in agent._system_prompt())

print("memory")
recall.init()
_cid = cases.create_case(_pack(), _assess())["id"]
_r1 = agent.chat("my barcode is 6034000005424", [], case_id=_cid)
_r2 = agent.chat("and the batch is BB 290927AJ57", [], case_id=_cid)
check("barcode remembered across sessions",
      "6034000005424" in _r2["answer"], _r2["answer"][:250])
check("memory feeds the assessment",
      (_r2["assessment"].get("input", {}) or {}).get("barcode")
      == "6034000005424",
      repr(_r2["assessment"].get("input"))[:200])
check("turns stored",
      len(recall.context(_cid)) >= 4,
      str(len(recall.context(_cid))))
_cid2 = cases.create_case(_pack(), _assess())["id"]
_r3 = agent.chat("and the batch is BB 290927AJ57", [], case_id=_cid2)
check("memory is per-case",
      "6034000005424" not in _r3["answer"], _r3["answer"][:200])
_r4 = agent.chat("hello", [], case_id="VC-NOPE")
check("unknown case chats fine", bool(_r4["answer"]))
check("unknown case stores nothing",
      recall.context("VC-NOPE") == [])
recall.remember(_cid, "user", "x" * 5000)
check("long turns truncated",
      all(len(t["text"]) <= recall.MAX_TEXT
          for t in recall.context(_cid)))
for _i in range(60):
    recall.remember(_cid, "user", f"note {_i}")
check("memory pruned to cap",
      len(recall.context(_cid)) <= recall.MAX_TURNS,
      str(len(recall.context(_cid))))
recall.forget(_cid)
check("forget clears", recall.context(_cid) == [])

print("product profile")
_PROFILE_ROW = {"product_id": 6647, "NAFDAC": "A11-0551",
                "product_name": "AC-Drex Tablet**",
                "applicant_name": "A.C. Drugs Ltd",
                "status": "Active", "approval_date": "2023-12-21",
                "expiry_date": "2028-12-20",
                "composition": "Each tablet contains: Paracetamol 500 mg",
                "pack_size": "10 x 10&#039;s (in blisters)",
                "form_name": "Tablet", "marketing_category_id": "OTC",
                "category_name": "Drugs", "atc": "N02BE51",
                "route_name": "Oral", "strength": "500 mg; 30 mg",
                "ingredient_name": "Paracetamol; Caffeine",
                "smpc": "https://example.test/smpc/6647"}
_prof_con = R._mirror_db()
R._mirror_upsert(_prof_con, _PROFILE_ROW, 1)
_prof_con.commit()
_prof_con.close()
R._sync_put(status="complete", finished_at=time.time(), total=1,
            fetched=1, pass_no=1, error="")
_rec = R.Record.from_api(_PROFILE_ROW)
check("record carries the full entry",
      (_rec.atc, _rec.route, _rec.strength, _rec.ingredients)
      == ("N02BE51", "Oral", "500 mg; 30 mg",
          "Paracetamol; Caffeine")
      and _rec.product_name == "AC-Drex Tablet"
      and _rec.smpc == "https://example.test/smpc/6647",
      repr((_rec.atc, _rec.route, _rec.product_name))[:160])
_prof = agent.chat("NAFDAC A11-0551", [])
check("number-only query profiles the product",
      "On the register:" in _prof["answer"]
      and "AC-Drex Tablet (A11-0551)" in _prof["answer"]
      and "A.C. Drugs Ltd" in _prof["answer"]
      and "Status Active" in _prof["answer"],
      _prof["answer"][:400])
check("profile carries form, route, strength",
      "Tablet" in _prof["answer"] and "Oral" in _prof["answer"]
      and "500 mg; 30 mg" in _prof["answer"],
      _prof["answer"][:400])
check("profile carries ingredients, dates, ATC",
      "Paracetamol; Caffeine" in _prof["answer"]
      and "2023-12-21" in _prof["answer"]
      and "2028-12-20" in _prof["answer"]
      and "N02BE51" in _prof["answer"]
      and "10 x 10's" in _prof["answer"],
      _prof["answer"][:500])
_bare = agent.chat("A11-0551", [])
check("bare number profiles too",
      "On the register:" in _bare["answer"]
      and "AC-Drex Tablet" in _bare["answer"],
      _bare["answer"][:300])
_nomatch = agent.chat("NAFDAC A99-9999", [])
check("unknown number stays honest, no profile",
      "On the register:" not in _nomatch["answer"],
      _nomatch["answer"][:200])

print("unknown number guidance")
_bare = agent.chat("01-3892", [])
check("bare unknown keeps its finding",
      _bare["assessment"].get("band") == "high_risk",
      repr(_bare["assessment"].get("band")))
check("bare unknown points at NAPAMS",
      "NAPAMS" in _bare["answer"]
      and "registration.nafdac.gov.ng" in _bare["answer"],
      _bare["answer"][-300:])
_water = agent.chat("CWAY water 01-3892", [])
check("water unknown is review, not a finding",
      _water["assessment"].get("band") == "review",
      repr(_water["assessment"].get("band")))
check("water unknown points at NAFDAC's food register",
      "NAPAMS" in _water["answer"], _water["answer"][-300:])
_med = agent.chat("NAFDAC A99-9999, the product is MALARIA tablets", [])
check("medicine with context keeps a clean finding",
      "NAPAMS" not in _med["answer"]
      and _med["assessment"].get("band") == "high_risk",
      _med["answer"][-300:])
_hits = knowledge.retrieve("food water cosmetics register")
check("planner knows the two registers",
      any("NAPAMS" in h for h in _hits), repr(_hits)[:200])
_gs1 = agent.chat("(01)06260001146105(17)281231(10)LOT42", [])
check("chat decodes a pasted pack code",
      "Decoded pack code" in _gs1["answer"]
      and "06260001146105" in _gs1["answer"]
      and "LOT42" in _gs1["answer"], _gs1["answer"][:300])

print("reaction routing")
_rr = agent.chat("the syrup gave my son a rash", [])
check("reaction routes to ADR channels",
      "Med Safety App" in _rr["answer"]
      and "pharmacovigilance@nafdac.gov.ng" in _rr["answer"]
      and "0800-1-NAFDAC" in _rr["answer"], _rr["answer"][:300])
check("no medical advice given",
      "can't give medical advice" in _rr["answer"],
      _rr["answer"][:200])
_ok = agent.chat("hello", [])
check("small talk unaffected",
      "Med Safety App" not in _ok["answer"])

print("server wiring")
import server as _server
from starlette.requests import Request as _Request

_scope = {"type": "http", "method": "POST", "path": "/api/agent/chat",
          "query_string": b"", "headers": []}
_cid3 = cases.create_case(_pack(), _assess())["id"]
_s1 = _server.agent_chat(
    _server.AgentBody(message="my barcode is 6034000005424", history=[],
                      case_id=_cid3), _Request(_scope))
_s2 = _server.agent_chat(
    _server.AgentBody(message="and the batch is BB 290927AJ57", history=[],
                      case_id=_cid3), _Request(_scope))
check("chat endpoint recalls the case",
      "6034000005424" in _s2["answer"], _s2["answer"][:200])
_cid4 = cases.create_case(_pack(), _assess(), username="ada")["id"]
_s3 = _server.agent_chat(
    _server.AgentBody(message="my barcode is 6034000005424", history=[],
                      case_id=_cid4), _Request(_scope))
_s4 = _server.agent_chat(
    _server.AgentBody(message="and the batch is BB 290927AJ57", history=[],
                      case_id=_cid4), _Request(_scope))
check("anonymous caller gets no owned-case memory",
      "6034000005424" not in _s4["answer"], _s4["answer"][:200])
_s5 = _server.agent_chat(
    _server.AgentBody(message="hello", history=[]), _Request(_scope))
check("chat without a case is unchanged",
      bool(_s5["answer"]) and _s5.get("image_received") is False)
napams.CACHE_FILE = _napams_real_cache
os.unlink(_napams_tmp.name)

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
