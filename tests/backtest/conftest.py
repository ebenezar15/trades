"""Synthetic data for backtest tests (built once per session)."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from indexedge.data.synthetic import generate_intraday
from indexedge.features.engine import build_features


@pytest.fixture(scope="session")
def bt_raw() -> pd.DataFrame:
    return generate_intraday("NIFTY", days=120, seed=5, start=date(2026, 1, 5))


@pytest.fixture(scope="session")
def bt_feat(bt_raw) -> pd.DataFrame:
    return build_features(bt_raw)
