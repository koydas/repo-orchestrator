"""The LangGraph workflow. Nodes call plain functions (agent.py, models.py); no verdict comes from the agent."""
import json
import os
import re
import uuid
from dataclasses import asdict
from datetime import datetime, timezone

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt

from .agent import (Budget, BudgetExhausted, MalformedOutput, classify, run_scripted_agent, validate_output)
from .errors import FrozenInputTampered, InvalidRunRequest, RunAlreadyInitialized
from .limits import DEFAULT_LIMITS, STEP_RESERVE
from .models import (RETRIABLE_REASONS, RunInput, RunState, digest, latest_results, normalize_report, result_key)
from .scenarios import SCENARIOS

RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _result(cid, attempt, outcome, reason, claim="", evidence=(), units=0, asserted=None):
    return {"candidate_id": cid, "attempt": attempt, "outcome": outcome, "reason": reason, "claim": claim,
            "evidence": list(evidence), "units_spent": units, "agent_asserted_outcome": asserted}


def validate_decision(answer, report_digest):
    """None when valid. An approval is bound to the digest of the report the reviewer saw."""
    if not isinstance(answer, dict):
        return "decision must be an object"
    if answer.get("decision") not in ("approve", "reject"):
        return "decision must be 'approve' or 'reject'"
    if not isinstance(answer.get("reviewer"), str) or not answer["reviewer"]:
        return "reviewer required"
    if answer.get("report_digest") != report_digest:
        return "report_digest does not match the pending report"
    return None


def pending_work(state, limits):
    """[(candidate_id, attempt)] still to run: never attempted, or retriable and under the attempt cap and not denied."""
    latest, denials, out = latest_results(state.get("results", {})), state.get("denials", {}), []
    for c in state["candidates"]:
        r = latest.get(c["id"])
        if r is None:
            out.append((c["id"], 1))
        elif (r["outcome"] == "failed" and r["reason"] in RETRIABLE_REASONS
              and r["attempt"] < limits.max_attempts and c["id"] not in denials):
            out.append((c["id"], r["attempt"] + 1))
    return sorted(out)


def check_frozen(state):
    if digest({"candidates": state["candidates"], "candidate_ids": state["candidate_ids"]}) != state["input_digest"]:
        raise FrozenInputTampered("candidates differ from the frozen input digest")


