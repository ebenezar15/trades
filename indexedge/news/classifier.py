"""Local, rule-based news classifier: relevance, sentiment and impact (no runtime LLM calls).

* relevance — weighted keyword hits (index names, RBI, rates, inflation, flows, Fed, crude, rupee) plus
  heavyweight constituent names/aliases taken from settings.
* sentiment — VADER compound score with a finance lexicon boost and phrase rules; optionally blended with
  FinBERT when ``use_finbert`` is set *and* ``transformers`` is importable.
* impact — rules: RBI policy, budget, Fed/FOMC, CPI/GDP prints, war/attack, bans, SEBI action and
  heavyweight results are ``high``; any relevant item is ``medium``; the rest ``low``.
"""
from __future__ import annotations

import importlib.util
import logging
import re
from collections.abc import Iterable
from dataclasses import replace
from typing import Any

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from indexedge.core.types import NewsItem

log = logging.getLogger(__name__)

# (pattern, weight) — weights add up, relevance is clipped to 1.
RELEVANCE_KEYWORDS: list[tuple[str, float]] = [
    (r"\bnifty\b", 0.6), (r"\bbank ?nifty\b", 0.7), (r"\bsensex\b", 0.5), (r"\bdalal street\b", 0.4),
    (r"\brbi\b|reserve bank", 0.5), (r"\brepo rate\b", 0.6), (r"\bmpc\b", 0.4), (r"\binflation\b", 0.3),
    (r"\bcpi\b", 0.3), (r"\bgdp\b", 0.3), (r"\bfiis?\b|\bfpis?\b|foreign (institutional|portfolio)", 0.4),
    (r"\bdiis?\b", 0.2), (r"\bfed\b|federal reserve|\bfomc\b|powell", 0.4), (r"\bcrude\b|\bbrent\b", 0.3),
    (r"\brupee\b|\busd/?inr\b", 0.3), (r"\bsebi\b", 0.3), (r"\bbudget\b", 0.3), (r"\bindia vix\b", 0.4),
    (r"\bf&o\b|\bderivatives?\b|\bexpiry\b", 0.2), (r"\bstock market\b|\bmarkets?\b", 0.1),
]

# Common aliases for Nifty / Bank Nifty heavyweights (symbol -> extra names).
HEAVYWEIGHT_ALIASES: dict[str, list[str]] = {
    "HDFCBANK": ["hdfc bank"], "ICICIBANK": ["icici bank", "icici"], "RELIANCE": ["reliance industries",
    "reliance", "ril"], "INFY": ["infosys"], "BHARTIARTL": ["bharti airtel", "airtel"],
    "LT": ["larsen & toubro", "larsen and toubro", "l&t"], "ITC": ["itc"], "TCS": ["tcs", "tata consultancy"],
    "SBIN": ["state bank of india", "sbi"], "AXISBANK": ["axis bank"], "KOTAKBANK": ["kotak mahindra bank",
    "kotak bank", "kotak"], "M&M": ["mahindra & mahindra", "mahindra and mahindra", "m&m"],
    "INDUSINDBK": ["indusind bank", "indusind"], "FEDERALBNK": ["federal bank"],
    "BANKBARODA": ["bank of baroda"], "AUBANK": ["au small finance", "au bank"],
    "PNB": ["punjab national bank", "pnb"], "HINDUNILVR": ["hindustan unilever", "hul"],
    "BAJFINANCE": ["bajaj finance"], "MARUTI": ["maruti suzuki", "maruti"], "SUNPHARMA": ["sun pharma"],
    "HCLTECH": ["hcltech", "hcl tech"], "TATAMOTORS": ["tata motors"], "NTPC": ["ntpc"],
}

# Finance-specific valence tweaks for VADER (scale -4..4).
FINANCE_LEXICON: dict[str, float] = {
    "surge": 2.5, "surges": 2.5, "soar": 2.6, "soars": 2.6, "rally": 2.4, "rallies": 2.4, "jump": 1.8,
    "jumps": 1.8, "gain": 1.6, "gains": 1.6, "rebound": 1.8, "rebounds": 1.8, "bullish": 2.5,
    "outperform": 1.8, "upgrade": 2.0, "upgrades": 2.0, "beat": 1.5, "beats": 1.8, "record": 1.2,
    "inflow": 1.5, "inflows": 1.5, "easing": 1.0, "plunge": -3.0, "plunges": -3.0, "crash": -3.2,
    "crashes": -3.2, "slump": -2.6, "slumps": -2.6, "tumble": -2.6, "tumbles": -2.6, "sink": -2.0,
    "sinks": -2.0, "fall": -1.6, "falls": -1.6, "drop": -1.6, "drops": -1.6, "slide": -1.6, "slides": -1.6,
    "decline": -1.6, "declines": -1.6, "bearish": -2.5, "selloff": -2.6, "sell-off": -2.6,
    "downgrade": -2.0, "downgrades": -2.0, "miss": -1.5, "misses": -1.8, "outflow": -1.5, "outflows": -1.5,
    "default": -2.5, "recession": -2.8, "slowdown": -2.0, "war": -2.8, "attack": -2.6, "tariff": -1.2,
    "tariffs": -1.2, "volatile": -1.0, "volatility": -0.8, "probe": -1.5, "penalty": -1.8, "fraud": -3.0,
}

PHRASE_ADJUST: list[tuple[str, float]] = [
    (r"rate cut|cuts? (the )?(repo|interest) rate|cuts? rates", 0.35),
    (r"rate hike|hikes? (the )?(repo|interest) rate|hikes? rates", -0.35),
    (r"fiis? (sell|dump|pull out)|foreign (investors|funds) (sell|exit)|fii selling", -0.3),
    (r"fiis? (buy|pour)|fii buying", 0.3), (r"profit[- ]booking|profit[- ]taking", -0.2),
    (r"all[- ]time high|record high|fresh high", 0.3), (r"52[- ]week low|multi[- ]month low", -0.3),
]

