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

console = Console()

TRADING_FEE = 0.001  # 0.1% par trade (Binance)


def fetch_historical_data(symbol, timeframe, months):
    """Télécharge les données historiques depuis Binance."""
    console.print(f"\n[cyan]Téléchargement des données historiques...[/cyan]")
    console.print(f"  Paire: {symbol} | Timeframe: {timeframe} | Période: {months} mois\n")

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

    console.print(f"  [green]✓[/green] {len(df)} bougies téléchargées")
    console.print(f"  Du {df['timestamp'].iloc[0].strftime('%Y-%m-%d')} au {df['timestamp'].iloc[-1].strftime('%Y-%m-%d')}\n")
    return df


def run_backtest(df, capital, stop_loss_pct, take_profit_pct, position_size_pct):
    """Simule la stratégie sur les données historiques."""
    strategy = SmaRsiStrategy()

    initial_capital = capital
    position = None  # {"price": ..., "amount": ..., "time": ...}
    trades = []
    equity_curve = []
    min_bars = max(config.SMA_LONG_PERIOD, config.MACD_SLOW, config.BB_PERIOD) + 5

    console.print(f"[cyan]Simulation en cours...[/cyan]\n")

    for i in range(min_bars, len(df)):
        window = df.iloc[:i + 1].copy()
        current_price = window["close"].iloc[-1]
        current_time = window["timestamp"].iloc[-1]

        signal, indicators = strategy.evaluate(window)

        # Vérifier stop-loss / take-profit sur position ouverte
        if position is not None:
            pnl_pct = (current_price - position["price"]) / position["price"] * 100

            # Stop-loss
            if pnl_pct <= -stop_loss_pct:
                sell_value = position["amount"] * current_price * (1 - TRADING_FEE)
                pnl = sell_value - (position["amount"] * position["price"])
                capital += sell_value
                trades.append({
                    "entry_time": position["time"],
                    "exit_time": current_time,
                    "entry_price": position["price"],
                    "exit_price": current_price,
                    "amount": position["amount"],
                    "pnl": round(pnl, 4),
                    "pnl_pct": round(pnl_pct, 2),
                    "reason": "STOP-LOSS",
                    "result": "LOSS",
                })
                position = None
                continue

            # Take-profit
            if pnl_pct >= take_profit_pct:
                sell_value = position["amount"] * current_price * (1 - TRADING_FEE)
                pnl = sell_value - (position["amount"] * position["price"])
                capital += sell_value
                trades.append({
                    "entry_time": position["time"],
                    "exit_time": current_time,
                    "entry_price": position["price"],
                    "exit_price": current_price,
                    "amount": position["amount"],
                    "pnl": round(pnl, 4),
                    "pnl_pct": round(pnl_pct, 2),
                    "reason": "TAKE-PROFIT",
                    "result": "WIN",
                })
                position = None
                continue

        # Signal BUY
        if signal == "BUY" and position is None:
            invest = capital * (position_size_pct / 100)
            invest_after_fee = invest * (1 - TRADING_FEE)
            amount = invest_after_fee / current_price
            capital -= invest
            position = {"price": current_price, "amount": amount, "time": current_time}

        # Signal SELL
        elif signal == "SELL" and position is not None:
            sell_value = position["amount"] * current_price * (1 - TRADING_FEE)
            pnl = sell_value - (position["amount"] * position["price"])
            pnl_pct = (current_price - position["price"]) / position["price"] * 100
            capital += sell_value
            trades.append({
                "entry_time": position["time"],
                "exit_time": current_time,
                "entry_price": position["price"],
                "exit_price": current_price,
                "amount": position["amount"],
                "pnl": round(pnl, 4),
                "pnl_pct": round(pnl_pct, 2),
                "reason": "SIGNAL",
                "result": "WIN" if pnl > 0 else "LOSS",
            })
            position = None

        # Equity = capital + valeur position ouverte
        equity = capital
        if position is not None:
            equity += position["amount"] * current_price
        equity_curve.append({"time": current_time, "equity": equity})

    # Fermer position ouverte à la fin
    if position is not None:
        final_price = df["close"].iloc[-1]
        sell_value = position["amount"] * final_price * (1 - TRADING_FEE)
        pnl = sell_value - (position["amount"] * position["price"])
        pnl_pct = (final_price - position["price"]) / position["price"] * 100
        capital += sell_value
        trades.append({
            "entry_time": position["time"],
            "exit_time": df["timestamp"].iloc[-1],
            "entry_price": position["price"],
            "exit_price": final_price,
            "amount": position["amount"],
            "pnl": round(pnl, 4),
            "pnl_pct": round(pnl_pct, 2),
            "reason": "FIN BACKTEST",
            "result": "WIN" if pnl > 0 else "LOSS",
        })

    return trades, equity_curve, capital, initial_capital


