"""One-time macro-and-news market-context briefing (offline-tested workflow).

FRED and Alpaca News -> evidence adapters -> Macro and News analysts ->
agent evidence adapters -> temporary Evidence Store -> briefing bundle ->
assistant and dashboard. No SPY price evidence, no Market Evidence Agent, no
setup qualification, contract selection, prediction or trading.

**Implementation does not authorize execution.** A live run (provider or
model requests) needs a recorded authorization that does not exist; tests use
injected fakes only.
"""
