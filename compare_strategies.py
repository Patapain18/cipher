"""
Comparateur de Stratégies — Teste toutes les stratégies côte à côte.

Lance un backtest pour chaque stratégie et affiche un tableau comparatif.
"""

import argparse
from datetime import datetime, timedelta

import ccxt
import pandas as pd
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

import config
from strategy import SmaRsiStrategy
from scalping_strategy import ScalpingStrategy
from grid_strategy import GridStrategy

console = Console()
TRADING_FEE = 0.001


def fetch_data(symbol, timeframe, months):
    console.print(f"[cyan]Téléchargement {symbol} ({timeframe}, {months}m)...[/cyan]")
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
    df = df.drop_duplicates(subset=["timestamp"]).reset_index(drop=True)
    console.print(f"  [green]✓[/green] {len(df)} bougies\n")
    return df


def backtest_indicators(df, capital, sl, tp, pos_pct):
    """Backtest stratégie SMA+RSI+MACD+BB."""
    strategy = SmaRsiStrategy()
    position = None
    trades = []
    min_bars = max(config.SMA_LONG_PERIOD, config.MACD_SLOW, config.BB_PERIOD) + 5

    for i in range(min_bars, len(df)):
        window = df.iloc[:i + 1].copy()
        price = window["close"].iloc[-1]
        time = window["timestamp"].iloc[-1]
        signal, _ = strategy.evaluate(window)

        if position:
            pnl_pct = (price - position["price"]) / position["price"] * 100
            if pnl_pct <= -sl:
                sell_val = position["amount"] * price * (1 - TRADING_FEE)
                trades.append({"pnl": sell_val - position["cost"], "result": "LOSS"})
                capital += sell_val
                position = None
                continue
            if pnl_pct >= tp:
                sell_val = position["amount"] * price * (1 - TRADING_FEE)
                trades.append({"pnl": sell_val - position["cost"], "result": "WIN"})
                capital += sell_val
                position = None
                continue

        if signal == "BUY" and not position:
            invest = capital * (pos_pct / 100)
            amount = invest * (1 - TRADING_FEE) / price
            capital -= invest
            position = {"price": price, "amount": amount, "cost": invest}
        elif signal == "SELL" and position:
            sell_val = position["amount"] * price * (1 - TRADING_FEE)
            trades.append({"pnl": sell_val - position["cost"], "result": "WIN" if sell_val > position["cost"] else "LOSS"})
            capital += sell_val
            position = None

    if position:
        sell_val = position["amount"] * df["close"].iloc[-1] * (1 - TRADING_FEE)
        trades.append({"pnl": sell_val - position["cost"], "result": "WIN" if sell_val > position["cost"] else "LOSS"})
        capital += sell_val

    return trades, capital


def backtest_scalping(df, capital, pos_pct):
    """Backtest stratégie scalping — SL 0.5%, TP 0.3%."""
    strategy = ScalpingStrategy()
    position = None
    trades = []
    SL, TP = 0.5, 0.3

    for i in range(30, len(df)):
        window = df.iloc[:i + 1].copy()
        price = window["close"].iloc[-1]
        signal, _ = strategy.evaluate(window)

        if position:
            pnl_pct = (price - position["price"]) / position["price"] * 100
            if pnl_pct <= -SL:
                sell_val = position["amount"] * price * (1 - TRADING_FEE)
                trades.append({"pnl": sell_val - position["cost"], "result": "LOSS"})
                capital += sell_val
                position = None
                continue
            if pnl_pct >= TP:
                sell_val = position["amount"] * price * (1 - TRADING_FEE)
                trades.append({"pnl": sell_val - position["cost"], "result": "WIN"})
                capital += sell_val
                position = None
                continue

        if signal == "BUY" and not position:
            invest = capital * (pos_pct / 100)
            amount = invest * (1 - TRADING_FEE) / price
            capital -= invest
            position = {"price": price, "amount": amount, "cost": invest}
        elif signal == "SELL" and position:
            sell_val = position["amount"] * price * (1 - TRADING_FEE)
            trades.append({"pnl": sell_val - position["cost"], "result": "WIN" if sell_val > position["cost"] else "LOSS"})
            capital += sell_val
            position = None

    if position:
        sell_val = position["amount"] * df["close"].iloc[-1] * (1 - TRADING_FEE)
        trades.append({"pnl": sell_val - position["cost"], "result": "WIN" if sell_val > position["cost"] else "LOSS"})
        capital += sell_val

    return trades, capital


