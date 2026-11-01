"""
Agent tests: keyless pipeline, proposals, confirmation execution, and the
planner loop against a stubbed model. No key, no network, no OCR.

Run:  python test_agent.py
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
import time

TMP = tempfile.mkdtemp(prefix="vouch-agent-")
os.environ["VOUCH_DATA_DIR"] = TMP
os.environ["VOUCH_AUTH_DB"] = os.path.join(TMP, "auth.sqlite")
os.environ["VOUCH_AUDIT_LOG"] = os.path.join(TMP, "audit.log")
os.environ["VOUCH_CACHE_DIR"] = os.path.join(TMP, "cache")

import agent
import ai
import registry as R

fails = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        fails += 1


con = R._mirror_db()
R._mirror_upsert(con, {"product_id": 101, "NAFDAC": "A11-0551",
                       "product_name": "AC-DREX Tablet",
                       "applicant_name": "A.C. Drugs Ltd",
                       "status": "Active"}, 1)
con.commit()
con.close()
R._sync_put(status="complete", finished_at=time.time(), total=1,
            fetched=1, pass_no=1, error="")

_old_key = agent.API_KEY
agent.API_KEY = ""  # force the deterministic pipeline in every shell

print("keyless pipeline")
out = agent.chat("hello, what can you check?")
check("plain question answered",
      len(out["answer"]) > 20 and out["proposal"] is None
      and out["provider"] == "vouch-composer", str(out)[:200])
out = agent.chat("NAFDAC A99-9999")
check("unknown number assessed, no nagging proposal",
      out["assessment"]["band"] == "high_risk"
      and out["proposal"] is None, str(out["assessment"].get("band")))
out = agent.chat("NAFDAC A99-9999, please open a case")
check("asked-for case proposed",
      (out["proposal"] or {}).get("action") == "open_case"
      and out["proposal"]["pack"]["nafdac_number"] == "A99-9999",
      str(out["proposal"])[:200])
out = agent.chat("", image_bytes=b"not-an-image")
check("unreadable photo fails honestly",
      "Paste the label text" in out["answer"]
      or "no text was found" in out["answer"],
      out["answer"][:150])
check("planner off without a key",
      agent.capabilities() == {"planner": False,
                               "model": "vouch-composer"},
      str(agent.capabilities()))

print("confirmation execution")
done = agent.execute_confirmed(
    {"action": "open_case", "pack": {"nafdac_number": "A99-9999"}},
    "tester")
check("case opens with fresh verdict",
      done["ok"] is True and done["case_id"].startswith("VC-")
      and done["band"] == "high_risk", str(done))
try:
    agent.execute_confirmed({"action": "nuke"}, "tester")
    check("unknown action refused", False)
except ValueError:
    check("unknown action refused", True)

print("image decoding")
raw = base64.b64encode(b"fake-bytes").decode()
check("roundtrip", agent.decode_image(raw) == b"fake-bytes")
check("data URL accepted",
      agent.decode_image("data:image/png;base64," + raw) == b"fake-bytes")
for bad in ("!!!", "x" * 12_000_001):
    try:
        agent.decode_image(bad)
        check(f"rejected {bad[:8]!r}", False)
    except ValueError:
        check(f"rejected {bad[:8]!r}", True)

print("history hygiene")
hist = [{"role": "user", "text": "hi"}] * 12 + [{"role": "sys", "text": "x"},
                                                "junk", {"role": "user"}]
clean = agent._clean_history(hist)
check("capped and filtered",
      len(clean) == 10 and all(h["role"] == "user" for h in clean),
      str(len(clean)))

print("planner loop (stubbed model)")
agent.API_KEY = "test-key"
real_ai_key = ai.API_KEY
ai.API_KEY = ""
real_chat = agent._chat
try:
    script = [
        {"thought": "t", "tool": "lookup_number",
         "args": {"number": "A11-0551"}},
        {"answer": "Registered to A.C. Drugs Ltd."},
    ]
    agent._chat = lambda messages: json.dumps(script.pop(0))
    out = agent.chat("is A11-0551 registered?")
    check("tool then answer",
          out["answer"] == "Registered to A.C. Drugs Ltd."
          and out["steps"] == 2 and out["provider"] == "meta:" + agent.MODEL,
          str(out)[:200])

    agent._chat = lambda messages: json.dumps(
        {"proposal": {"action": "open_case",
                      "pack": {"nafdac_number": "A99-9999"}}})
    out = agent.chat("open a case")
    check("proposal recomputed server-side",
          (out["proposal"] or {}).get("title", "").startswith("Open a case")
          and out["proposal"]["pack"]["nafdac_number"] == "A99-9999",
          str(out["proposal"])[:200])

    agent._chat = lambda messages: "not json{{{"
    out = agent.chat("hello")
    check("bad model JSON falls back",
          len(out["answer"]) > 20 and out["steps"] == 1,
          str(out)[:150])

    agent._chat = lambda messages: json.dumps({"tool": "nuke"})
    out = agent.chat("hello")
    check("unknown tool falls back", len(out["answer"]) > 20)

    agent._chat = lambda messages: json.dumps(
        {"thought": "t", "tool": "lookup_number", "args": {"number": "x"}})
    out = agent.chat("hello")
    check("loop stops at max steps", out["steps"] == agent.MAX_STEPS,
          str(out["steps"]))

    print("planner photo proposals (stubbed model + extractor)")
    real_extract = agent.vision.extract_from_image
    agent.vision.extract_from_image = (
        lambda d, t="image/jpeg": agent.vision.Extraction(
            nafdac_number="A99-9999"))
    try:
        agent._chat = lambda messages: json.dumps(
            {"answer": "This pack needs a case."})
        out = agent.chat("check this photo", image_bytes=b"x")
        check("photo high-risk proposes",
              (out["proposal"] or {}).get("action") == "open_case",
              str(out.get("proposal"))[:200])

        def _dead(messages):
            raise RuntimeError("model down")

        agent._chat = _dead
        out = agent.chat("hello")
        check("model failure labels the fallback",
              out["provider"] == "vouch-composer"
              and len(out["answer"]) > 20 and out["steps"] == 1,
              out["provider"])
        out = agent.chat("NAFDAC A99-9999")
        check("fallen-back planner still mines the number",
              out["assessment"]["band"] == "high_risk"
              and out["proposal"] is None,
              out["assessment"].get("band"))
    finally:
        agent.vision.extract_from_image = real_extract
finally:
    agent._chat = real_chat
    agent.API_KEY = _old_key
    ai.API_KEY = real_ai_key

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