def draw_equity_chart(equity_curve, width=60, height=15):
    """Dessine un graphique ASCII de l'évolution du capital."""
    if not equity_curve:
        return

    # Échantillonner les points pour la largeur
    step = max(1, len(equity_curve) // width)
    points = [equity_curve[i]["equity"] for i in range(0, len(equity_curve), step)]

    min_val = min(points)
    max_val = max(points)
    value_range = max_val - min_val if max_val != min_val else 1

    chart_lines = []
    for row in range(height, -1, -1):
        threshold = min_val + (row / height) * value_range
        line = ""
        for val in points[:width]:
            if val >= threshold:
                line += "█"
            else:
                line += " "

        # Label à gauche
        if row == height:
            label = f"${max_val:>8.2f} │"
        elif row == 0:
            label = f"${min_val:>8.2f} │"
        elif row == height // 2:
            mid = (max_val + min_val) / 2
            label = f"${mid:>8.2f} │"
        else:
            label = f"          │"

        chart_lines.append(label + line)

    chart_lines.append("          └" + "─" * width)

    chart_text = "\n".join(chart_lines)
    console.print(Panel(chart_text, title="[bold cyan]Évolution du Capital[/bold cyan]", border_style="cyan"))


def print_report(trades, equity_curve, final_capital, initial_capital, symbol, months):
    """Affiche le rapport de backtest."""
    console.print()
    header = Text()
    header.append("RAPPORT DE BACKTEST", style="bold cyan")
    header.append(f"  |  {symbol}  |  {months} mois", style="dim")
    console.print(Panel(header, style="cyan"))

    # Stats globales
    total_trades = len(trades)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    total_pnl = sum(t["pnl"] for t in trades)
    total_wins = sum(t["pnl"] for t in wins) if wins else 0
    total_losses = abs(sum(t["pnl"] for t in losses)) if losses else 0
    win_rate = (len(wins) / total_trades * 100) if total_trades > 0 else 0
    profit_factor = (total_wins / total_losses) if total_losses > 0 else float("inf")
    roi = ((final_capital - initial_capital) / initial_capital) * 100

    # Max drawdown
    max_drawdown = 0
    peak = initial_capital
    for point in equity_curve:
        if point["equity"] > peak:
            peak = point["equity"]
        dd = (peak - point["equity"]) / peak * 100
        if dd > max_drawdown:
            max_drawdown = dd

    stats_table = Table(title="Résultats", show_header=True, header_style="bold magenta")
    stats_table.add_column("Métrique", style="cyan", width=25)
    stats_table.add_column("Valeur", justify="right", width=20)

    pnl_style = "green" if total_pnl >= 0 else "red"
    roi_style = "green" if roi >= 0 else "red"

    stats_table.add_row("Capital initial", f"${initial_capital:.2f}")
    stats_table.add_row("Capital final", f"[{pnl_style}]${final_capital:.2f}[/{pnl_style}]")
    stats_table.add_row("P&L Total", f"[{pnl_style}]{total_pnl:+.4f} $[/{pnl_style}]")
    stats_table.add_row("ROI", f"[{roi_style}]{roi:+.2f}%[/{roi_style}]")
    stats_table.add_row("", "")
    stats_table.add_row("Trades totaux", str(total_trades))
    stats_table.add_row("Wins", f"[green]{len(wins)}[/green]")
    stats_table.add_row("Losses", f"[red]{len(losses)}[/red]")
    stats_table.add_row("Win Rate", f"{win_rate:.1f}%")
    stats_table.add_row("Profit Factor", f"{profit_factor:.2f}")
    stats_table.add_row("", "")
    stats_table.add_row("Gain moyen", f"[green]+{total_wins/len(wins):.4f} $[/green]" if wins else "N/A")
    stats_table.add_row("Perte moyenne", f"[red]-{total_losses/len(losses):.4f} $[/red]" if losses else "N/A")
    stats_table.add_row("Max Drawdown", f"[red]{max_drawdown:.2f}%[/red]")
    stats_table.add_row("Frais de trading", f"0.1% par trade")

    console.print(stats_table)
    console.print()

    # Graphique
    draw_equity_chart(equity_curve)
    console.print()

    # Liste des trades
    if trades:
        trades_table = Table(title=f"Détail des Trades ({len(trades)})", show_header=True, header_style="bold green")
        trades_table.add_column("#", style="dim", width=4)
        trades_table.add_column("Entrée", width=12)
        trades_table.add_column("Sortie", width=12)
        trades_table.add_column("Prix Entrée", justify="right", width=12)
        trades_table.add_column("Prix Sortie", justify="right", width=12)
        trades_table.add_column("P&L", justify="right", width=12)
        trades_table.add_column("P&L %", justify="right", width=8)
        trades_table.add_column("Raison", width=12)
        trades_table.add_column("Résultat", justify="center", width=8)

        for i, t in enumerate(trades, 1):
            pnl_s = "green" if t["pnl"] > 0 else "red"
            res_s = "green" if t["result"] == "WIN" else "red"
            entry_date = t["entry_time"].strftime("%m-%d %H:%M") if hasattr(t["entry_time"], "strftime") else str(t["entry_time"])[:12]
            exit_date = t["exit_time"].strftime("%m-%d %H:%M") if hasattr(t["exit_time"], "strftime") else str(t["exit_time"])[:12]

            trades_table.add_row(
                str(i),
                entry_date,
                exit_date,
                f"${t['entry_price']:,.2f}",
                f"${t['exit_price']:,.2f}",
                f"[{pnl_s}]{t['pnl']:+.4f}[/{pnl_s}]",
                f"[{pnl_s}]{t['pnl_pct']:+.1f}%[/{pnl_s}]",
                t["reason"],
                f"[{res_s}]{t['result']}[/{res_s}]",
            )

        console.print(trades_table)

    # Verdict
    console.print()
    if roi > 0:
        console.print(Panel(
            f"[bold green]La stratégie aurait été RENTABLE sur cette période : {roi:+.2f}% de ROI[/bold green]\n"
            f"Capital: ${initial_capital:.2f} → ${final_capital:.2f}",
            title="VERDICT", border_style="green",
        ))
    else:
        console.print(Panel(
            f"[bold red]La stratégie aurait été PERDANTE sur cette période : {roi:+.2f}% de ROI[/bold red]\n"
            f"Capital: ${initial_capital:.2f} → ${final_capital:.2f}",
            title="VERDICT", border_style="red",
        ))


def main():
    parser = argparse.ArgumentParser(description="Backtester — Teste la stratégie sur données historiques")
    parser.add_argument("--capital", type=float, default=10, help="Capital de départ en $ (défaut: 10)")
    parser.add_argument("--months", type=int, default=6, help="Période en mois (défaut: 6)")
    parser.add_argument("--symbol", type=str, default=None, help="Paire de trading (défaut: config)")
    parser.add_argument("--timeframe", type=str, default=None, help="Timeframe (défaut: config)")
    args = parser.parse_args()

    symbol = args.symbol or config.SYMBOL
    timeframe = args.timeframe or config.TIMEFRAME

    # Télécharger les données
    df = fetch_historical_data(symbol, timeframe, args.months)

    # Lancer le backtest
    trades, equity_curve, final_capital, initial_capital = run_backtest(
        df,
        capital=args.capital,
        stop_loss_pct=config.STOP_LOSS_PCT,
        take_profit_pct=config.TAKE_PROFIT_PCT,
        position_size_pct=config.POSITION_SIZE_PCT,
    )

    # Afficher le rapport
    print_report(trades, equity_curve, final_capital, initial_capital, symbol, args.months)


if __name__ == "__main__":
    main()