def backtest_grid(df, capital, grid_levels=10, grid_spacing=0.5):
    """Backtest stratégie grid trading."""
    grid = GridStrategy(grid_levels=grid_levels, grid_spacing_pct=grid_spacing)

    center_price = df["close"].iloc[len(df) // 4]  # Prix au 1er quart
    grid.setup_grid(center_price, capital * 0.5)  # 50% du capital dans la grille

    trades = []
    holdings = 0  # Crypto détenue
    cash = capital

    for i in range(len(df)):
        price = df["close"].iloc[i]
        actions = grid.evaluate(price)

        for action in actions:
            if action["action"] == "BUY" and cash > 0:
                cost = action["amount"] * price * (1 + TRADING_FEE)
                if cost <= cash:
                    cash -= cost
                    holdings += action["amount"]
                    trades.append({"action": "BUY", "price": price, "pnl": 0, "result": "BUY"})

            elif action["action"] == "SELL" and holdings >= action["amount"]:
                revenue = action["amount"] * price * (1 - TRADING_FEE)
                cash += revenue
                holdings -= action["amount"]
                pnl = revenue - (action["amount"] * action["price"])
                trades.append({"action": "SELL", "price": price, "pnl": pnl, "result": "WIN" if pnl > 0 else "LOSS"})

    final_capital = cash + holdings * df["close"].iloc[-1]
    return trades, final_capital


def backtest_multi_pair(symbols, months, capital, sl, tp, pos_pct, timeframe="1h"):
    """Backtest multi-paires — rotate vers la meilleure paire."""
    from multi_pair_scanner import MultiPairScanner

    # Charger toutes les données
    all_data = {}
    for sym in symbols:
        try:
            all_data[sym] = fetch_data(sym, timeframe, months)
        except:
            pass

    if not all_data:
        return [], capital

    # Trouver la longueur min commune
    min_len = min(len(df) for df in all_data.values())
    for sym in all_data:
        all_data[sym] = all_data[sym].iloc[:min_len].copy()

    strategy = SmaRsiStrategy()
    scanner = MultiPairScanner(pairs=list(all_data.keys()), min_score=4)
    position = None
    trades = []
    min_bars = 50
    check_interval = 24  # Re-scanner toutes les 24 bougies

    for i in range(min_bars, min_len):
        # Scanner les paires périodiquement
        if i % check_interval == 0 and not position:
            dataframes = {sym: all_data[sym].iloc[:i + 1] for sym in all_data}
            rankings = scanner.scan_from_dataframes(dataframes)
            best = rankings[0] if rankings and rankings[0]["score"] >= 4 else None

            if best:
                sym = best["symbol"]
                price = best["price"]
                invest = capital * (pos_pct / 100)
                amount = invest * (1 - TRADING_FEE) / price
                capital -= invest
                position = {"symbol": sym, "price": price, "amount": amount, "cost": invest, "bar": i}

        # Gérer la position ouverte
        if position:
            sym = position["symbol"]
            price = all_data[sym]["close"].iloc[i]
            pnl_pct = (price - position["price"]) / position["price"] * 100
            bars_held = i - position["bar"]

            if pnl_pct <= -sl or pnl_pct >= tp or bars_held >= 48:
                sell_val = position["amount"] * price * (1 - TRADING_FEE)
                pnl = sell_val - position["cost"]
                trades.append({
                    "symbol": sym, "pnl": pnl,
                    "result": "WIN" if pnl > 0 else "LOSS",
                })
                capital += sell_val
                position = None

    if position:
        price = all_data[position["symbol"]]["close"].iloc[-1]
        sell_val = position["amount"] * price * (1 - TRADING_FEE)
        trades.append({"pnl": sell_val - position["cost"], "result": "WIN" if sell_val > position["cost"] else "LOSS"})
        capital += sell_val

    return trades, capital


def compute_stats(trades, initial, final):
    wins = [t for t in trades if t.get("pnl", 0) > 0]
    losses = [t for t in trades if t.get("pnl", 0) <= 0 and t.get("result") != "BUY"]
    total_pnl = sum(t.get("pnl", 0) for t in trades)
    roi = ((final - initial) / initial) * 100
    win_rate = (len(wins) / (len(wins) + len(losses)) * 100) if (len(wins) + len(losses)) > 0 else 0
    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "pnl": round(total_pnl, 4),
        "roi": round(roi, 2),
        "final": round(final, 2),
    }


