"""Shared synthetic data for strategy tests (built once per session)."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from indexedge.data.synthetic import generate_intraday
from indexedge.features.engine import build_features


@pytest.fixture(scope="session")
def nifty250() -> pd.DataFrame:
    return generate_intraday("NIFTY", days=250, seed=0)


@pytest.fixture(scope="session")
def feat250(nifty250) -> pd.DataFrame:
    return build_features(nifty250)


@pytest.fixture(scope="session")
def nifty60() -> pd.DataFrame:
    return generate_intraday("NIFTY", days=60, seed=11, start=date(2026, 3, 2))
