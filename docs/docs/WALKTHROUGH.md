# Kalshi Arbitrage Lab: Walkthrough

This document explains the market-microstructure and probability concepts the engine demonstrates: price formation, pricing violations, and the trading frictions that can eliminate an apparent edge. Read-only and paper-only: no account, no keys, no order code.

Run order (one command per line, in the folder with kalshi_arb.py):

    python3 kalshi_arb.py learn
    python3 kalshi_arb.py book KXRT-DIG-50
    python3 kalshi_arb.py event KXRT-DIG
    python3 kalshi_arb.py scan --verify 2
    python3 kalshi_arb.py sim SENATEAZ-28

(KXRT-DIG may have settled by the time you read this. Use any ticker the scan prints.)

## 1. Price is probability
A YES contract pays $1 or $0. A 39c price means the market says roughly 39%. YES + NO always = $1, so a NO price is just 1 minus a YES price.

## 2. The book only shows bids
A bid for NO at 61c equals an offer to sell YES at 39c. The code builds YES asks as 1 - (NO bids). Spread = best ask - best bid. The spread is what you pay for trading immediately (taker) and what market makers earn for waiting.
- Code: parse_book(), cmd_book().

## 3. Mid, microprice, imbalance
Mid is the average of bid and ask, but nobody can trade there. Honest read: the true probability lies somewhere in [bid, ask] unless you have a model. Microprice weights toward the thin side (small ask + big bid hints the price will tick up). Imbalance is the share of top-of-book size on the bid.

## 4. Fees are quadratic
fee = 0.07 x contracts x p x (1-p), rounded up to the cent. Max 1.75c at 50c, near 0 at the extremes. Because p(1-p) is the variance of a Bernoulli, fees track uncertainty.
- Code: fee().

## 5. Arbitrage family A: partitions
Mutually exclusive outcomes, exactly one wins. Prices should sum to 100c.
- Asks sum below 100c: buy all, collect $1. Only a guarantee if the list is exhaustive. Sums far below 100c usually mean outcomes are missing, so the scan ignores those (a fake arb).
- Bids sum above 100c: buy NO on every leg. At most one outcome wins, so at least n-1 NOs pay. Safe without exhaustiveness.
The overround (sum of mids minus 100c) is the book's margin.

## 6. Arbitrage family B: threshold ladders
"Score > 50" and "Score > 55" are nested: P(>55) <= P(>50), always. If bid(>55) > ask(>50), buy YES(>50) and NO(>55). Payout is at least $1 in every outcome (1, 2, or 1), cost below $1.
- Code: ladder_gaps().
- Mid-space violation (mids out of order but bid < ask) is a near-miss. The spread hides it.

## 7. A ladder is a distribution
P(>a) is a survival function (1 - CDF). Subtract adjacent strikes to get bucket probabilities: P(a < X <= b) = P(>a) - P(>b). They sum to 1 by construction. A negative bucket is impossible, which is how a violation shows up statistically. The event command prints the buckets, the entropy (how confident the market is), and a rough implied mean.

## 8. Why arbs die
- Spread: you pay the ask on every leg.
- Fees: charged per leg, and biggest near 50c.
- Depth: best price may be 5 contracts deep. Marginal contracts cost more, so profit rises, peaks, then goes negative with size. The sim shows that curve.
- Leg risk: legs fill one at a time. Prices move.
- Capital lockup: 1% over three months is a poor return.
- Rules risk: "mutually exclusive" has edge cases (ties, voided markets).
- Code: walk(), basket_pnl(), best_size().

## 9. Arbitrage vs mispricing
Arbitrage needs no opinion. A mispricing means your probability p differs from price q. EV per contract = p - q - fee, variance p(1-p). A 3c edge with +/-10c model error is noise. A model has to beat the half-spread plus fee plus its own error.

## Reproducibility checks
1. Run `scan --save` on three different days. Do the edges persist, or vanish within hours? (Data goes to arb_scans.db.)
2. Pick one `sim` result and compute the net profit by hand to the cent. Check against the output.
3. For a ladder, find the bucket with the biggest implied probability. Compare it with an independent model of the quantity.

## Potential research extensions
- Add a model-vs-market module: compare a base-rate model for a ladder with market-implied buckets.
- Add a persistence tracker over the saved scans to measure how long mispricings live.
