"""Batterie massive de tests — trouve la meilleure combinaison."""

from datetime import datetime, timedelta
import ccxt
import pandas as pd
import ta
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()
TRADING_FEE = 0.001


def fetch(symbol, timeframe, months):
    exchange = ccxt.binance({"enableRateLimit": True})
    since = int((datetime.now() - timedelta(days=months * 30)).timestamp() * 1000)
    all_data = []
    while True:
        data = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
        if not data:
            break
        all_data.extend(data)
        since = data[-1][0] + 1
        if len(data) < 1000:
            break
    df = pd.DataFrame(all_data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df.drop_duplicates(subset=["timestamp"]).reset_index(drop=True)


def backtest_sma_rsi(df, capital, sma_s, sma_l, rsi_p, rsi_low, rsi_high, sl, tp, pos_pct, min_conf):
    close = df["close"]
    high = df["high"]
    low = df["low"]

    sma_short = ta.trend.sma_indicator(close, window=sma_s)
    sma_long = ta.trend.sma_indicator(close, window=sma_l)
    ema_short = ta.trend.ema_indicator(close, window=sma_s)
    ema_long = ta.trend.ema_indicator(close, window=sma_l)
    rsi = ta.momentum.rsi(close, window=rsi_p)
    macd_hist = ta.trend.macd_diff(close)
    bb_upper = ta.volatility.bollinger_hband(close)
    bb_lower = ta.volatility.bollinger_lband(close)

    initial = capital
    position = None
    wins = 0
    losses = 0
    start = max(sma_l, 30) + 5

    for i in range(start, len(df)):
        price = close.iloc[i]

        if position:
            pnl_pct = (price - position["price"]) / position["price"] * 100
            if pnl_pct <= -sl:
                capital += position["amount"] * price * (1 - TRADING_FEE)
                losses += 1
                position = None
                continue
            if pnl_pct >= tp:
                capital += position["amount"] * price * (1 - TRADING_FEE)
                wins += 1
                position = None
                continue

        buy_s = 0
        sell_s = 0
        if sma_short.iloc[i] > sma_long.iloc[i]: buy_s += 1
        else: sell_s += 1
        if ema_short.iloc[i] > ema_long.iloc[i]: buy_s += 1
        else: sell_s += 1
        if rsi.iloc[i] < rsi_low: buy_s += 1
        elif rsi.iloc[i] > rsi_high: sell_s += 1
        if macd_hist.iloc[i] > 0: buy_s += 1
        elif macd_hist.iloc[i] < 0: sell_s += 1
        if price <= bb_lower.iloc[i]: buy_s += 1
        elif price >= bb_upper.iloc[i]: sell_s += 1

        if buy_s >= min_conf and not position:
            invest = capital * (pos_pct / 100)
            position = {"price": price, "amount": invest * (1 - TRADING_FEE) / price}
            capital -= invest
        elif sell_s >= min_conf and position:
            rev = position["amount"] * price * (1 - TRADING_FEE)
            if rev > position["amount"] * position["price"]: wins += 1
            else: losses += 1
            capital += rev
            position = None

    if position:
        capital += position["amount"] * close.iloc[-1] * (1 - TRADING_FEE)

    roi = ((capital - initial) / initial) * 100
    total = wins + losses
    wr = (wins / total * 100) if total > 0 else 0
    return {"roi": round(roi, 3), "trades": total, "wins": wins, "losses": losses, "wr": round(wr, 1), "final": round(capital, 2)}


def backtest_grid(df, capital, levels, spacing):
    center = df["close"].iloc[len(df)//4]
    per_grid = (capital * 0.5) / (levels * 2)
    grids = []
    for i in range(-levels, levels+1):
        if i == 0: continue
        p = center * (1 + i * spacing / 100)
        grids.append({"price": p, "side": "buy" if i < 0 else "sell", "filled": False, "amount": per_grid / p, "level": i})

    initial = capital
    cash = capital
    holdings = 0
    trades = 0
    wins = 0

    for idx in range(len(df)):
        price = df["close"].iloc[idx]
        for g in grids:
            if g["filled"]: continue
            if g["side"] == "buy" and price <= g["price"]:
                cost = g["amount"] * price * (1 + TRADING_FEE)
                if cost <= cash:
                    cash -= cost
                    holdings += g["amount"]
                    g["filled"] = True
                    for g2 in grids:
                        if g2["level"] == -g["level"]: g2["filled"] = False
                    trades += 1
            elif g["side"] == "sell" and price >= g["price"] and holdings >= g["amount"]:
                rev = g["amount"] * price * (1 - TRADING_FEE)
                cash += rev
                holdings -= g["amount"]
                g["filled"] = True
                for g2 in grids:
                    if g2["level"] == -g["level"]: g2["filled"] = False
                trades += 1
                wins += 1

    final = cash + holdings * df["close"].iloc[-1]
    roi = ((final - initial) / initial) * 100
    return {"roi": round(roi, 3), "trades": trades, "wins": wins, "losses": trades - wins, "wr": round(wins/trades*100, 1) if trades > 0 else 0, "final": round(final, 2)}


def main():
    CAPITAL = 20
    SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "DOGE/USDT", "AVAX/USDT", "ADA/USDT"]
    MONTHS = [3, 6]
    TIMEFRAMES = ["1h", "4h"]

    # Charger toutes les données
    console.print(Panel("[bold cyan]MEGA TEST — Batterie complète[/bold cyan]", style="cyan"))
    data_cache = {}
    for sym in SYMBOLS:
        for tf in TIMEFRAMES:
            for m in MONTHS:
                key = f"{sym}_{tf}_{m}"
                try:
                    console.print(f"  Chargement {sym} {tf} {m}m...", end=" ")
                    data_cache[key] = fetch(sym, tf, m)
                    console.print(f"[green]✓[/green] {len(data_cache[key])} bougies")
                except Exception as e:
                    console.print(f"[red]✗ {e}[/red]")

    all_results = []

    # === TEST 1: Paramètres SMA/RSI ===
    console.print("\n[yellow]Phase 1: Test paramètres indicateurs...[/yellow]")
    param_combos = [
        {"sma_s": 5,  "sma_l": 20, "rsi_p": 7,  "rsi_low": 25, "rsi_high": 75, "sl": 1.5, "tp": 3, "pos": 10, "conf": 3},
        {"sma_s": 5,  "sma_l": 20, "rsi_p": 7,  "rsi_low": 30, "rsi_high": 70, "sl": 2,   "tp": 4, "pos": 5,  "conf": 3},
        {"sma_s": 7,  "sma_l": 25, "rsi_p": 10, "rsi_low": 25, "rsi_high": 75, "sl": 1.5, "tp": 2, "pos": 8,  "conf": 3},
        {"sma_s": 10, "sma_l": 30, "rsi_p": 14, "rsi_low": 30, "rsi_high": 70, "sl": 2,   "tp": 4, "pos": 5,  "conf": 3},
        {"sma_s": 10, "sma_l": 30, "rsi_p": 14, "rsi_low": 25, "rsi_high": 75, "sl": 3,   "tp": 5, "pos": 5,  "conf": 3},
        {"sma_s": 10, "sma_l": 30, "rsi_p": 14, "rsi_low": 35, "rsi_high": 65, "sl": 1.5, "tp": 3, "pos": 10, "conf": 2},
        {"sma_s": 10, "sma_l": 50, "rsi_p": 14, "rsi_low": 30, "rsi_high": 70, "sl": 3,   "tp": 6, "pos": 5,  "conf": 3},
        {"sma_s": 20, "sma_l": 50, "rsi_p": 14, "rsi_low": 30, "rsi_high": 70, "sl": 3,   "tp": 5, "pos": 5,  "conf": 3},
        {"sma_s": 20, "sma_l": 50, "rsi_p": 21, "rsi_low": 25, "rsi_high": 75, "sl": 4,   "tp": 8, "pos": 5,  "conf": 3},
        {"sma_s": 5,  "sma_l": 15, "rsi_p": 7,  "rsi_low": 20, "rsi_high": 80, "sl": 1,   "tp": 2, "pos": 15, "conf": 3},
    ]

    count = 0
    total = len(SYMBOLS) * len(TIMEFRAMES) * len(MONTHS) * len(param_combos)
    for sym in SYMBOLS:
        for tf in TIMEFRAMES:
            for m in MONTHS:
                key = f"{sym}_{tf}_{m}"
                if key not in data_cache: continue
                df = data_cache[key]
                for pi, p in enumerate(param_combos):
                    count += 1
                    if count % 50 == 0:
                        console.print(f"  Progression: {count}/{total}...")
                    try:
                        res = backtest_sma_rsi(df, CAPITAL, p["sma_s"], p["sma_l"], p["rsi_p"],
                                               p["rsi_low"], p["rsi_high"], p["sl"], p["tp"], p["pos"], p["conf"])
                        all_results.append({
                            "strategy": "Indicateurs",
                            "symbol": sym, "tf": tf, "months": m,
                            "params": f"SMA{p['sma_s']}/{p['sma_l']} RSI{p['rsi_p']}({p['rsi_low']}-{p['rsi_high']}) SL{p['sl']}% TP{p['tp']}% Pos{p['pos']}%",
                            **res
                        })
                    except: pass

    # === TEST 2: Grid Trading ===
    console.print("[yellow]Phase 2: Test Grid Trading...[/yellow]")
    grid_combos = [
        {"levels": 5,  "spacing": 0.3},
        {"levels": 5,  "spacing": 0.5},
        {"levels": 5,  "spacing": 1.0},
        {"levels": 10, "spacing": 0.3},
        {"levels": 10, "spacing": 0.5},
        {"levels": 10, "spacing": 1.0},
        {"levels": 15, "spacing": 0.5},
        {"levels": 20, "spacing": 0.3},
        {"levels": 20, "spacing": 0.5},
        {"levels": 20, "spacing": 1.0},
    ]

    for sym in SYMBOLS:
        for tf in ["1h"]:
            for m in MONTHS:
                key = f"{sym}_{tf}_{m}"
                if key not in data_cache: continue
                df = data_cache[key]
                for g in grid_combos:
                    try:
                        res = backtest_grid(df, CAPITAL, g["levels"], g["spacing"])
                        all_results.append({
                            "strategy": "Grid",
                            "symbol": sym, "tf": tf, "months": m,
                            "params": f"Lvl{g['levels']} Sp{g['spacing']}%",
                            **res
                        })
                    except: pass

    # === RÉSULTATS ===
    all_results.sort(key=lambda x: x["roi"], reverse=True)

    # Top 20
    console.print()
    top_table = Table(title="TOP 20 MEILLEURES COMBINAISONS", show_header=True, header_style="bold green")
    top_table.add_column("#", style="dim", width=3)
    top_table.add_column("Stratégie", width=12)
    top_table.add_column("Crypto", width=10)
    top_table.add_column("TF", width=4)
    top_table.add_column("Mois", width=5)
    top_table.add_column("ROI", justify="right", width=8)
    top_table.add_column("WR", justify="right", width=6)
    top_table.add_column("Trades", justify="right", width=7)
    top_table.add_column("Final", justify="right", width=8)
    top_table.add_column("Paramètres", width=45)

    for i, r in enumerate(all_results[:20], 1):
        roi_s = "green" if r["roi"] >= 0 else "red"
        top_table.add_row(
            str(i), r["strategy"], r["symbol"], r["tf"], str(r["months"]),
            f"[{roi_s}]{r['roi']:+.2f}%[/{roi_s}]",
            f"{r['wr']}%", str(r["trades"]),
            f"${r['final']}", r["params"]
        )
    console.print(top_table)

    # Worst 10
    console.print()
    worst_table = Table(title="10 PIRES COMBINAISONS", show_header=True, header_style="bold red")
    worst_table.add_column("#", style="dim", width=3)
    worst_table.add_column("Stratégie", width=12)
    worst_table.add_column("Crypto", width=10)
    worst_table.add_column("TF", width=4)
    worst_table.add_column("ROI", justify="right", width=8)
    worst_table.add_column("Paramètres", width=45)

    for i, r in enumerate(all_results[-10:], 1):
        worst_table.add_row(str(i), r["strategy"], r["symbol"], r["tf"], f"[red]{r['roi']:+.2f}%[/red]", r["params"])
    console.print(worst_table)

    # Stats par crypto
    console.print()
    crypto_stats = {}
    for r in all_results:
        sym = r["symbol"]
        if sym not in crypto_stats:
            crypto_stats[sym] = {"total": 0, "profitable": 0, "avg_roi": 0, "best": -999}
        crypto_stats[sym]["total"] += 1
        crypto_stats[sym]["avg_roi"] += r["roi"]
        if r["roi"] > 0: crypto_stats[sym]["profitable"] += 1
        if r["roi"] > crypto_stats[sym]["best"]: crypto_stats[sym]["best"] = r["roi"]

    crypto_table = Table(title="RÉSUMÉ PAR CRYPTO", show_header=True, header_style="bold cyan")
    crypto_table.add_column("Crypto", width=12)
    crypto_table.add_column("Tests", justify="right", width=7)
    crypto_table.add_column("Rentables", justify="right", width=10)
    crypto_table.add_column("% Rentable", justify="right", width=10)
    crypto_table.add_column("ROI Moyen", justify="right", width=10)
    crypto_table.add_column("Meilleur ROI", justify="right", width=12)

    for sym in sorted(crypto_stats, key=lambda s: crypto_stats[s]["best"], reverse=True):
        s = crypto_stats[sym]
        avg = s["avg_roi"] / s["total"]
        pct = s["profitable"] / s["total"] * 100
        avg_s = "green" if avg >= 0 else "red"
        best_s = "green" if s["best"] >= 0 else "red"
        crypto_table.add_row(
            sym, str(s["total"]), str(s["profitable"]), f"{pct:.0f}%",
            f"[{avg_s}]{avg:+.2f}%[/{avg_s}]", f"[{best_s}]{s['best']:+.2f}%[/{best_s}]"
        )
    console.print(crypto_table)

    # Stats par stratégie
    console.print()
    strat_stats = {}
    for r in all_results:
        st = r["strategy"]
        if st not in strat_stats:
            strat_stats[st] = {"total": 0, "profitable": 0, "avg_roi": 0, "best": -999}
        strat_stats[st]["total"] += 1
        strat_stats[st]["avg_roi"] += r["roi"]
        if r["roi"] > 0: strat_stats[st]["profitable"] += 1
        if r["roi"] > strat_stats[st]["best"]: strat_stats[st]["best"] = r["roi"]

    strat_table = Table(title="RÉSUMÉ PAR TYPE DE STRATÉGIE", show_header=True, header_style="bold yellow")
    strat_table.add_column("Stratégie", width=15)
    strat_table.add_column("Tests", justify="right", width=7)
    strat_table.add_column("Rentables", justify="right", width=10)
    strat_table.add_column("% Rentable", justify="right", width=10)
    strat_table.add_column("ROI Moyen", justify="right", width=10)
    strat_table.add_column("Meilleur ROI", justify="right", width=12)

    for st in sorted(strat_stats, key=lambda s: strat_stats[s]["best"], reverse=True):
        s = strat_stats[st]
        avg = s["avg_roi"] / s["total"]
        pct = s["profitable"] / s["total"] * 100
        avg_s = "green" if avg >= 0 else "red"
        best_s = "green" if s["best"] >= 0 else "red"
        strat_table.add_row(
            st, str(s["total"]), str(s["profitable"]), f"{pct:.0f}%",
            f"[{avg_s}]{avg:+.2f}%[/{avg_s}]", f"[{best_s}]{s['best']:+.2f}%[/{best_s}]"
        )
    console.print(strat_table)

    # Verdict
    best = all_results[0]
    profitable = sum(1 for r in all_results if r["roi"] > 0)
    console.print()
    console.print(Panel(
        f"[bold]Tests totaux: {len(all_results)} | Rentables: {profitable} ({profitable/len(all_results)*100:.0f}%)[/bold]\n\n"
        f"[bold cyan]Meilleure combinaison:[/bold cyan]\n"
        f"  Stratégie: [bold]{best['strategy']}[/bold]\n"
        f"  Crypto: [bold]{best['symbol']}[/bold] ({best['tf']}, {best['months']}m)\n"
        f"  ROI: [bold green]{best['roi']:+.2f}%[/bold green] | Win Rate: {best['wr']}% | Trades: {best['trades']}\n"
        f"  Paramètres: {best['params']}\n"
        f"  Capital: $20 → ${best['final']}",
        title="VERDICT FINAL", border_style="green" if best["roi"] > 0 else "yellow",
    ))


if __name__ == "__main__":
    main()
