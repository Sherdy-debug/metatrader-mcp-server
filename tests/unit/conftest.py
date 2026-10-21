"""
Fixtures for unit tests: ``fake_mt5`` replaces the ``mt5`` module used by the
order functions, so the trading logic runs end to end without a terminal.
"""

import importlib
from types import SimpleNamespace

import pytest

from metatrader_client.risk import RiskConfig, RiskGuard
from .fakes import FakeMT5, PATCHED_MODULES


@pytest.fixture
def fake_mt5(monkeypatch):
    fake = FakeMT5()
    for name in PATCHED_MODULES:
        monkeypatch.setattr(importlib.import_module(name), "mt5", fake)
    return fake


@pytest.fixture
def connection():
    """Connection stand-in carrying a risk guard with live trading blocked and no other limits."""
    return SimpleNamespace(risk_guard=RiskGuard(RiskConfig(duplicate_window_seconds=0)))
