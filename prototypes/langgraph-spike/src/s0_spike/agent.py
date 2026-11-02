"""Scripted Investigator stand-in plus the deterministic checks that classify its output.

The agent only produces a claim and quotes. The outcome is assigned by classify(), in code.
"""
import os
import re
import time

from .models import RETRIABLE_REASONS  # noqa: F401  (re-exported for readers)

CLAIMS = ("drift", "no_drift", "unknown")


class BudgetExhausted(Exception):
    pass


class MalformedOutput(Exception):
    pass


class Budget:
    def __init__(self, cap):
        self.cap, self.spent = cap, 0

    def spend(self, units):
        if self.spent + units > self.cap:
            raise BudgetExhausted(f"{self.spent + units} > {self.cap}")
        self.spent += units


def snapshot_key(repo, sha, path):
    return f"{repo}@{sha}:{path}"


def run_scripted_agent(response, candidate, ctx, attempt, budget):
    """Executes one scripted response. `ctx` carries run_id and the ledger (for one-shot crash markers)."""
    if response.get("delay"):
        time.sleep(response["delay"])
    if response.get("crash_once") and ctx["ledger"].mark_once(ctx["run_id"], f"crash:{candidate['id']}"):
        os._exit(137)  # simulates SIGKILL / OOM: no exception, no cleanup
    for units in response.get("spend", [1]):
        budget.spend(units)
    if "raise" in response:
        raise RuntimeError(response["raise"])
    return response["raw"] if "raw" in response else response["output"]


def validate_output(raw, candidate):
    """Structural validation. Unknown keys (including a self-declared `outcome`) are ignored, not trusted."""
    if not isinstance(raw, dict):
        raise MalformedOutput("output is not an object")
    if raw.get("candidate_id") != candidate["id"]:
        raise MalformedOutput("candidate_id missing or different")
    if raw.get("claim") not in CLAIMS:
        raise MalformedOutput("claim missing or unknown")
    ev = raw.get("evidence")
    if not isinstance(ev, list):
        raise MalformedOutput("evidence is not a list")
    out = []
    for e in ev:
        if not isinstance(e, dict) or any(not isinstance(e.get(k), str) or not e[k] for k in ("repo", "sha", "path", "quote")):
            raise MalformedOutput("evidence item malformed")
        out.append({k: e[k] for k in ("repo", "sha", "path", "quote")})
    return raw["claim"], out


def fact_check(candidate, snapshots, pins):
    """Deterministic comparison of the values at each site. None when the candidate has no mechanical check."""
    sites = candidate["sites"]
    if not sites or any(not s.get("pattern") for s in sites):
        return None
    values = []
    for s in sites:
        text = snapshots.get(snapshot_key(s["repo"], pins[s["repo"]], s["path"]))
        m = re.search(s["pattern"], text or "")
        if not m:
            return None
        values.append(m.group(1))
    return "drift" if len(set(values)) > 1 else "no_drift"


def classify(candidate, claim, evidence, snapshots, pins):
    """(outcome, reason, verified_evidence). `verified` needs verified quotes on every site AND a code-run check."""
    if claim == "unknown" or not evidence:
        return "insufficient_evidence", "missing_evidence", []
    good = [e for e in evidence if e["sha"] == pins.get(e["repo"]) and e["quote"] in snapshots.get(snapshot_key(e["repo"], e["sha"], e["path"]), "")]
    if len(good) != len(evidence):
        return "insufficient_evidence", "unverified_quote", good
    covered = {(e["repo"], e["path"]) for e in good}
    if any((s["repo"], s["path"]) not in covered for s in candidate["sites"]):
        return "insufficient_evidence", "missing_evidence", good
    check = fact_check(candidate, snapshots, pins)
    if check is None:
        return "supported", "no_mechanical_check", good
    if check != claim:
        return "insufficient_evidence", "claim_contradicted_by_check", good
    return "verified", "claim_confirmed_by_check", good
