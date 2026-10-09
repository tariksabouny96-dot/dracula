# Provider and cost policy

1. **No price, no call.** A paid provider/model with no entry in the operator's pricing file
   (`HOOD_MODEL_PRICING`, see `packages/config/pricing.py`) is refused with "unknown cost".
   Hood never guesses prices and never infers free usage from a $0 estimate.
2. **Reserve before sending.** Each call reserves its worst case (prompt estimate + `max_tokens`)
   against the daily, monthly and per-task caps (`BudgetSettings`) and, for agent missions, against
   the mission budget in the durable spend ledger. Concurrent calls cannot jointly exceed a cap.
3. **Settle honestly.** Measured usage is charged at the configured price. If the provider omits
   usage, or the request fails after it may have been processed, the full reservation is charged.
   Unsettled reservations found after a crash are charged on recovery.
4. **No silent substitution.** Simulated (mock) output is refused by the live engine and by the
   router unless the request explicitly allows it; outputs are labelled SIMULATED / LIVE / MIXED.
5. **Opt-in per provider.** OpenAI calls additionally need `HOOD_ALLOW_OPENAI_API_CALLS=1`;
   local models must be on loopback. Subscriptions to chat products do not grant API credits.
6. **Live tests need written approval:** key, pricing file, spend cap, and `HOOD_RUN_LIVE_PROVIDER=1`.

Configuration: `HOOD_GEMINI_{FAST,STANDARD,DEEP}_MODEL`, `HOOD_OPENAI_{FAST,STANDARD,DEEP}_MODEL`,
`GEMINI_API_KEY` / vault `SECRET://gemini/api_key`, `OPENAI_API_KEY` / vault `SECRET://openai/api_key`.