def main():
    parser = argparse.ArgumentParser(description="Comparateur de Stratégies")
    parser.add_argument("--capital", type=float, default=20)
    parser.add_argument("--months", type=int, default=6)
    parser.add_argument("--symbol", type=str, default="BTC/USDT")
    args = parser.parse_args()

    initial = args.capital
    symbol = args.symbol

    console.print(Panel(
        Text("COMPARATEUR DE STRATÉGIES", style="bold cyan"),
        subtitle=f"{symbol} | {args.months} mois | Capital: ${initial}",
        style="cyan",
    ))

    # Télécharger les données
    df_1h = fetch_data(symbol, "1h", args.months)
    df_5m = fetch_data(symbol, "5m", min(args.months, 1))  # 5min = max 1 mois sur Binance

    results = {}

    # 1. Indicateurs classiques
    console.print("[yellow]1/4 Backtest Indicateurs (SMA+RSI+MACD+BB)...[/yellow]")
    trades, final = backtest_indicators(df_1h, initial, config.STOP_LOSS_PCT, config.TAKE_PROFIT_PCT, config.POSITION_SIZE_PCT)
    results["Indicateurs"] = compute_stats(trades, initial, final)

    # 2. Scalping
    console.print("[yellow]2/4 Backtest Scalping (5min)...[/yellow]")
    trades, final = backtest_scalping(df_5m, initial, 10)
    results["Scalping"] = compute_stats(trades, initial, final)

    # 3. Grid Trading
    console.print("[yellow]3/4 Backtest Grid Trading...[/yellow]")
    trades, final = backtest_grid(df_1h, initial, grid_levels=10, grid_spacing=0.5)
    results["Grid Trading"] = compute_stats(trades, initial, final)

    # 4. Multi-paires
    console.print("[yellow]4/4 Backtest Multi-Paires...[/yellow]")
    multi_symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"]
    trades, final = backtest_multi_pair(multi_symbols, args.months, initial, config.STOP_LOSS_PCT, config.TAKE_PROFIT_PCT, config.POSITION_SIZE_PCT)
    results["Multi-Paires"] = compute_stats(trades, initial, final)

    # Tableau comparatif
    console.print()
    table = Table(title="COMPARAISON DES STRATÉGIES", show_header=True, header_style="bold cyan")
    table.add_column("Stratégie", style="bold white", width=16)
    table.add_column("ROI", justify="right", width=10)
    table.add_column("P&L", justify="right", width=12)
    table.add_column("Trades", justify="right", width=8)
    table.add_column("Win Rate", justify="right", width=10)
    table.add_column("Capital Final", justify="right", width=14)
    table.add_column("Verdict", justify="center", width=10)

    best_roi = max(r["roi"] for r in results.values())

    for name, stats in results.items():
        roi_style = "green" if stats["roi"] >= 0 else "red"
        pnl_style = "green" if stats["pnl"] >= 0 else "red"
        is_best = stats["roi"] == best_roi
        verdict = "[bold green]★ BEST[/bold green]" if is_best else ("[green]OK[/green]" if stats["roi"] >= 0 else "[red]✗[/red]")

        table.add_row(
            f"[bold]{'→ ' if is_best else '  '}{name}[/bold]",
            f"[{roi_style}]{stats['roi']:+.2f}%[/{roi_style}]",
            f"[{pnl_style}]{stats['pnl']:+.4f} $[/{pnl_style}]",
            str(stats["trades"]),
            f"{stats['win_rate']}%",
            f"${stats['final']:.2f}",
            verdict,
        )

    console.print(table)

    # Meilleure stratégie
    best_name = max(results, key=lambda k: results[k]["roi"])
    best = results[best_name]
    console.print()

    if best["roi"] > 0:
        console.print(Panel(
            f"[bold green]Meilleure stratégie: {best_name}[/bold green]\n"
            f"ROI: {best['roi']:+.2f}% | Win Rate: {best['win_rate']}% | Capital: ${initial} → ${best['final']:.2f}",
            title="GAGNANT", border_style="green",
        ))
    else:
        console.print(Panel(
            f"[bold yellow]Moins pire: {best_name}[/bold yellow]\n"
            f"ROI: {best['roi']:+.2f}% | Win Rate: {best['win_rate']}% | Capital: ${initial} → ${best['final']:.2f}\n\n"
            f"[dim]Aucune stratégie n'est rentable sur cette période.[/dim]",
            title="RÉSULTAT", border_style="yellow",
        ))


if __name__ == "__main__":
    main()
