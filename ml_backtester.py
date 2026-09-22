import argparse
from datetime import datetime, timedelta

import ccxt
import pandas as pd
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

import config
from ml_strategy import MLStrategy

console = Console()

TRADING_FEE = 0.001


def fetch_historical_data(symbol, timeframe, months):
    """Télécharge les données historiques."""
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


def run_ml_backtest(df, capital, stop_loss_pct, take_profit_pct, position_size_pct, model_type, lookahead):
    """Backtest avec stratégie ML."""

    # Séparer: 70% entraînement, 30% test
    train_size = int(len(df) * 0.7)
    train_df = df.iloc[:train_size].copy()
    test_df = df.iloc[train_size:].copy()

    console.print(f"[cyan]Données d'entraînement:[/cyan] {len(train_df)} bougies ({train_df['timestamp'].iloc[0].strftime('%Y-%m-%d')} → {train_df['timestamp'].iloc[-1].strftime('%Y-%m-%d')})")
    console.print(f"[cyan]Données de test:[/cyan]         {len(test_df)} bougies ({test_df['timestamp'].iloc[0].strftime('%Y-%m-%d')} → {test_df['timestamp'].iloc[-1].strftime('%Y-%m-%d')})\n")

    # Entraîner le modèle
    strategy = MLStrategy(model_type=model_type, lookahead=lookahead)
    console.print("[yellow]Entraînement du modèle ML...[/yellow]\n")
    train_stats = strategy.train(train_df)

    # Afficher les stats d'entraînement
    train_table = Table(title="Résultats d'entraînement", show_header=True, header_style="bold yellow")
    train_table.add_column("Métrique", style="cyan")
    train_table.add_column("Valeur", justify="right")
    train_table.add_row("Modèle", model_type)
    train_table.add_row("Accuracy (train)", f"{train_stats['train_accuracy']*100:.1f}%")
    train_table.add_row("Accuracy (test)", f"{train_stats['test_accuracy']*100:.1f}%")
    train_table.add_row("Échantillons train", str(train_stats['train_samples']))
    train_table.add_row("Échantillons test", str(train_stats['test_samples']))
    train_table.add_row("Signaux BUY (train)", str(train_stats['buy_signals_train']))
    train_table.add_row("Signaux BUY (test)", str(train_stats['buy_signals_test']))
    console.print(train_table)
    console.print()

    # Feature importance
    feat_table = Table(title="Top 10 Features", show_header=True, header_style="bold magenta")
    feat_table.add_column("Feature", style="cyan")
    feat_table.add_column("Importance", justify="right")
    for name, imp in train_stats["top_features"]:
        bar = "█" * int(imp * 100)
        feat_table.add_row(name, f"{imp:.4f} {bar}")
    console.print(feat_table)
    console.print()

    # Simuler les trades sur les données de test
    console.print("[yellow]Simulation sur données de test...[/yellow]\n")

    initial_capital = capital
    position = None
    trades = []
    equity_curve = []
    min_window = 60  # Minimum de bougies pour calculer les features

    for i in range(min_window, len(test_df)):
        # Fenêtre glissante incluant assez d'historique
        window_start = max(0, i - 200)
        window = test_df.iloc[window_start:i + 1].copy()
        current_price = window["close"].iloc[-1]
        current_time = window["timestamp"].iloc[-1]

        signal, indicators = strategy.predict(window)

        # Stop-loss / Take-profit
        if position is not None:
            pnl_pct = (current_price - position["price"]) / position["price"] * 100

            if pnl_pct <= -stop_loss_pct:
                sell_value = position["amount"] * current_price * (1 - TRADING_FEE)
                pnl = sell_value - (position["amount"] * position["price"])
                capital += sell_value
                trades.append({
                    "entry_time": position["time"], "exit_time": current_time,
                    "entry_price": position["price"], "exit_price": current_price,
                    "amount": position["amount"], "pnl": round(pnl, 4),
                    "pnl_pct": round(pnl_pct, 2), "reason": "STOP-LOSS",
                    "result": "LOSS", "probability": position.get("probability", 0),
                })
                position = None
                continue

            if pnl_pct >= take_profit_pct:
                sell_value = position["amount"] * current_price * (1 - TRADING_FEE)
                pnl = sell_value - (position["amount"] * position["price"])
                capital += sell_value
                trades.append({
                    "entry_time": position["time"], "exit_time": current_time,
                    "entry_price": position["price"], "exit_price": current_price,
                    "amount": position["amount"], "pnl": round(pnl, 4),
                    "pnl_pct": round(pnl_pct, 2), "reason": "TAKE-PROFIT",
                    "result": "WIN", "probability": position.get("probability", 0),
                })
                position = None
                continue

        # Signal BUY
        if signal == "BUY" and position is None:
            invest = capital * (position_size_pct / 100)
            invest_after_fee = invest * (1 - TRADING_FEE)
            amount = invest_after_fee / current_price
            capital -= invest
            position = {
                "price": current_price, "amount": amount,
                "time": current_time, "probability": indicators.get("probability", 0),
            }

        # Signal SELL
        elif signal == "SELL" and position is not None:
            sell_value = position["amount"] * current_price * (1 - TRADING_FEE)
            pnl = sell_value - (position["amount"] * position["price"])
            pnl_pct = (current_price - position["price"]) / position["price"] * 100
            capital += sell_value
            trades.append({
                "entry_time": position["time"], "exit_time": current_time,
                "entry_price": position["price"], "exit_price": current_price,
                "amount": position["amount"], "pnl": round(pnl, 4),
                "pnl_pct": round(pnl_pct, 2), "reason": "ML-SIGNAL",
                "result": "WIN" if pnl > 0 else "LOSS",
                "probability": position.get("probability", 0),
            })
            position = None

        equity = capital
        if position is not None:
            equity += position["amount"] * current_price
        equity_curve.append({"time": current_time, "equity": equity})

    # Fermer position ouverte
    if position is not None:
        final_price = test_df["close"].iloc[-1]
        sell_value = position["amount"] * final_price * (1 - TRADING_FEE)
        pnl = sell_value - (position["amount"] * position["price"])
        pnl_pct = (final_price - position["price"]) / position["price"] * 100
        capital += sell_value
        trades.append({
            "entry_time": position["time"], "exit_time": test_df["timestamp"].iloc[-1],
            "entry_price": position["price"], "exit_price": final_price,
            "amount": position["amount"], "pnl": round(pnl, 4),
            "pnl_pct": round(pnl_pct, 2), "reason": "FIN BACKTEST",
            "result": "WIN" if pnl > 0 else "LOSS",
            "probability": position.get("probability", 0),
        })

    return trades, equity_curve, capital, initial_capital, train_stats


