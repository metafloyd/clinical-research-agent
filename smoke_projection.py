"""Offline guard for the enrollment-PROJECTION chart feature (no API, deterministic).

Locks the forecast MATH (_project_enrollment) and the figure OVERLAY behavior so a later
prompt/chart edit can't silently break them. The model-driven half (a forecast phrasing
sets projection:true, a plain trend does not) is covered live by smoke_agent / stress_routing
+ a manual e2e check; this file pins the parts that must never regress without an API call.

Run: .venv\\Scripts\\python.exe smoke_projection.py
"""
import trial_ops as t

_MONTHS = [f"2025-{m:02d}-01" for m in range(1, 13)] + [f"2026-{m:02d}-01" for m in range(1, 6)]
_COLS = ["as_of_date", "enrolled", "target", "planned_end_date"]


def _rows(enr):
    return [(d, e, 75, "2027-03-31") for d, e in zip(_MONTHS, enr)]


def _cd(rows):
    return {c: [r[i] for r in rows] for i, c in enumerate(_COLS)}


def _spec(projection):
    s = {"chart_type": "line", "x": "as_of_date", "y": "enrolled", "title": "Study"}
    if projection:
        s["projection"] = True
    return s


def main():
    passed = failed = 0

    def check(name, cond):
        nonlocal passed, failed
        print(f"[{'PASS' if cond else 'FAIL'}] {name}")
        passed += bool(cond); failed += (not cond)

    # 1) Behind pace: a stalling ramp projects short of target, verdict red.
    behind = _rows([10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 31, 32, 33, 33, 34, 34])
    p = t._project_enrollment(_cd(behind), "as_of_date", "enrolled")
    check("behind: forecast computed", p is not None)
    check("behind: projects short of target", p and p["proj_at_end"] < p["target"])
    check("behind: meets_target False", p and p["meets_target"] is False)

    # 2) On pace: a fast ramp projects past target.
    ahead = _rows([10, 18, 26, 34, 42, 50, 58, 66, 72, 78, 82, 86, 88, 90, 92, 93, 94])
    p2 = t._project_enrollment(_cd(ahead), "as_of_date", "enrolled")
    check("ahead: meets_target True", p2 and p2["meets_target"] is True)

    # 3) Horizon is bounded by the planned end month (not runaway).
    check("horizon ends at planned end", p and p["proj_x"][-1] == "2027-03-01")

    # 4) Overlay present WITH the flag (Projected trace + target line + planned-end line).
    fig = t._build_figure(_spec(True), _COLS, behind)
    names = [tr.name for tr in fig.data]
    check("flag: Projected trace drawn", "Projected" in names)
    check("flag: two reference lines (target + planned end)",
          len([s for s in fig.layout.shapes if s.type == "line"]) == 2)
    check("flag: red target line when behind",
          any(s.line.color == t._RAG_RED for s in fig.layout.shapes))
    check("flag: verdict subline in title", "velocity" in (fig.layout.title.text or ""))

    # 5) NO overlay without the flag (ordinary trend is untouched).
    fig2 = t._build_figure(_spec(False), _COLS, behind)
    check("no-flag: single trace, no shapes",
          [tr.name for tr in fig2.data] == ["Enrolled"] and len(fig2.layout.shapes) == 0)

    # 6) Too few points -> no forecast (needs >=3).
    short = [(d, e, 75, "2027-03-31") for d, e in zip(_MONTHS[:2], [10, 12])]
    check("guard: <3 points returns None",
          t._project_enrollment(_cd(short), "as_of_date", "enrolled") is None)

    print(f"\n{passed}/{passed + failed} passed.")
    return failed == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)
