"""Budgets, model and prices: code constants, never inputs, never prompt content (ADR-0004 §5, ADR-0014 §4)."""

from __future__ import annotations

from dataclasses import dataclass

MODEL = "claude-sonnet-5-5"
EFFORT = "medium"  # output_config.effort; fixed in code. No sampling parameters are sent (5.x models reject them).
PROMPT_VERSION = "c5-1"
API_VERSION = "2023-06-01"

# USD per million tokens (input, output). Source: the Claude API model table cached 2026-10-06 (Sonnet 5.5: $2 / $10).
# A list price, not an invoice: the report labels every cost an estimate and the table must be re-verified before use.
PRICES_USD_PER_MTOK: dict[str, tuple[float, float]] = {MODEL: (2.00, 10.00)}
PRICE_TABLE_DATE = "2026-10-06"


@dataclass(frozen=True)
class Budget:
    max_investigations: int = 12  # candidates investigated per run; the rest are insufficient_evidence (cause budget)
    max_tool_calls: int = 8  # per investigation
    max_llm_calls: int = 6  # per investigation, tool turns and retries included
    max_malformed_retries: int = 1
    max_transient_retries: int = 1  # timeout, rate limit, server error, network
    max_input_tokens: int = 24_000  # per investigation, summed over its model calls
    max_output_tokens: int = 4_000  # per investigation
    max_tokens_per_call: int = 2_000  # the max_tokens request parameter
    max_run_tokens: int = 150_000  # input + output, whole run
    max_run_cost_micro_usd: int = 2_000_000  # 2.00 USD
    max_tool_result_chars: int = 6_000
    max_read_lines: int = 80
    max_search_hits: int = 10
    max_list_entries: int = 60
    max_quotes: int = 6
    max_quote_chars: int = 600
    max_quote_lines: int = 20
    min_quote_chars: int = 8
    max_claim_chars: int = 600
    max_justification_chars: int = 400


BUDGET = Budget()


def cost_micro_usd(model: str, input_tokens: int, output_tokens: int) -> int:
    """Estimated cost in millionths of a dollar (integer arithmetic: tokens x USD per million tokens). Unknown model: 0."""
    price = PRICES_USD_PER_MTOK.get(model)
    if price is None:
        return 0
    return round(input_tokens * price[0] + output_tokens * price[1])


def estimate_input_tokens(chars: int) -> int:
    """A deliberately pessimistic character-based estimate (3 characters per token) used only to reserve budget."""
    return -(-chars // 3)