def draw_equity_chart(equity_curve, width=60, height=15):
    if not equity_curve:
        return
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
            line += "█" if val >= threshold else " "
        if row == height:
            label = f"${max_val:>8.2f} │"
        elif row == 0:
            label = f"${min_val:>8.2f} │"
        elif row == height // 2:
            label = f"${(max_val+min_val)/2:>8.2f} │"
        else:
            label = f"          │"
        chart_lines.append(label + line)
    chart_lines.append("          └" + "─" * width)
    console.print(Panel("\n".join(chart_lines), title="[bold cyan]Évolution du Capital (ML)[/bold cyan]", border_style="cyan"))


def print_report(trades, equity_curve, final_capital, initial_capital, symbol, months, train_stats):
    console.print()
    header = Text()
    header.append("RAPPORT ML BACKTEST", style="bold cyan")
    header.append(f"  |  {symbol}  |  {months} mois", style="dim")
    console.print(Panel(header, style="cyan"))

    total_trades = len(trades)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    total_pnl = sum(t["pnl"] for t in trades)
    total_wins = sum(t["pnl"] for t in wins) if wins else 0
    total_losses = abs(sum(t["pnl"] for t in losses)) if losses else 0
    win_rate = (len(wins) / total_trades * 100) if total_trades > 0 else 0
    profit_factor = (total_wins / total_losses) if total_losses > 0 else float("inf")
    roi = ((final_capital - initial_capital) / initial_capital) * 100

    max_drawdown = 0
    peak = initial_capital
    for point in equity_curve:
        if point["equity"] > peak:
            peak = point["equity"]
        dd = (peak - point["equity"]) / peak * 100
        if dd > max_drawdown:
            max_drawdown = dd

    stats_table = Table(title="Résultats ML", show_header=True, header_style="bold magenta")
    stats_table.add_column("Métrique", style="cyan", width=25)
    stats_table.add_column("Valeur", justify="right", width=20)

    pnl_style = "green" if total_pnl >= 0 else "red"
    roi_style = "green" if roi >= 0 else "red"

    stats_table.add_row("Modèle", train_stats.get("model_type", "random_forest") if isinstance(train_stats, dict) else "N/A")
    stats_table.add_row("Accuracy test", f"{train_stats['test_accuracy']*100:.1f}%")
    stats_table.add_row("", "")
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
    stats_table.add_row("Max Drawdown", f"[red]{max_drawdown:.2f}%[/red]")

    console.print(stats_table)
    console.print()

    draw_equity_chart(equity_curve)
    console.print()

    if trades:
        trades_table = Table(title=f"Trades ML ({len(trades)})", show_header=True, header_style="bold green")
        trades_table.add_column("#", style="dim", width=4)
        trades_table.add_column("Entrée", width=12)
        trades_table.add_column("Sortie", width=12)
        trades_table.add_column("Prix E.", justify="right", width=11)
        trades_table.add_column("Prix S.", justify="right", width=11)
        trades_table.add_column("P&L", justify="right", width=10)
        trades_table.add_column("Proba", justify="right", width=6)
        trades_table.add_column("Raison", width=11)
        trades_table.add_column("Rés.", justify="center", width=5)

        for i, t in enumerate(trades, 1):
            pnl_s = "green" if t["pnl"] > 0 else "red"
            res_s = "green" if t["result"] == "WIN" else "red"
            entry_date = t["entry_time"].strftime("%m-%d %H:%M") if hasattr(t["entry_time"], "strftime") else str(t["entry_time"])[:12]
            exit_date = t["exit_time"].strftime("%m-%d %H:%M") if hasattr(t["exit_time"], "strftime") else str(t["exit_time"])[:12]

            trades_table.add_row(
                str(i), entry_date, exit_date,
                f"${t['entry_price']:,.2f}", f"${t['exit_price']:,.2f}",
                f"[{pnl_s}]{t['pnl']:+.4f}[/{pnl_s}]",
                f"{t.get('probability', 0):.0%}",
                t["reason"],
                f"[{res_s}]{t['result']}[/{res_s}]",
            )

        console.print(trades_table)

    console.print()
    if roi > 0:
        console.print(Panel(
            f"[bold green]ML RENTABLE : {roi:+.2f}% de ROI[/bold green]\n"
            f"Capital: ${initial_capital:.2f} → ${final_capital:.2f} | Win Rate: {win_rate:.1f}%",
            title="VERDICT ML", border_style="green",
        ))
    else:
        console.print(Panel(
            f"[bold red]ML PERDANT : {roi:+.2f}% de ROI[/bold red]\n"
            f"Capital: ${initial_capital:.2f} → ${final_capital:.2f} | Win Rate: {win_rate:.1f}%",
            title="VERDICT ML", border_style="red",
        ))


