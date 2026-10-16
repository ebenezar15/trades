from indexedge.config.settings import Settings
from indexedge.models.registry import ModelRegistry
from indexedge.services.training import train_all


def test_train_all_builds_trains_and_registers(candles, make_candles_fn, rule, broken_rule, tmp_path):
    settings = Settings()
    reg = ModelRegistry(tmp_path / "m")
    data = {"NIFTY": candles, "BANKNIFTY": make_candles_fn(days=20, seed=11, price=52000.0)}
    res = train_all(data, {"rsi_rule": rule, "broken": broken_rule}, settings, reg)
    m = res["rsi_rule"]
    assert m["n"] > 150 and m["n_oos"] > 0
    assert 0.3 < m["auc"] < 0.7                                  # random-walk data: no real edge
    assert isinstance(m["usable"], bool)
    assert m["fallback"]["n"] == m["n"]
    assert reg.get("rsi_rule") is not None
    assert res["broken"]["usable"] is False and "scan failed" in res["broken"]["reason"]
    assert reg.get("broken") is None


def test_train_all_accepts_single_frame_and_no_signals(candles, tmp_path):
    class Never:
        def scan(self, feat, underlying):
            return []

    reg = ModelRegistry(tmp_path / "m")
    res = train_all(candles, {"never": Never()}, Settings(), reg)
    assert res["never"]["n"] == 0 and not res["never"]["usable"]
    assert reg.get("never") is not None and not reg.get("never").usable
