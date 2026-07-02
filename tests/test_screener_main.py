"""Tests for the screener CLI failure-alert wrapper (post-incident: a crashed
nightly run must post a FAILED message, not fail silently)."""

import pytest

import brokebyte.screener.__main__ as cli


def test_main_failure_posts_alert_and_reraises(monkeypatch):
    sent = []
    monkeypatch.setattr(cli.alerts, "send", lambda msg: sent.append(msg))
    def boom(**kwargs):
        raise RuntimeError("universe fetch exploded")
    monkeypatch.setattr(cli, "load_universe", boom)

    with pytest.raises(RuntimeError, match="universe fetch exploded"):
        cli.main([])

    assert len(sent) == 1
    assert "FAILED" in sent[0]
    assert "RuntimeError" in sent[0]
    assert "universe fetch exploded" in sent[0]


def test_main_failure_survives_broken_webhook(monkeypatch):
    def bad_send(msg):
        raise ConnectionError("telegram down")
    monkeypatch.setattr(cli.alerts, "send", bad_send)
    def boom(**kwargs):
        raise ValueError("original error")
    monkeypatch.setattr(cli, "load_universe", boom)

    with pytest.raises(ValueError, match="original error"):
        cli.main([])  # the ORIGINAL error propagates, not the webhook one


def test_main_success_sends_digest_not_failure(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(cli.alerts, "send", lambda msg: sent.append(msg))
    monkeypatch.setattr(cli, "load_universe", lambda **kw: ["FAKE"])
    monkeypatch.setenv("LOG_DIR", str(tmp_path))

    class FakeScreener:
        def __init__(self, provider, account=500.0):
            pass
        def scan(self, symbols, **kw):
            return []
    monkeypatch.setattr(cli, "Screener", FakeScreener)
    monkeypatch.setattr(cli, "YFinanceProvider", lambda: None)

    cli.main([])

    assert len(sent) == 1
    assert "no qualifying setups" in sent[0]
    assert "FAILED" not in sent[0]
