"""Regression tests for YFinanceProvider.fx_per_gbp and a source-integrity
tripwire. Context: commit c7e2e7c accidentally committed a linter-truncated
yfinance_provider.py whose fx_per_gbp body ended in a bare `t` (a NameError at
call time that no test exercised, and which crashed the whole nightly scan).
These tests make both failure modes loud."""

import ast
from pathlib import Path

from brokebyte.screener import yfinance_provider as yp

BROKEBYTE = Path(yp.__file__).resolve().parents[1]


class _FakeFastInfo:
    def __init__(self, **kv):
        self._kv = kv

    def __getattr__(self, key):
        try:
            return self._kv[key]
        except KeyError:
            raise AttributeError(key)

    def __getitem__(self, key):
        return self._kv[key]


class _FakeTicker:
    def __init__(self, fast_info):
        self.fast_info = fast_info


def _provider_with_rate(rate):
    prov = yp.YFinanceProvider.__new__(yp.YFinanceProvider)  # skip yf import
    prov._ticker = lambda symbol: _FakeTicker(_FakeFastInfo(last_price=rate))
    return prov


def test_fx_per_gbp_gbp_is_one():
    prov = yp.YFinanceProvider.__new__(yp.YFinanceProvider)
    assert prov.fx_per_gbp("GBP") == 1.0


def test_fx_per_gbp_usd_returns_rate():
    assert _provider_with_rate(1.3250).fx_per_gbp("USD") == 1.3250


def test_fx_per_gbp_missing_rate_returns_none():
    assert _provider_with_rate(None).fx_per_gbp("USD") is None


def test_fx_per_gbp_provider_error_returns_none():
    prov = yp.YFinanceProvider.__new__(yp.YFinanceProvider)
    def boom(symbol):
        raise RuntimeError("network down")
    prov._ticker = boom
    assert prov.fx_per_gbp("USD") is None


def test_no_truncated_functions_in_brokebyte():
    """Tripwire for the mount's linter truncating files mid-function: no
    function body in brokebyte/ may end with a bare-Name expression statement,
    and every module must end with a newline."""
    offenders = []
    for path in sorted(BROKEBYTE.rglob("*.py")):
        src = path.read_text(encoding="utf-8", errors="replace")
        if src and not src.endswith("\n"):
            offenders.append(f"{path}: no trailing newline")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
                last = node.body[-1]
                if isinstance(last, ast.Expr) and isinstance(last.value, ast.Name):
                    offenders.append(f"{path}:{last.lineno} {node.name} ends in bare name")
    assert not offenders, "; ".join(offenders)
