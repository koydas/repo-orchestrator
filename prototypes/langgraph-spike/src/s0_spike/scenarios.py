"""Synthetic repositories, candidates and scripted agent responses. No real repository is read."""
from dataclasses import dataclass, field

from .agent import snapshot_key


@dataclass
class Scenario:
    name: str
    candidates: list = field(default_factory=list)
    snapshots: dict = field(default_factory=dict)
    pins: dict = field(default_factory=dict)
    responses: dict = field(default_factory=dict)  # candidate id -> [response for attempt 1, 2, ...]; the last repeats
    faults: dict = field(default_factory=dict)  # scenario-level injections, e.g. crash_after_publish

    def response_for(self, cid, attempt):
        rs = self.responses[cid]
        return rs[min(attempt, len(rs)) - 1]


def _sha(repo):
    import hashlib
    return hashlib.sha1(repo.encode()).hexdigest()


def _add(sc, cid, a_text, b_text, pattern=r"(\d+)"):
    """Adds a candidate over two synthetic repositories; returns its honest response builder inputs."""
    sites = []
    for side, text in (("svc", a_text), ("app", b_text)):
        repo = f"acme/{side}-{cid}"
        sc.pins[repo] = _sha(repo)
        sc.snapshots[snapshot_key(repo, sc.pins[repo], "README.md")] = text
        sites.append({"repo": repo, "path": "README.md", "pattern": pattern})
    sc.candidates.append({"id": cid, "title": f"synthetic claim {cid}", "sites": sites})
    return sites, (a_text, b_text)


def _quotes(sc, cid):
    c = next(c for c in sc.candidates if c["id"] == cid)
    return [{"repo": s["repo"], "sha": sc.pins[s["repo"]], "path": s["path"],
             "quote": sc.snapshots[snapshot_key(s["repo"], sc.pins[s["repo"]], s["path"])]} for s in c["sites"]]


def honest(sc, cid, claim="drift", **extra):
    return {"output": {"candidate_id": cid, "claim": claim, "evidence": _quotes(sc, cid)}, **extra}


def _drift(sc, cid, a=20, b=17, **extra):
    _add(sc, cid, f"Holds {a} patterns.", f"Describes {b} patterns.")
    sc.responses[cid] = [honest(sc, cid, **extra)]


def _boom():
    return {"raise": "scripted agent failure"}


def _honest_set(name, n, **faults):
    sc = Scenario(name, faults=dict(faults))
    for i in range(1, n + 1):
        _drift(sc, f"c{i:02d}", 20 + i, 10 + i)
    return sc


def baseline():
    sc = Scenario("baseline")
    _drift(sc, "c01")  # verified: quotes verified and the code-run check confirms the drift
    _add(sc, "c02", "schema_version = 2", "expects schema_version = 1")
    sc.responses["c02"] = [honest(sc, "c02")]  # verified
    _add(sc, "c03", "Retries are bounded.", "Retries are unbounded.", pattern=None)
    sc.responses["c03"] = [honest(sc, "c03")]
    sc.responses["c03"][0]["output"]["outcome"] = "verified"  # self-declared: must be ignored -> supported
    _add(sc, "c04", "Holds 5 patterns.", "Describes 5 patterns.")
    fab = honest(sc, "c04")
    fab["output"]["evidence"][0]["quote"] = "Holds 99 patterns."  # fabricated quote
    fab["output"]["outcome"] = "verified"
    sc.responses["c04"] = [fab]  # insufficient_evidence: unverified_quote
    _add(sc, "c05", "Holds 1 patterns.", "Describes 2 patterns.")
    sc.responses["c05"] = [_boom()]  # failed after max attempts
    _add(sc, "c06", "Holds 3 patterns.", "Describes 4 patterns.")
    sc.responses["c06"] = [_boom(), honest(sc, "c06")]  # transient: verified at attempt 2
    _add(sc, "c07", "Holds 7 patterns.", "Describes 8 patterns.")
    sc.responses["c07"] = [{"output": {"candidate_id": "c07", "claim": "drift", "evidence": []}}]  # missing evidence
    _add(sc, "c08", "Holds 9 patterns.", "Describes 10 patterns.")
    sc.responses["c08"] = [{"raw": {"candidate_id": "c08", "claim": "drift"}}, honest(sc, "c08")]  # malformed, then ok
    _drift(sc, "c09", 11, 12, spend=[3, 3])  # 6 units > 5: budget exhausted
    return sc


def small():
    return _honest_set("small", 3)


def too_many():
    return _honest_set("too_many", 12)


def all_fail():
    sc = _honest_set("all_fail", 10)
    for cid in sc.responses:
        sc.responses[cid] = [_boom()]
    return sc


def _parallel(name, reverse):
    sc = _honest_set(name, 6)
    for i, cid in enumerate(sorted(sc.responses), start=1):
        delay = 0.08 * ((7 - i) if not reverse else i)  # c01 finishes last (or first when reversed)
        sc.responses[cid] = [_boom() | {"delay": delay}] if cid == "c03" else [honest(sc, cid, delay=delay)]
    return sc


def crash_mid_fanout():
    sc = _honest_set("crash_mid_fanout", 5)
    sc.responses["c03"] = [honest(sc, "c03", crash_once=True, delay=0.5)]  # siblings finish while c03 sleeps
    return sc


def crash_in_finalize():
    return _honest_set("crash_in_finalize", 3, crash_after_publish=True)


SCENARIOS = {f.__name__: f for f in (baseline, small, too_many, all_fail, crash_mid_fanout, crash_in_finalize)}
SCENARIOS["parallel"] = lambda: _parallel("parallel", reverse=False)
SCENARIOS["parallel_rev"] = lambda: _parallel("parallel_rev", reverse=True)


def load_scenario(name):
    return SCENARIOS[name]()
