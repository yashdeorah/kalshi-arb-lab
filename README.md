# Kalshi Arbitrage Lab

A read-only, paper-only Python CLI for studying pricing consistency, order-book microstructure, and market-implied probability distributions in prediction markets.

## Research questions

- How long do pricing violations survive in prediction markets once spreads, fees, and available depth are taken into account?
- How quickly is public news absorbed into market prices?

The engine provides snapshot-level measurements for these questions. Longitudinal collection and news-event analysis are ongoing research work, not completed features or findings of this CLI.

**Status: collecting live market data; findings posted when statistically meaningful.**

## What the engine does

- Scans up to approximately 1,200 open Kalshi events per run by default (six pages of up to 200 events; the actual count depends on API results).
- Computes bid/ask-implied probabilities, spreads, microprices, book imbalance, and threshold-ladder bucket probabilities.
- Flags pricing inconsistencies across nested thresholds and mutually exclusive outcomes.
- Paper-fills selected candidate baskets against live order-book depth, including slippage and the implemented quadratic taker-fee model: `ceil_to_cent(0.07 * contracts * p * (1 - p))`.
- Optionally stores timestamped scan candidates in a local SQLite database.

No account, API key, or trading credentials are needed. The code only requests public market data and cannot place orders. Python standard library only.

## Quickstart

Python 3 is required. No packages need to be installed.

```sh
python3 kalshi_arb.py --help
python3 kalshi_arb.py learn
python3 kalshi_arb.py scan --verify 2
python3 kalshi_arb.py scan --save
```

Use an active ticker or event ticker printed by the scan:

```sh
python3 kalshi_arb.py book <MARKET_TICKER>
python3 kalshi_arb.py event <EVENT_TICKER>
python3 kalshi_arb.py sim <EVENT_TICKER>
```

Replace the angle-bracket placeholders before running those commands. Market and event tickers differ; market tickers include the outcome or strike. `learn` runs offline. The other commands fetch current market data. `--save` writes `arb_scans.db` in the working directory; use `--db PATH` to choose another location.

See [the walkthrough](docs/WALKTHROUGH.md) for pricing identities, fee math, simulation mechanics, worked concepts, and reproducibility checks.

## Interpretation and limits

A positive gross top-of-book edge is a candidate, not a confirmed profit. Fees, spread, depth, stale or sequential quotes, and leg risk can erase it. Buying every YES outcome only has the stated payout when the outcomes are exhaustive; the scan's price-sum filter is a heuristic, not proof of exhaustiveness. Settlement rules, ties, and voided markets need separate review.

The fee model is an assumption, not a guarantee of the current fee schedule for every market. Paper fills do not model queue position, simultaneous execution, or actual order acceptance. The default scan is a capped sample, not a census of all markets. Saved scan rows are repeated observations and are not necessarily independent trials; the CLI does not estimate violation lifetimes or identify news events on its own.

Findings should distinguish gross from executable edges, state sample sizes and uncertainty, and account for repeated observations and censoring. No performance or causal conclusions are claimed at this stage.

## Repository contents

- `kalshi_arb.py`: CLI, market-data access, pricing checks, and paper-fill calculations.
- `docs/WALKTHROUGH.md`: reader-facing documentation of the concepts and methods.
- `.gitignore`: excludes local databases, CSV exports, and data directories.

Raw data and personal files are not included in this repository. This is a research tool, not investment advice or a live trading system.
