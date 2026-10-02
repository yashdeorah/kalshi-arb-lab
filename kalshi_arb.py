#!/usr/bin/env python3
"""
kalshi_arb.py - a Kalshi mispricing and arbitrage lab that teaches market
microstructure and probability modeling.

READ-ONLY and PAPER-ONLY. It only calls Kalshi's public market-data API.
No account, no login, no API keys, and it contains no code that can place an
order. Python 3 standard library only.

Commands (run  python3 kalshi_arb.py <command> --help  for options):
  learn                      offline lesson with worked numbers (no network)
  book TICKER                dissect one market's order book
  event EVENT_TICKER         treat an event as a probability distribution
  scan                       hunt for violations and near-violations
  sim EVENT_TICKER           paper-fill the best arbitrage basket in an event
"""
import argparse, json, math, sqlite3, sys, time, urllib.request, urllib.error
from decimal import Decimal, ROUND_CEILING

BASE = "https://external-api.kalshi.com/trade-api/v2"
FEE_RATE = 0.07  # taker fee = ceil_to_cent(0.07 * contracts * p * (1-p))
UA = {"User-Agent": "kalshi-arb-lab/1.0 (read-only educational)"}

# ---------------------------------------------------------------- API (GET only)
def get(path, params=None, retries=3):
    url = BASE + path
    if params:
        url += "?" + "&".join("%s=%s" % (k, v) for k, v in params.items() if v is not None)
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA, method="GET")
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(1.5 * (i + 1)); continue
            if e.code == 404:
                return None
            raise
        except urllib.error.URLError:
            time.sleep(1)
    sys.exit("Could not reach Kalshi's public API. Check your internet and retry.")

