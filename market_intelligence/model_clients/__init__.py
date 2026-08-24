"""Model-provider client boundaries for AI Market Intelligence.

Every module in this package is a narrow, defensive boundary around one
external model provider's SDK. No module here contains agent prompts,
market bias, forecasting logic, or recommendations -- see
``market_intelligence.model_clients.openai_structured`` and
``docs/OPENAI_PROVIDER_BOUNDARY.md``.
"""
