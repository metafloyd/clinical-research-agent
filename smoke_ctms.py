"""
Fast, standalone regression smoke test for the internal trial-operations (NL→SQL) tool.

Runs a battery of SIMPLE natural-language questions a stakeholder would actually ask, and
checks each one behaves: schema-answerable questions return DATA; out-of-schema questions
DECLINE (never fabricate). It hits the tool layer directly (no MCP, no Chainlit) so it's
quick and cheap — run it after ANY change to trial_ops.py / the schema / the prompts.

Run:  .venv\\Scripts\\python.exe smoke_ctms.py
Exit code is non-zero if anything regresses (so it can gate a deploy).
"""

import asyncio
import sys

import trial_ops as t
from config import TRIAL_OPS_DB_PATH

# (question, expected) — expected is "data" (must return rows/a number) or
# "decline" (must hit the not-in-our-database message; out-of-schema).
CASES = [
    # counts
    ("how many studies do we have", "data"),
    ("how many active studies", "data"),
    ("how many are recruiting", "data"),
    ("how many completed studies", "data"),
    ("how many oncology trials do we have", "data"),
    ("how many phase 3 studies", "data"),
    ("how many sites do we have", "data"),
    ("how many investigator-initiated studies", "data"),
    # lookups (study↔PI, attributes of a named trial)
    ("who is the PI of the donanemab trial", "data"),
    ("who runs our neurology studies", "data"),
    ("what phase is the lecanemab trial", "data"),
    ("what is the status of the tirzepatide trial", "data"),
    ("when did the DAPA-HF trial start", "data"),
    ("what is the NCT number for our melanoma trial", "data"),
    ("what is Dr. Raj Patel researching", "data"),
    # rankings / superlatives
    ("which study has the best enrollment", "data"),
    ("which study is furthest behind", "data"),
    ("which site has enrolled the most patients", "data"),
    ("which PI runs the most trials", "data"),
    ("what is our biggest trial by target", "data"),
    ("what is our smallest trial", "data"),
    # filters / lists
    ("list our oncology trials", "data"),
    ("show our recruiting studies", "data"),
    ("which trials are industry sponsored", "data"),
    ("what studies started in 2024", "data"),
    ("list our completed cardiology trials", "data"),
    # sites
    ("enrollment by site for the donanemab trial", "data"),
    ("how many patients enrolled at Rochester", "data"),
    ("what trials run at the Arizona site", "data"),
    ("what is happening at our Florida site", "data"),
    ("which site is doing best", "data"),
    # aggregates
    ("what is our total enrollment", "data"),
    ("what is the average fill rate across studies", "data"),
    # out-of-schema → MUST decline (no fabrication)
    ("what is our trial budget", "decline"),
    ("what is the dropout rate", "decline"),
    ("who are the patients in the donanemab trial", "decline"),
    ("what adverse events were reported", "decline"),
    ("what is the weather", "decline"),
]

_DECLINE_MARK = "isn't answerable"
_EMPTY_MARK = "No rows matched"
_RETRY_MARK = "Couldn't answer"


async def main() -> int:
    fails = []
    for q, expected in CASES:
        try:
            out = await t._answer(q, TRIAL_OPS_DB_PATH)
            if _DECLINE_MARK in out:
                got = "decline"
            elif _EMPTY_MARK in out or _RETRY_MARK in out:
                got = "empty"
            else:
                got = "data"
        except Exception as e:  # pragma: no cover - surfaces infra errors
            got = f"error:{e}"
        ok = (got == expected)
        if not ok:
            fails.append((q, expected, got))
        print(f"[{'PASS' if ok else 'FAIL'}] expect={expected:7s} got={got:7s} | {q}")

    print(f"\n{len(CASES) - len(fails)}/{len(CASES)} passed.")
    if fails:
        print("\nREGRESSIONS:")
        for q, exp, got in fails:
            print(f"  - '{q}' — expected {exp}, got {got}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