def f(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default

# ---------------------------------------------------------------- fee + book math
def fee(contracts, price):
    """Kalshi taker fee for one order: 0.07*C*p*(1-p), rounded UP to the next cent.
    Quadratic in p: biggest at 50c (1.75c/contract), tiny near 0 or 100."""
    if contracts <= 0:
        return 0.0
    raw = Decimal(str(FEE_RATE)) * Decimal(str(contracts)) * Decimal(str(price)) * (1 - Decimal(str(price)))
    return float((raw * 100).to_integral_value(rounding=ROUND_CEILING) / 100)

def parse_book(ob):
    """Kalshi books list only BIDS. A bid for NO at 0.60 is the same thing as an
    offer to sell YES at 0.40. So:
       yes_bids = yes_dollars            (people who will BUY yes)
       yes_asks = 1 - no bid prices      (people who will SELL yes to you)
    Returns levels best-first: [(price, size), ...]"""
    b = (ob or {}).get("orderbook_fp") or (ob or {}).get("orderbook") or {}
    yes = sorted([(f(p), f(s)) for p, s in b.get("yes_dollars", []) or []], reverse=True)
    no = sorted([(f(p), f(s)) for p, s in b.get("no_dollars", []) or []], reverse=True)
    yes_bids = [(p, s) for p, s in yes if p is not None]
    no_bids = [(p, s) for p, s in no if p is not None]
    yes_asks = [(round(1 - p, 4), s) for p, s in no_bids]   # best ask = highest no bid
    no_asks = [(round(1 - p, 4), s) for p, s in yes_bids]
    return {"yes_bids": yes_bids, "yes_asks": yes_asks, "no_bids": no_bids, "no_asks": no_asks}

def fetch_book(ticker):
    ob = get("/markets/%s/orderbook" % ticker)
    return parse_book(ob)

def walk(levels, n):
    """Buy n contracts walking levels best-first. Returns (cost, avg, filled)."""
    need, cost, got = n, 0.0, 0.0
    for p, s in levels:
        take = min(need, s)
        cost += take * p; got += take; need -= take
        if need <= 1e-9:
            break
    return cost, (cost / got if got else None), got

def top(levels):
    return levels[0] if levels else (None, 0.0)

def cents(x):
    return "  n/a" if x is None else "%5.1fc" % (x * 100)

def money(x):
    return ("-$%.2f" % -x) if x < 0 else ("$%.2f" % x)

# ---------------------------------------------------------------- market loading
def load_event(ev):
    d = get("/events/%s" % ev, {"with_nested_markets": "true"})
    if not d:
        sys.exit("Event %s not found. Event tickers look like KXRT-DIG (no -50 on the end)." % ev)
    e = d["event"]
    mk = e.get("markets") or d.get("markets") or []
    return e, [m for m in mk if m.get("status") in ("active", "open")]

def tob(m):
    """top of book from the market object (cheap, no extra call)"""
    return {"yb": f(m.get("yes_bid_dollars")), "ya": f(m.get("yes_ask_dollars")),
            "nb": f(m.get("no_bid_dollars")), "na": f(m.get("no_ask_dollars")),
            "yas": f(m.get("yes_ask_size_fp"), 0.0), "ybs": f(m.get("yes_bid_size_fp"), 0.0)}

def valid(x):
    return x is not None and 0 < x < 1

# ---------------------------------------------------------------- analyses
def ladder_of(markets):
    """If every market is a 'greater than strike' contract, return them sorted by strike."""
    out = []
    for m in markets:
        if m.get("strike_type") == "greater" and m.get("floor_strike") is not None:
            out.append((float(m["floor_strike"]), m))
    if len(out) >= 2 and len(out) == len(markets):
        return sorted(out, key=lambda t: t[0])
    return None

def analyze_partition(markets):
    """Mutually exclusive set. Returns dict of the two basket edges at top of book."""
    legs = [(m, tob(m)) for m in markets]
    if any(not (valid(t["ya"]) or valid(t["yb"])) for _, t in legs):
        pass
    asks = [t["ya"] for _, t in legs]
    bids = [t["yb"] for _, t in legs]
    res = {"n": len(legs)}
    res["sum_ask"] = sum(asks) if all(valid(a) for a in asks) else None
    res["sum_bid"] = sum(b for b in bids if b is not None)
    res["sum_mid"] = sum(((t["ya"] + t["yb"]) / 2) for _, t in legs if valid(t["ya"]) and valid(t["yb"]))
    res["n_two_sided"] = sum(1 for _, t in legs if valid(t["ya"]) and valid(t["yb"]))
    # buy NO on every leg (safe if outcomes are mutually exclusive): cost sum(na), payout >= n-1
    nas = [t["na"] for _, t in legs]
    res["no_basket_cost"] = sum(nas) if all(valid(x) for x in nas) else None
    res["no_basket_edge"] = (res["n"] - 1 - res["no_basket_cost"]) if res["no_basket_cost"] is not None else None
    res["yes_basket_edge"] = (1 - res["sum_ask"]) if res["sum_ask"] is not None else None
    return res

def ladder_gaps(lad):
    """For strikes a<b: P(>b) can never exceed P(>a). Gap in mid space and in executable space."""
    rows = []
    for (ka, ma), (kb, mb) in zip(lad, lad[1:]):
        a, b = tob(ma), tob(mb)
        if not (valid(a["ya"]) and valid(b["yb"])):
            continue
        mid_a = (a["ya"] + a["yb"]) / 2 if valid(a["yb"]) else None
        mid_b = (b["ya"] + b["yb"]) / 2 if valid(b["yb"]) else None
        rows.append({"lo": ma, "hi": mb, "klo": ka, "khi": kb,
                     # buy YES(>a) + buy NO(>b): payout >= $1 always. cost = ask_a + (1 - bid_b)
                     "exec_edge": b["yb"] - a["ya"] if b["yb"] is not None else None,
                     "mid_gap": (mid_b - mid_a) if (mid_a is not None and mid_b is not None) else None})
    return rows

# ---------------------------------------------------------------- basket paper-fill
def basket_pnl(legs_levels, n, payout, n_legs_cost_label=""):
    """legs_levels: list of ask-level lists (one per leg). Buy n of each leg.
    Returns dict with gross cost, fees, net profit, or None if depth is short."""
    gross, fees = 0.0, 0.0
    for lv in legs_levels:
        c, avg, got = walk(lv, n)
        if got + 1e-9 < n:
            return None
        gross += c
        # fee per price level touched (a conservative, simple model)
        need, ft = n, 0.0
        for p, s in lv:
            take = min(need, s); ft += FEE_RATE * take * p * (1 - p); need -= take
            if need <= 1e-9: break
        fees += math.ceil(round(ft * 100, 6)) / 100
    return {"n": n, "cost": gross, "fees": fees, "payout": payout * n,
            "gross_profit": payout * n - gross, "net": payout * n - gross - fees}

def best_size(legs_levels, payout, cap=5000):
    depth = min(sum(s for _, s in lv) for lv in legs_levels) if legs_levels else 0
    depth = int(min(depth, cap))
    if depth < 1:
        return [], None
    sizes = sorted(set([1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, depth] +
                       list(range(1, depth + 1, max(1, depth // 60)))))
    sizes = [s for s in sizes if s <= depth]
    rows = [r for r in (basket_pnl(legs_levels, s, payout) for s in sizes) if r]
    return rows, (max(rows, key=lambda r: r["net"]) if rows else None)

def show_sim(title, legs_levels, payout, tickers):
    print("\n--- Paper fill: %s ---" % title)
    rows, best = best_size(legs_levels, payout)
    if not rows:
        print("  No depth on at least one leg, so nothing to fill.")
        return
    print("  Legs: %s" % ", ".join(tickers))
    print("  %8s %10s %9s %9s %10s" % ("size", "cost", "fees", "payout", "NET"))
    shown = set()
    for r in rows:
        if r["n"] in (1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000) or r is best:
            if r["n"] in shown: continue
            shown.add(r["n"])
            print("  %8d %10s %9s %9s %10s%s" % (r["n"], money(r["cost"]), money(r["fees"]),
                  money(r["payout"]), money(r["net"]), "   <- best" if r is best else ""))
    if best["net"] > 0:
        print("  RESULT: paper profit %s at size %d (%.2f%% on cost). Depth ran out or marginal"
              " price+fees turned the next contract unprofitable beyond this." %
              (money(best["net"]), best["n"], 100 * best["net"] / best["cost"]))
    else:
        print("  RESULT: no size is profitable after fees. The 'gap' was an illusion once you "
              "pay real ask prices and real fees. Best you can do is %s." % money(best["net"]))
    print("  CAVEATS: paper only; ignores latency (books move while you click), leg risk (one"
          " leg fills, the other doesn't), and capital locked until settlement.")

# ---------------------------------------------------------------- commands
def cmd_learn(a):
    print(LESSON)

def cmd_book(a):
    t = a.ticker.upper()
    m = get("/markets/%s" % t)
    if not m:
        sys.exit("Market %s not found. Market tickers look like KXRT-DIG-50." % t)
    m = m["market"]
    bk = fetch_book(t)
    print("\n%s  -  %s" % (t, m.get("title")))
    print("status=%s  closes=%s" % (m.get("status"), m.get("close_time")))
    yb, ya = top(bk["yes_bids"]), top(bk["yes_asks"])
    print("\nORDER BOOK (best 8 levels each side). Remember: Kalshi shows only bids.")
    print("  YES bids (buyers)            YES asks (sellers, = 1 - NO bids)")
    for i in range(8):
        l = "%s x %-9.0f" % (cents(bk["yes_bids"][i][0]), bk["yes_bids"][i][1]) if i < len(bk["yes_bids"]) else " " * 18
        r = "%s x %-9.0f" % (cents(bk["yes_asks"][i][0]), bk["yes_asks"][i][1]) if i < len(bk["yes_asks"]) else ""
        print("  %s          %s" % (l, r))
    if yb[0] is None or ya[0] is None:
        print("\nOne side of the book is empty, so there is no spread or mid to compute.")
        return
    spread = ya[0] - yb[0]; mid = (ya[0] + yb[0]) / 2
    micro = (yb[0] * ya[1] + ya[0] * yb[1]) / (yb[1] + ya[1]) if (yb[1] + ya[1]) else mid
    imb = yb[1] / (yb[1] + ya[1]) if (yb[1] + ya[1]) else 0.5
    print("\nWHAT THE BOOK SAYS")
    print("  best bid %s, best ask %s" % (cents(yb[0]), cents(ya[0])))
    print("  spread   = ask - bid = %s. This is the price of immediacy: buy then instantly sell"
          " and you lose this." % cents(spread))
    print("  mid      = %s  <- the usual 'market probability', but you can't trade at it." % cents(mid))
    print("  Honest range for the true probability given only this book: %s to %s."
          " (A trade needs to beat the far side, not the mid.)" % (cents(yb[0]), cents(ya[0])))
    print("  imbalance= %.0f%% of top-of-book size is on the bid. microprice = %s" % (imb * 100, cents(micro)))
    print("    (microprice leans toward the thin side: a big bid and a tiny ask suggests the price"
          " is about to tick up.)")
    print("\nWHAT IT COSTS TO TRADE (taker fees included)")
    for n in (1, 10, 100, 1000):
        c, avg, got = walk(bk["yes_asks"], n)
        if got + 1e-9 < n:
            print("  buy %5d YES: only %.0f available at any price" % (n, got)); continue
        fe = fee(n, avg)
        print("  buy %5d YES: avg fill %s, slippage vs best ask %s, fee %s -> all-in %.2fc/contract" %
              (n, cents(avg), cents(avg - ya[0]), money(fe), 100 * (c + fe) / n))
    p = ya[0]
    print("\n  Fee per contract at your best-ask price: %.2fc. Max possible is 1.75c at 50c, and"
          " it shrinks toward the extremes because p*(1-p) does." % (100 * FEE_RATE * p * (1 - p)))
    print("  To profit buying YES at %s you need your probability above %s (ask + fee)." %
          (cents(p), cents(p + FEE_RATE * p * (1 - p))))

def cmd_event(a):
    e, mk = load_event(a.event.upper())
    print("\n%s  -  %s" % (e["event_ticker"], e.get("title")))
    print("mutually_exclusive flag: %s   open markets: %d" % (e.get("mutually_exclusive"), len(mk)))
    if not mk:
        return
    lad = ladder_of(mk)
    if lad:
        print("\nThis is a THRESHOLD LADDER: each contract is 'outcome > strike'. These are nested "
              "events (>55 implies >50), NOT mutually exclusive, so probabilities must FALL as the strike rises.")
        print("\n  %8s  %7s %7s %7s  %s" % ("strike", "bid", "ask", "mid", "ticker"))
        for k, m in lad:
            t = tob(m)
            mid = (t["ya"] + t["yb"]) / 2 if valid(t["ya"]) and valid(t["yb"]) else None
            print("  %8g  %s %s %s  %s" % (k, cents(t["yb"]), cents(t["ya"]), cents(mid), m["ticker"]))
        print("\nCHECK 1: is it monotone? (P(>b) must be <= P(>a) when b > a)")
        for g in ladder_gaps(lad):
            flag = ""
            if g["exec_edge"] is not None and g["exec_edge"] > 0:
                flag = "  <== EXECUTABLE VIOLATION: bid above is higher than ask below"
            elif g["mid_gap"] is not None and g["mid_gap"] > 0:
                flag = "  <== mids are out of order (spreads hide it; not capturable)"
            print("  >%g vs >%g: bid(>%g)-ask(>%g) = %s%s" % (g["khi"], g["klo"], g["khi"], g["klo"],
                  cents(g["exec_edge"]), flag))
        # implied distribution: P(a < X <= b) = P(>a) - P(>b)
        print("\nCHECK 2: the ladder IS a probability distribution. Differences of adjacent mids give"
              " the probability of landing in each bucket:")
        mids = []
        for k, m in lad:
            t = tob(m)
            mids.append((k, (t["ya"] + t["yb"]) / 2 if valid(t["ya"]) and valid(t["yb"]) else None))
        prev_p, prev_k = 1.0, "-inf"
        tot = 0.0
        for k, p in mids:
            if p is None:
                continue
            w = prev_p - p; tot += w
            bar = "#" * max(0, int(round(w * 60)))
            print("  (%s, %g]  %s %s%s" % (prev_k, k, cents(w), bar, "   NEGATIVE: impossible" if w < -1e-9 else ""))
            prev_p, prev_k = p, k
        print("  (%s, +inf)  %s" % (prev_k, cents(prev_p)))
        print("  Buckets sum to 100% by construction. A negative bucket means a monotonicity violation.")
        ps = [(k, p) for k, p in mids if p is not None]
        if len(ps) >= 2:
            mean_est = 0.0
            pp = 1.0
            # crude expected value: bucket midpoints between strikes
            pts = [ps[0][0] - (ps[1][0] - ps[0][0])] + [k for k, _ in ps] + [ps[-1][0] + (ps[-1][0] - ps[-2][0])]
            probs = [1 - ps[0][1]] + [ps[i][1] - (ps[i + 1][1] if i + 1 < len(ps) else 0) for i in range(len(ps))]
            for i, w in enumerate(probs):
                lo = pts[i]; hi = pts[i + 1] if i + 1 < len(pts) else pts[i]
                mean_est += w * (lo + hi) / 2
            print("  Rough market-implied mean (bucket midpoints, edges extrapolated): %.1f" % mean_est)
        return
    r = analyze_partition(mk)
    print("\n  %-34s %7s %7s %7s" % ("outcome", "bid", "ask", "mid"))
    for m in mk:
        t = tob(m)
        mid = (t["ya"] + t["yb"]) / 2 if valid(t["ya"]) and valid(t["yb"]) else None
        print("  %-34s %s %s %s" % ((m.get("yes_sub_title") or m["ticker"])[:34], cents(t["yb"]), cents(t["ya"]), cents(mid)))
    print("\nTHE DISTRIBUTION TEST (if the outcomes are mutually exclusive AND exhaustive)")
    print("  sum of asks = %s   sum of mids = %s   sum of bids = %s" %
          (cents(r["sum_ask"]), cents(r["sum_mid"]), cents(r["sum_bid"])))
    print("  Exactly one outcome pays $1, so a fair price set sums to 100c. The excess of the mids over"
          " 100c is the OVERROUND (the book's margin); the gap between asks and bids is made of spreads.")
    if r["sum_mid"] and r["n_two_sided"] == r["n"]:
        z = r["sum_mid"]
        print("  Normalized probabilities (mid / sum), what you'd model with:")
        H = 0.0
        for m in mk:
            t = tob(m); mid = (t["ya"] + t["yb"]) / 2
            pn = mid / z; H -= pn * math.log(pn, 2) if pn > 0 else 0
            print("    %-34s %5.1f%%" % ((m.get("yes_sub_title") or m["ticker"])[:34], pn * 100))
        print("  Entropy of the distribution: %.2f bits (max possible for %d outcomes: %.2f). Low entropy"
              " = the market is confident." % (H, r["n"], math.log(r["n"], 2)))
    print("\nTWO WAYS TO TRADE THE SUM")
    if r["yes_basket_edge"] is not None:
        print("  Buy YES on all legs: cost %s, pays 100c. Edge %s before fees. Needs outcomes EXHAUSTIVE"
              " (if 'none of these' can happen you lose the whole cost)." % (cents(r["sum_ask"]), cents(r["yes_basket_edge"])))
    if r["no_basket_edge"] is not None:
        print("  Buy NO on all legs: cost %s, pays at least %dc (n-1). Edge %s before fees. Safe whenever"
              " outcomes are mutually exclusive, even if not exhaustive." %
              (cents(r["no_basket_cost"]), (r["n"] - 1) * 100, cents(r["no_basket_edge"])))
    print("  Run:  python3 kalshi_arb.py sim %s   to paper-fill it with real depth and fees." % e["event_ticker"])

def cmd_sim(a):
    e, mk = load_event(a.event.upper())
    print("\n%s - %s" % (e["event_ticker"], e.get("title")))
    lad = ladder_of(mk)
    if lad:
        gaps = [g for g in ladder_gaps(lad) if g["exec_edge"] is not None]
        gaps.sort(key=lambda g: -g["exec_edge"])
        if not gaps:
            print("Not enough two-sided quotes."); return
        g = gaps[0]
        print("Best adjacent pair: >%g vs >%g, bid(>%g) - ask(>%g) = %s" %
              (g["klo"], g["khi"], g["khi"], g["klo"], cents(g["exec_edge"])))
        print("Trade: buy YES on %s and buy NO on %s. Whatever the outcome, that pair pays at least $1." %
              (g["lo"]["ticker"], g["hi"]["ticker"]))
        blo, bhi = fetch_book(g["lo"]["ticker"]), fetch_book(g["hi"]["ticker"])
        show_sim("monotonicity pair (payout >= $1)", [blo["yes_asks"], bhi["no_asks"]], 1.0,
                 [g["lo"]["ticker"] + " YES", g["hi"]["ticker"] + " NO"])
        return
    r = analyze_partition(mk)
    n = len(mk)
    books = {}
    for m in mk:
        books[m["ticker"]] = fetch_book(m["ticker"]); time.sleep(0.12)
    tick = [m["ticker"] for m in mk]
    if r["sum_mid"] >= 0.85 and r["n_two_sided"] == r["n"]:
        show_sim("buy YES on every outcome (payout exactly $1 IF exhaustive)", [books[t]["yes_asks"] for t in tick], 1.0,
                 [t + " YES" for t in tick])
    else:
        print("\n--- YES basket skipped ---\n  The listed outcomes' mids sum to only %s (or some legs are one-sided)."
              " Sums far below 100c almost always mean the list is INCOMPLETE (other outcomes exist or\n"
              "  aren't listed), so 'buy everything for %s' is not a guaranteed $1. Cheap-looking sums are the"
              " classic fake arbitrage." % (cents(r["sum_mid"]), cents(r["sum_ask"])))
    show_sim("buy NO on every outcome (payout >= $%d if mutually exclusive)" % (n - 1), [books[t]["no_asks"] for t in tick],
             float(n - 1), [t + " NO" for t in tick])
    print("\nNote: the NO basket's real payout is n-1 if exactly one outcome wins, or n if none win."
          " This sim counts only the guaranteed n-1.")

def cmd_scan(a):
    print("Scanning open Kalshi events (read-only). pages=%d, min 24h-volume-free filter: min_open_interest=%s" %
          (a.pages, a.min_oi))
    cursor, events = None, []
    for _ in range(a.pages):
        d = get("/events", {"status": "open", "limit": 200, "with_nested_markets": "true", "cursor": cursor})
        if not d: break
        events += d.get("events", [])
        cursor = d.get("cursor")
        if not cursor: break
        time.sleep(0.2)
    print("Loaded %d events." % len(events))
    cands = []
    for e in events:
        if a.prefix and not e["event_ticker"].upper().startswith(a.prefix.upper()):
            continue
        mk = [m for m in (e.get("markets") or []) if m.get("status") in ("active", "open")]
        mk = [m for m in mk if (f(m.get("open_interest_fp"), 0) or 0) >= a.min_oi]
        if len(mk) < 2:
            continue
        lad = ladder_of(mk)
        if lad:
            for g in ladder_gaps(lad):
                if g["exec_edge"] is not None:
                    cands.append(("ladder", g["exec_edge"], e, "%s: bid(>%g)=%s vs ask(>%g)=%s  mid_gap=%s" % (
                        e["event_ticker"], g["khi"], cents(tob(g["hi"])["yb"]), g["klo"], cents(tob(g["lo"])["ya"]), cents(g["mid_gap"]))))
        elif e.get("mutually_exclusive"):
            r = analyze_partition(mk)
            if r["no_basket_edge"] is not None and r["n_two_sided"] == r["n"]:
                cands.append(("ME-NO-basket", r["no_basket_edge"], e,
                              "%s: %d legs, sum(yes bids)=%s, NO-basket edge %s" % (
                                  e["event_ticker"], r["n"], cents(r["sum_bid"]), cents(r["no_basket_edge"]))))
            if r["yes_basket_edge"] is not None and r["sum_mid"] >= 0.85 and r["n_two_sided"] == r["n"]:
                # sum far below 100c means the listed outcomes are INCOMPLETE (not exhaustive): not an arb
                cands.append(("ME-YES-basket", r["yes_basket_edge"], e,
                              "%s: %d legs, sum(yes asks)=%s, YES-basket edge %s (needs exhaustive)" % (
                                  e["event_ticker"], r["n"], cents(r["sum_ask"]), cents(r["yes_basket_edge"]))))
    cands.sort(key=lambda c: -c[1])
    pos = [c for c in cands if c[1] > 0]
    print("Checked %d basket/pair candidates. %d show a positive gross edge at top of book." % (len(cands), len(pos)))
    print("\nTOP CANDIDATES (edge = guaranteed gross profit per $1 payout, BEFORE fees and depth):")
    for kind, edge, e, text in cands[:a.top]:
        print("  [%-13s] %+.1fc  %s" % (kind, edge * 100, text))
    print("\nNEAR-MISSES are the interesting ones: edge slightly negative means the market is almost"
          " inconsistent but the spread protects it. That is the spread doing its job.")
    if a.verify and cands:
        print("\nVERIFYING the top %d with full books, fees and depth..." % a.verify)
        for kind, edge, e, text in cands[:a.verify]:
            print("\n== %s ==" % text)
            ns = argparse.Namespace(event=e["event_ticker"])
            try:
                cmd_sim(ns)
            except SystemExit as ex:
                print("  skipped:", ex)
    if a.save:
        db = sqlite3.connect(a.db)
        db.execute("CREATE TABLE IF NOT EXISTS arb_scan(ts TEXT, kind TEXT, edge REAL, event TEXT, detail TEXT)")
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        db.executemany("INSERT INTO arb_scan VALUES (?,?,?,?,?)", [(now, k, ed, e["event_ticker"], t) for k, ed, e, t in cands[:200]])
        db.commit(); db.close()
        print("\nSaved top 200 candidates to %s (a separate file from lab.db). Re-run later: do edges persist?" % a.db)
    print("\nNext: python3 kalshi_arb.py event <EVENT_TICKER>   (see the distribution)\n"
          "      python3 kalshi_arb.py sim <EVENT_TICKER>     (paper-fill with fees and depth)")

LESSON = r"""
=============== KALSHI ARBITRAGE LAB: THE LESSON (offline, no network) ===============

1. WHAT A CONTRACT IS
   A Kalshi YES contract pays $1 if the event happens, $0 if not. A NO contract pays
   $1 if it does not. So price = the market's probability, in dollars. YES at 40c
   means "the crowd says about 40%".

2. THE ORDER BOOK ONLY HAS BIDS
   Kalshi lists buy orders. A bid to buy NO at 60c is the same as an offer to sell
   YES at 40c (YES + NO always sums to $1). So:  YES ask = 100c - best NO bid.
   Example: YES bids 36c, NO bids 61c -> YES ask is 39c. Spread = 39 - 36 = 3c.

3. SPREAD, MID, AND WHY "THE PROBABILITY" IS A RANGE
   Mid = (bid+ask)/2 = 37.5c. But you buy at 39c and sell at 36c. The spread is
   the toll for trading right now (the 'taker' price). Market makers earn it for
   waiting. Any 'mispricing' smaller than the spread is not tradable.

4. FEES ARE QUADRATIC
   fee = 0.07 * contracts * p * (1-p), rounded up to the next cent.
   At p=50c: 1.75c per contract. At p=10c: 0.63c. At p=2c: 0.14c.
   Fees hurt most exactly where the book is most uncertain.

5. ARBITRAGE FAMILY A: A PARTITION (mutually exclusive outcomes)
   Candidates A, B, C. Exactly one wins. Their YES prices should sum to 100c.
   Suppose asks are 40c + 35c + 28c = 103c. Buying all three costs 103c, pays 100c:
   a guaranteed LOSS of 3c. The overround (3c) is the market's margin.
   Now suppose asks sum to 97c. Buy all three for 97c, collect 100c: 3c locked in.
   That is free money in theory. It needs: (a) the outcomes are exhaustive (no
   'someone else wins'), (b) depth at every leg, (c) all fills at the same time.
   Mirror trade: if YES BIDS sum above 100c (say 104c), buy NO on every leg. At most
   one outcome wins, so at least n-1 of your NOs pay $1. Safe even if not exhaustive.

6. ARBITRAGE FAMILY B: A THRESHOLD LADDER (nested outcomes)
   'Score > 50' and 'Score > 55'. If the score tops 55 it also tops 50, so
   P(>55) can never exceed P(>50). If bid(>55)=30c and ask(>50)=28c, buy YES(>50)
   at 28c and NO(>55) at 70c. Cost 98c. Payout: 100c if score<=50 (NO pays),
   100c if >55 (YES pays), 200c in between. Never below 100c. Locked 2c.

7. A LADDER IS A PROBABILITY DISTRIBUTION
   P(>50) - P(>55) = P(50 < score <= 55). Subtracting adjacent contracts gives the
   probability of each bucket. A negative bucket is impossible, hence a mispricing.
   This is a survival function (1 - CDF). The ladder is the market's whole belief
   about the outcome, not just one number.

8. WHY MOST ARBS DIE IN PRACTICE
   Spread: you pay the ask on every leg. Fees: quadratic and per leg. Depth: the
   best price may be 5 contracts deep. Leg risk: legs fill one at a time, prices
   move between clicks. Capital: money sits locked until settlement, so a 1% gain
   over 3 months is a poor return. Resolution risk: a 'mutually exclusive' event
   may have an edge case. This engine's 'sim' command walks the real book to show
   each of these costs in dollars.

9. WHAT A MISPRICING IS, STATISTICALLY
   Two different things:
   - Arbitrage: a guaranteed profit across contracts. Needs no opinion.
   - Mispricing: your probability differs from the market's. This is a bet, and it
     requires a model. A gap only counts if (your p - price - fee) is bigger than the
     uncertainty in your own estimate. A 3c edge with +/-10c model error is noise.
   Expected value of buying YES at price q with true probability p:
       EV per contract = p - q - fee.   Variance = p(1-p). Few trades -> luck dominates.

TRY IT ON LIVE DATA
   python3 kalshi_arb.py scan --verify 3
   python3 kalshi_arb.py event KXRT-DIG
   python3 kalshi_arb.py book KXRT-DIG-50
=======================================================================================
"""

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("learn").set_defaults(fn=cmd_learn)
    p = sub.add_parser("book"); p.add_argument("ticker"); p.set_defaults(fn=cmd_book)
    p = sub.add_parser("event"); p.add_argument("event"); p.set_defaults(fn=cmd_event)
    p = sub.add_parser("sim"); p.add_argument("event"); p.set_defaults(fn=cmd_sim)
    p = sub.add_parser("scan")
    p.add_argument("--pages", type=int, default=6, help="pages of 200 events to load (default 6)")
    p.add_argument("--prefix", default="", help="only event tickers starting with this, e.g. KXRT")
    p.add_argument("--min-oi", type=float, default=100, help="skip markets with open interest below this")
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--verify", type=int, default=0, help="paper-fill the top N with fees and depth")
    p.add_argument("--save", action="store_true", help="append candidates to arb_scans.db")
    p.add_argument("--db", default="arb_scans.db")
    p.set_defaults(fn=cmd_scan)
    a = ap.parse_args()
    if not a.cmd:
        ap.print_help(); return
    a.fn(a)

if __name__ == "__main__":
    main()
