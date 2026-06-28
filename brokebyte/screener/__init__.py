"""Broker-agnostic swing-trade screener (Strategy v2).

This package never places orders. It screens a universe of LSE/US stocks
against the reconciled ruleset (see REASSESSMENT_AND_PLAN.md §3) and emits
alerts for setups that qualify. Execution is done manually by the user.

Runs in parallel with, and fully independent of, the news bot.
"""