def build_graph(checkpointer, ledger, limits=DEFAULT_LIMITS, **compile_kwargs):
    def init_run(state):
        if state.get("status") or state.get("meta"):  # thread already has a run: a start input must not restart it
            raise RunAlreadyInitialized(f"thread {state.get('run_id')} already holds a run in status {state.get('status')}")
        if not RUN_ID_RE.match(str(state.get("run_id", ""))):
            raise InvalidRunRequest("run_id missing or invalid")
        if state.get("scenario") not in SCENARIOS:
            raise InvalidRunRequest("unknown scenario")
        meta = {"nonce": uuid.uuid4().hex, "started_at": datetime.now(timezone.utc).isoformat()}
        return {"status": "running", "meta": meta, "spent": 0, "rounds": 0, "steps": 1}

    def freeze_input(state):
        sc = SCENARIOS[state["scenario"]]()
        cands = sorted(sc.candidates, key=lambda c: c["id"])
        ids = [c["id"] for c in cands]
        if len(set(ids)) != len(ids) or not ids:
            raise InvalidRunRequest("candidate ids must be unique and non-empty")
        accepted, deferred = cands[:limits.max_candidates], cands[limits.max_candidates:]
        out = {"candidates": accepted, "candidate_ids": ids, "steps": 1,
               "results": {result_key(c["id"], 0): _result(c["id"], 0, "insufficient_evidence", "candidate_cap") for c in deferred}}
        out["input_digest"] = digest({"candidates": accepted, "candidate_ids": ids})
        return out

    def dispatch(state):
        check_frozen(state)
        batch, results, denials = [], {}, {}
        spent, steps = state["spent"], state["steps"]
        for cid, attempt in pending_work(state, limits):
            reason = None
            if spent + len(batch) >= limits.max_investigations:
                reason = "investigation_cap"
            elif steps + len(batch) + 1 > limits.max_steps - STEP_RESERVE:
                reason = "step_cap"
            if reason:
                denials[cid] = {"reason": reason, "attempt": attempt}
                if attempt == 1:  # never investigated: say so explicitly instead of dropping it
                    results[result_key(cid, 0)] = _result(cid, 0, "insufficient_evidence", reason)
                continue
            batch.append({"run_id": state["run_id"], "scenario": state["scenario"], "attempt": attempt,
                          "candidate": next(c for c in state["candidates"] if c["id"] == cid)})
        return {"batch": batch, "spent": spent + len(batch), "rounds": state["rounds"] + bool(batch),
                "results": results, "denials": denials, "steps": 1}

    def fan_out(state):
        return [Send("investigate", item) for item in state["batch"]] or "aggregate"

    def investigate(item):
        cand, attempt, run_id = item["candidate"], item["attempt"], item["run_id"]
        cid = cand["id"]
        key = result_key(cid, attempt)
        # Second budget check, authoritative: the ledger lives outside the checkpoint, so state edits cannot reset it.
        if ledger.reserve_spend(run_id, cid, attempt, limits.max_investigations) == "denied":
            return {"results": {key: _result(cid, attempt, "insufficient_evidence", "investigation_cap")}, "steps": 1}
        ledger.log(run_id, "agent", key)
        sc = SCENARIOS[item["scenario"]]()
        budget = Budget(limits.max_units_per_investigation)
        asserted = None
        try:
            raw = run_scripted_agent(sc.response_for(cid, attempt), cand, {"run_id": run_id, "ledger": ledger}, attempt, budget)
            asserted = raw.get("outcome") if isinstance(raw, dict) else None
            claim, evidence = validate_output(raw, cand)
            outcome, reason, good = classify(cand, claim, evidence, sc.snapshots, sc.pins)
            res = _result(cid, attempt, outcome, reason, claim, good, budget.spent, asserted)
        except BudgetExhausted:
            res = _result(cid, attempt, "insufficient_evidence", "budget_exhausted", units=budget.spent)
        except MalformedOutput as e:
            res = _result(cid, attempt, "failed", "malformed_output", units=budget.spent)
            res["detail"] = str(e)
        except Exception as e:  # isolation is ours: a raising parallel task would abort the whole superstep
            res = _result(cid, attempt, "failed", "agent_exception", units=budget.spent)
            res["detail"] = type(e).__name__
        ledger.log(run_id, "done", key)  # completion order, observable by tests
        return {"results": {key: res}, "steps": 1}

    def collect(state):
        check_frozen(state)
        errors, results = [], {}
        for item in state["batch"]:
            k = result_key(item["candidate"]["id"], item["attempt"])
            if k not in state["results"]:
                errors.append(f"missing result {k}")
                results[k] = _result(item["candidate"]["id"], item["attempt"], "failed", "no_result")
        return {"results": results, "integrity_errors": errors, "steps": 1}

    def after_collect(state):
        return "dispatch" if pending_work(state, limits) and state["steps"] < limits.max_steps else "aggregate"

    def aggregate(state):
        check_frozen(state)
        latest, errors, findings = latest_results(state["results"]), [], []
        for cid in state["candidate_ids"]:
            r = latest.get(cid)
            if r is None:
                errors.append(f"no result for {cid}")
                r = _result(cid, 0, "failed", "no_result")
            findings.append({k: r[k] for k in ("candidate_id", "attempt", "outcome", "reason", "claim", "evidence")})
        counts = {o: sum(f["outcome"] == o for f in findings) for o in ("verified", "supported", "insufficient_evidence", "failed")}
        report = {"run_id": state["run_id"], "input_digest": state["input_digest"], "findings": findings, "counts": counts,
                  "denials": state.get("denials", {}), "integrity_errors": state.get("integrity_errors", []) + errors,
                  "usage": {"investigations_spent": state["spent"], "rounds": state["rounds"], "limits": asdict(limits)}}
        report["digest"] = digest(normalize_report(report))
        return {"report": report, "status": "awaiting_approval", "steps": 1}

    def approval_gate(state):
        # LangGraph re-runs this node from its first line on resume: nothing above interrupt() may have a side effect.
        payload = {"run_id": state["run_id"], "report_digest": state["report"]["digest"], "counts": state["report"]["counts"]}
        while True:
            answer = interrupt(payload)
            err = validate_decision(answer, state["report"]["digest"])
            if err is None:
                return {"decision": {k: answer.get(k) for k in ("decision", "reviewer", "note", "report_digest")}, "steps": 1}
            payload = {**payload, "error": err}  # an invalid answer never approves: pause again

    def route_decision(state):
        return "finalize_approved" if state["decision"]["decision"] == "approve" else "finalize_rejected"

    def _finalize(state, decision):
        d = state["decision"]
        payload = json.dumps(state["report"], sort_keys=True) if decision == "approved" else None
        ledger.log(state["run_id"], "publish_attempt", decision)
        if ledger.publish(state["run_id"], decision, d["report_digest"], d["reviewer"], d.get("note"), payload):
            ledger.log(state["run_id"], "publish_performed", decision)
        if decision == "approved" and SCENARIOS[state["scenario"]]().faults.get("crash_after_publish") \
                and ledger.mark_once(state["run_id"], "crash_after_publish"):
            os._exit(137)  # effect committed, checkpoint not yet written: the replay must not publish twice
        return {"status": decision, "outcome": {"decision": decision, "report_digest": d["report_digest"]}, "steps": 1}

    g = StateGraph(RunState, input_schema=RunInput)
    for name, fn in (("init_run", init_run), ("freeze_input", freeze_input), ("dispatch", dispatch),
                     ("investigate", investigate), ("collect", collect), ("aggregate", aggregate),
                     ("approval_gate", approval_gate),
                     ("finalize_approved", lambda s: _finalize(s, "approved")),
                     ("finalize_rejected", lambda s: _finalize(s, "rejected"))):
        g.add_node(name, fn)
    g.add_edge(START, "init_run")
    g.add_edge("init_run", "freeze_input")
    g.add_edge("freeze_input", "dispatch")
    g.add_conditional_edges("dispatch", fan_out, ["investigate", "aggregate"])
    g.add_edge("investigate", "collect")
    g.add_conditional_edges("collect", after_collect, ["dispatch", "aggregate"])
    g.add_edge("aggregate", "approval_gate")
    g.add_conditional_edges("approval_gate", route_decision, ["finalize_approved", "finalize_rejected"])
    g.add_edge("finalize_approved", END)
    g.add_edge("finalize_rejected", END)
    return g.compile(checkpointer=checkpointer, **compile_kwargs)
