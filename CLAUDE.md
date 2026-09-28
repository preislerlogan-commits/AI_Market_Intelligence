# Claude Code Instructions — AI Market Intelligence

These instructions govern how Claude Code should operate in this repository.
They apply to all work in this project, in addition to any general Claude
Code guidance.

## Before Starting Work

- **Read [PROJECT_STATE.md](PROJECT_STATE.md) first.** It is the
  authoritative record of what phase the project is in, what is and is not
  connected, and what work is planned next. Do not assume capabilities or
  integrations exist without checking it.
- **Inspect `git status` before editing.** Understand what is already
  staged, modified, or untracked before making changes, so existing
  in-progress work is not lost or overwritten.

## Product Direction

- **Read the product documents first.** Read
  [docs/PRODUCT_VISION.md](docs/PRODUCT_VISION.md) and
  [docs/PRODUCT_ROADMAP.md](docs/PRODUCT_ROADMAP.md) before proposing
  product work.
- **Don't reduce the project to VWAP.** VWAP reversion is one researched
  evidence module of a broader SPY market-intelligence copilot, not the
  whole product.
- **Keep kinds of evidence separate.** Preserve the separation between
  researched facts, deterministic calculations, and model inference.
- **Roadmaps never override research.** A product or roadmap document never
  overrides a frozen research protocol, DECISION_RULES.md, or a recorded
  authorization boundary.

## Data and Credential Safety

- **This is an independent project.** It does not depend on or access the
  separate ORB_Project. No historical or live datasets are connected yet.
  Future data will come through this project's own reviewed connectors.
- **No access outside this repository.** No code may access files outside
  this repository unless the user explicitly authorizes a specific source.
- **Never expose credentials.** Never print, log, commit, or otherwise
  expose API keys or secrets (Alpaca, FRED, OpenAI, Anthropic, or any
  other). Use `.env` (never committed) and reference `.env.example` for the
  expected variable names.
- **Never connect brokerage execution.** Do not write code that places,
  modifies, or cancels orders, or that otherwise integrates with Robinhood
  or any brokerage execution API. Execution is manual only, by the user.

## Evidence and Claims

- **Never claim a model or strategy is validated without recorded
  evidence.** Do not describe a forecast, feature, or strategy as
  "validated," "proven," or "working" unless there is a recorded evaluation
  backing that claim.
- **Preserve failed and null findings.** Do not delete or silently discard
  negative results, failed experiments, or null findings from data,
  forecasts, or the trade journal. These are as valuable as positive
  results and must remain visible.

## How to Work

- **Make small, reviewable changes.** Prefer incremental, focused diffs
  over large sweeping changes, so each change can be understood and
  reviewed on its own.
- **Run relevant tests.** Before considering a change complete, run the
  tests relevant to what changed.
- **Do not commit or push unless explicitly instructed.** Never run
  `git commit` or `git push` on your own initiative; wait for an explicit
  instruction from the user.