def main():
    parser = argparse.ArgumentParser(description="ML Backtester")
    parser.add_argument("--capital", type=float, default=20, help="Capital de départ (défaut: 20)")
    parser.add_argument("--months", type=int, default=12, help="Période en mois (défaut: 12)")
    parser.add_argument("--symbol", type=str, default=None, help="Paire (défaut: config)")
    parser.add_argument("--model", type=str, default="random_forest", choices=["random_forest", "gradient_boosting"])
    parser.add_argument("--lookahead", type=int, default=5, help="Bougies à regarder dans le futur (défaut: 5)")
    parser.add_argument("--stop-loss", type=float, default=None, help="Stop-loss %% (défaut: config)")
    parser.add_argument("--take-profit", type=float, default=None, help="Take-profit %% (défaut: config)")
    args = parser.parse_args()

    symbol = args.symbol or config.SYMBOL
    stop_loss = args.stop_loss or config.STOP_LOSS_PCT
    take_profit = args.take_profit or config.TAKE_PROFIT_PCT

    df = fetch_historical_data(symbol, config.TIMEFRAME, args.months)

    trades, equity, final, initial, train_stats = run_ml_backtest(
        df, capital=args.capital,
        stop_loss_pct=stop_loss, take_profit_pct=take_profit,
        position_size_pct=config.POSITION_SIZE_PCT,
        model_type=args.model, lookahead=args.lookahead,
    )

    print_report(trades, equity, final, initial, symbol, args.months, train_stats)


if __name__ == "__main__":
    main()