# Impact rules (any match ⇒ high).
HIGH_IMPACT_PATTERNS: list[str] = [
    r"\brbi\b.*\b(policy|repo|rate|mpc|stance)\b|\bmonetary policy\b|\brepo rate\b|\bmpc\b",
    r"\bunion budget\b|\bbudget 20\d\d\b|\bbudget (day|speech)\b|\binterim budget\b",
    r"\bfomc\b|\bfed(eral reserve)?\b.*\b(rate|hike|cut|decision|powell|holds?)\b|\bpowell\b",
    r"\b(cpi|retail inflation|wpi|inflation data|gdp|gdp growth)\b",
    r"\bwar\b|\battack(s|ed)?\b|\bairstrikes?\b|\bmissiles?\b|\bterror\w*\b|\binvasion\b|\bsanctions?\b",
    r"\bsebi\b.*\b(ban|bars?|barred|penalty|order|crackdown|curbs?|new rules?|circular|tightens?)\b",
]
# Bans only matter when the story is market-relevant ("Australia bans ..." is not).
BAN_PATTERN = r"\bban(s|ned)?\b|\bprohibit(s|ed)?\b"
RESULTS_PATTERN = r"\b(q[1-4]|results?|earnings|profit|net income|quarterly)\b"


def _compile(patterns: Iterable[str]) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


class NewsClassifier:
    """Classify :class:`NewsItem` objects. ``heavyweights`` may be the settings mapping
    ``{underlying: {symbol: weight}}`` or a plain list of symbols."""

    def __init__(self, heavyweights: dict[str, dict[str, float]] | Iterable[str] | None = None,
                 use_finbert: bool = False) -> None:
        symbols: set[str] = set()
        if isinstance(heavyweights, dict):
            for v in heavyweights.values():
                symbols.update(v.keys() if isinstance(v, dict) else [v])
        elif heavyweights:
            symbols.update(heavyweights)
        self.heavyweights = sorted(symbols)
        self._hw_patterns: list[tuple[str, re.Pattern]] = []
        for sym in self.heavyweights:
            names = {sym.lower(), *HEAVYWEIGHT_ALIASES.get(sym, [])}
            alts = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
            self._hw_patterns.append((sym, re.compile(rf"(?<![\w&])({alts})(?![\w&])", re.IGNORECASE)))
        self._rel = [(re.compile(p, re.IGNORECASE), w) for p, w in RELEVANCE_KEYWORDS]
        self._phrases = [(re.compile(p, re.IGNORECASE), w) for p, w in PHRASE_ADJUST]
        self._high = _compile(HIGH_IMPACT_PATTERNS)
        self._results = re.compile(RESULTS_PATTERN, re.IGNORECASE)
        self._ban = re.compile(BAN_PATTERN, re.IGNORECASE)
        self._vader = SentimentIntensityAnalyzer()
        self._vader.lexicon.update(FINANCE_LEXICON)
        self._finbert: Any = None
        self.use_finbert = bool(use_finbert) and importlib.util.find_spec("transformers") is not None
        if use_finbert and not self.use_finbert:
            log.info("FinBERT requested but transformers is not installed; using VADER only")

    # ------------------------------------------------------------------ parts
    def heavyweight_hits(self, text: str) -> list[str]:
        return [sym for sym, pat in self._hw_patterns if pat.search(text)]

    def relevance(self, text: str) -> tuple[float, list[str]]:
        score = sum(w for pat, w in self._rel if pat.search(text))
        hits = self.heavyweight_hits(text)
        score += 0.4 * len(hits)
        return min(1.0, round(score, 3)), hits

    def _finbert_score(self, text: str) -> float | None:
        if not self.use_finbert:
            return None
        try:
            if self._finbert is None:
                from transformers import pipeline  # optional heavy dependency

                self._finbert = pipeline("text-classification", model="ProsusAI/finbert")
            res = self._finbert(text[:512])[0]
            sign = {"positive": 1.0, "negative": -1.0}.get(str(res["label"]).lower(), 0.0)
            return sign * float(res["score"])
        except Exception:
            log.warning("FinBERT failed; disabling it for this session", exc_info=True)
            self.use_finbert = False
            return None

    def sentiment(self, text: str) -> float:
        score = float(self._vader.polarity_scores(text)["compound"])
        score += sum(w for pat, w in self._phrases if pat.search(text))
        fb = self._finbert_score(text)
        if fb is not None:
            score = 0.5 * score + 0.5 * fb
        return max(-1.0, min(1.0, round(score, 4)))

    def impact(self, text: str, relevance: float, hits: list[str]) -> str:
        if any(p.search(text) for p in self._high):
            return "high"
        if relevance > 0 and self._ban.search(text):
            return "high"
        if hits and self._results.search(text):
            return "high"
        if relevance >= 0.3:
            return "medium"
        return "low"

    # ------------------------------------------------------------------ public
    def classify(self, item: NewsItem) -> NewsItem:
        """Return a copy of ``item`` with relevance, sentiment, impact and entities filled in."""
        title_text = item.title
        full = f"{item.title}. {item.summary}".strip()
        rel, hits = self.relevance(full)
        # sentiment is driven by the headline; summaries are often boilerplate
        sent = self.sentiment(title_text if title_text else full)
        imp = self.impact(title_text + " " + item.summary[:200], rel, hits)
        return replace(item, relevance=rel, sentiment=sent, impact=imp, entities=hits)
