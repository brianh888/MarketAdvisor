"""Offline lifecycle test of the options stream (the wheel), with simulated quotes.

Runs against a throwaway database — never touches state/trading.db. Verifies the cash
ledger at every step: propose -> fill -> take-profit -> assignment -> covered call ->
called away.  Run:  .venv\\Scripts\\python tests\\test_wheel_lifecycle.py
"""
import asyncio
import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hermes_trading import db  # noqa: E402

db.DB_FILE = Path(tempfile.mkdtemp()) / "wheel_test.db"
db.init_db()
from hermes_trading import wheel, wheel_book as book, wheel_screen as screen  # noqa: E402

FAILS = []


def ok(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def money(x):
    return f"${x:,.2f}"


cfg = book.load_config()
c = db.connect()

# a fake screen: two companies in different sectors, plus a same-sector duplicate
fake = {"date": "2026-09-27", "ts": 9e9, "funnel": {}, "excluded_downtrend": [], "candidates": [
    {"symbol": "BMY", "sector": "Health Care", "fcf_margin": .23, "fcf_yield": .089, "net_debt_to_fcf": 3.0, "why": "BMY why"},
    {"symbol": "ABC", "sector": "Health Care", "fcf_margin": .2, "fcf_yield": .06, "net_debt_to_fcf": 1, "why": "same sector"},
    {"symbol": "BF-B", "sector": "Consumer Staples", "fcf_margin": .236, "fcf_yield": .077, "net_debt_to_fcf": 2.3,
     "dividend_yield": float("nan"), "why": "BF-B why"}]}
screen.store(c, fake)
ok(json.dumps(screen.latest(c), allow_nan=False) is not None, "stored screen with a NaN field reads back as valid JSON")

S = {"BMY": 62.86, "ABC": 50.0, "BF-B": 26.16}


def fake_best(sym, kind, cfg_, s, min_strike=None):
    K = {"BMY": 60.0, "ABC": 45.0, "BF-B": 25.0}[sym] if kind == "put" else max(min_strike or 0, s * 1.05)
    return {"contract": f"{sym}-{kind}-{K}", "expiry": "2026-10-23", "dte": 26, "strike": K, "bid": .80, "ask": .94,
            "mid": .87, "fill": .835, "iv": .25, "delta": -.26 if kind == "put" else .25, "p_expire_worthless": .71,
            "open_interest": 276, "earnings": "2026-10-29"}


wheel.best_contract = fake_best
wheel.underlying_price = lambda s: S[s]
QUOTE = {"bid": .80, "ask": .94}
wheel.quote = lambda sym, e, con, k: {**QUOTE, "last": .87, "mid": (QUOTE["bid"] + QUOTE["ask"]) / 2}

print("1. proposals: two slots, one per sector, cash buffer respected")
asyncio.run(wheel._propose(c, cfg))
pend = book.pending(c)
ok([p["symbol"] for p in pend] == ["BMY", "BF-B"], f"proposed {[p['symbol'] for p in pend]} (ABC skipped: same sector as BMY)")
ok(abs(pend[0]["context"]["breakeven"] - (60 - .835)) < 1e-9,
   f"BMY context: breakeven {pend[0]['context']['breakeven']:.3f}, collateral {money(pend[0]['context']['collateral'])}")

print("2. approve + fill (sell at bid + 25% of spread, minus $0.65)")
for p in pend:
    wheel.decide(c, p["id"], "approve")
for p in book.pending(c, "approved"):
    asyncio.run(wheel._fill(c, cfg, p))
v = book.valuation(c, cfg)
exp_cash = 10000 + 2 * (0.835 * 100 - 0.65)
ok(abs(v["cash"] - exp_cash) < 1e-6, f"cash {money(v['cash'])} = 10,000 + 2 x ($83.50 - $0.65)")
ok(v["reserved"] == 8500, f"reserved collateral {money(v['reserved'])} (BMY $6,000 + BF-B $2,500)")
ok(abs(v["equity"] - (exp_cash - 2 * 83.5)) < 1e-6,
   f"equity {money(v['equity'])} (no live marks yet: short puts valued at their $0.835 sale price)")

print("3. automatic take-profit at 50% of premium")
QUOTE.update(bid=.36, ask=.42)
bmy_put = [p for p in book.open_positions(c) if p["symbol"] == "BMY"][0]
mk = {"ts": 1, "stocks": {}, "options": {bmy_put["contract"]: {"bid": .36, "ask": .42, "mid": .39}}}
wheel._take_profit(c, cfg, mk)
closed = c.execute("SELECT close_reason, close_price, pnl FROM opt_positions WHERE id=?", (bmy_put["id"],)).fetchone()
buy = .42 - .25 * .06
ok(closed["close_reason"] == "take_profit", f"BMY put bought back @ ${closed['close_price']:.3f}")
ok(abs(closed["pnl"] - ((.835 - buy) * 100 - 1.30)) < 1e-6, f"P&L {money(closed['pnl'])} includes both commissions")

print("4. expiry below the strike -> ASSIGNED")
bf = [p for p in book.open_positions(c) if p["symbol"] == "BF-B"][0]
c.execute("UPDATE opt_positions SET expiry='2026-09-18' WHERE id=?", (bf["id"],))
c.commit()
wheel.close_on = lambda sym, d: 23.10
cash_before = book.cash(c)
asyncio.run(wheel._expire(c, dt.datetime(2026, 9, 27, 12, tzinfo=wheel.ET)))
stock = book.open_positions(c, "stock")
ok(len(stock) == 1 and stock[0]["qty"] == 100 and stock[0]["open_price"] == 25.0, "bought 100 BF-B @ $25 (the strike)")
ok(abs(book.cash(c) - (cash_before - 2500)) < 1e-6, f"cash debited $2,500 -> {money(book.cash(c))}")
ok(abs(json.loads(stock[0]["context"])["effective_basis"] - (25 - .835)) < 1e-9, "effective basis = strike - premium")

print("5. covered call: only at/above cost basis, then CALLED AWAY")
S["BF-B"] = 24.0
asyncio.run(wheel._propose(c, cfg))
call = [p for p in book.pending(c) if p["action"] == "sell_call"][0]
ok(call["strike"] >= 25.0, f"call strike ${call['strike']:.2f} >= $25 basis even with the stock at $24")
wheel.decide(c, call["id"], "approve")
asyncio.run(wheel._fill(c, cfg, [p for p in book.pending(c, "approved") if p["action"] == "sell_call"][0]))
cc = book.open_positions(c, "short_call")[0]
c.execute("UPDATE opt_positions SET expiry='2026-09-25' WHERE id=?", (cc["id"],))
c.commit()
wheel.close_on = lambda sym, d: 27.0
cash_before = book.cash(c)
asyncio.run(wheel._expire(c, dt.datetime(2026, 9, 27, 12, tzinfo=wheel.ET)))
st = c.execute("SELECT status, close_reason FROM opt_positions WHERE kind='stock'").fetchone()
ok(st["status"] == "closed" and st["close_reason"] == "called_away", "shares called away at the call strike")
ok(abs(book.cash(c) - (cash_before + call["strike"] * 100)) < 1e-6, f"cash credited {money(call['strike'] * 100)}")

print("6. final book: everything closed, equity = cash, all P&L reconciles")
v = book.valuation(c, cfg)
realised = c.execute("SELECT SUM(pnl) FROM opt_positions WHERE status='closed'").fetchone()[0]
ok(not book.open_positions(c), "no open positions")
ok(abs(v["equity"] - (10000 + realised)) < 1e-6, f"equity {money(v['equity'])} = $10,000 + realised P&L {money(realised)}")

print(f"\n{'ALL PASSED' if not FAILS else str(len(FAILS)) + ' FAILED'}")
sys.exit(1 if FAILS else 0)
