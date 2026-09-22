from datetime import datetime

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text
from rich.live import Live


class Dashboard:
    def __init__(self):
        self.console = Console()

    def render(self, indicators, portfolio, risk_manager, balance, symbol, mode):
        self.console.clear()

        # Header
        header = Text()
        header.append("TRADING BOT", style="bold cyan")
        header.append(f"  |  {symbol}  |  Mode: {mode}  |  ", style="dim")
        header.append(datetime.now().strftime("%H:%M:%S"), style="bold white")
        self.console.print(Panel(header, style="cyan"))

        # Indicateurs
        ind_table = Table(title="Indicateurs Techniques", show_header=True, header_style="bold magenta")
        ind_table.add_column("Indicateur", style="cyan")
        ind_table.add_column("Valeur", justify="right")
        ind_table.add_column("Signal", justify="center")

        price = indicators.get("price", 0)
        ind_table.add_row("Prix", f"${price:,.2f}", "")

        sma_s = indicators.get("sma_short", 0)
        sma_l = indicators.get("sma_long", 0)
        sma_signal = "[green]HAUSSIER[/green]" if sma_s > sma_l else "[red]BAISSIER[/red]"
        ind_table.add_row(f"SMA (courte/longue)", f"{sma_s} / {sma_l}", sma_signal)

        ema_s = indicators.get("ema_short", 0)
        ema_l = indicators.get("ema_long", 0)
        ema_signal = "[green]HAUSSIER[/green]" if ema_s > ema_l else "[red]BAISSIER[/red]"
        ind_table.add_row(f"EMA (courte/longue)", f"{ema_s} / {ema_l}", ema_signal)

        rsi = indicators.get("rsi", 0)
        if rsi < 30:
            rsi_style = "[green]SURVENTE[/green]"
        elif rsi > 70:
            rsi_style = "[red]SURACHAT[/red]"
        else:
            rsi_style = "[yellow]NEUTRE[/yellow]"
        ind_table.add_row("RSI", f"{rsi}", rsi_style)

        macd_h = indicators.get("macd_hist", 0)
        macd_signal = "[green]HAUSSIER[/green]" if macd_h > 0 else "[red]BAISSIER[/red]"
        ind_table.add_row("MACD Histogramme", f"{macd_h}", macd_signal)

        bb_l = indicators.get("bb_lower", 0)
        bb_u = indicators.get("bb_upper", 0)
        if price <= bb_l:
            bb_sig = "[green]SOUS BANDE[/green]"
        elif price >= bb_u:
            bb_sig = "[red]AU-DESSUS[/red]"
        else:
            bb_sig = "[yellow]DANS BANDE[/yellow]"
        ind_table.add_row("Bollinger", f"[{bb_l} — {bb_u}]", bb_sig)

        signal = indicators.get("signal", "HOLD")
        confidence = indicators.get("confidence", 0)
        signal_style = {"BUY": "bold green", "SELL": "bold red", "HOLD": "bold yellow"}.get(signal, "white")
        ind_table.add_row("SIGNAL", f"[{signal_style}]{signal}[/{signal_style}]", f"Confiance: {confidence}/5")

        self.console.print(ind_table)
        self.console.print()

        # Portfolio
        stats = portfolio.get_stats()
        port_table = Table(title="Portfolio", show_header=True, header_style="bold blue")
        port_table.add_column("Métrique", style="cyan")
        port_table.add_column("Valeur", justify="right")

        port_table.add_row("Solde USDT", f"${balance:,.2f}")

        if portfolio.has_open_position():
            entry = portfolio.get_entry_price()
            unrealized, unrealized_pct = portfolio.get_unrealized_pnl(price)
            pnl_style = "green" if unrealized >= 0 else "red"
            port_table.add_row("Position", f"Entrée: ${entry:,.2f}")
            port_table.add_row("P&L Latent", f"[{pnl_style}]{unrealized:+.4f} USDT ({unrealized_pct:+.2f}%)[/{pnl_style}]")
        else:
            port_table.add_row("Position", "Aucune")

        port_table.add_row("Trades Total", str(stats["total_trades"]))
        port_table.add_row("Win Rate", f"{stats['win_rate']}%")

        pnl_total = stats["total_pnl"]
        pnl_style = "green" if pnl_total >= 0 else "red"
        port_table.add_row("P&L Réalisé", f"[{pnl_style}]{pnl_total:+.4f} USDT[/{pnl_style}]")
        port_table.add_row("Profit Factor", str(stats["profit_factor"]))

        self.console.print(port_table)
        self.console.print()

        # Risk Manager
        risk_status = risk_manager.get_status()
        risk_table = Table(title="Risk Manager", show_header=True, header_style="bold yellow")
        risk_table.add_column("Paramètre", style="cyan")
        risk_table.add_column("Valeur", justify="right")

        risk_table.add_row("Trades aujourd'hui", f"{risk_status['trades_today']}/{risk_status['max_trades']}")
        risk_table.add_row("Cooldown", f"{risk_status['cooldown_remaining']}s" if risk_status['cooldown_remaining'] > 0 else "Prêt")
        risk_table.add_row("Stop-Loss", f"{risk_status['stop_loss_pct']}%")
        risk_table.add_row("Take-Profit", f"{risk_status['take_profit_pct']}%")
        risk_table.add_row("Max Drawdown", f"{risk_status['max_drawdown_pct']}%")

        self.console.print(risk_table)
        self.console.print()

        # Derniers trades
        recent = portfolio.get_recent_trades(5)
        if recent:
            trades_table = Table(title="Derniers Trades", show_header=True, header_style="bold green")
            trades_table.add_column("Date", style="dim")
            trades_table.add_column("Entrée", justify="right")
            trades_table.add_column("Sortie", justify="right")
            trades_table.add_column("P&L", justify="right")
            trades_table.add_column("Résultat", justify="center")

            for t in reversed(recent):
                pnl_style = "green" if t["pnl"] > 0 else "red"
                result_style = "green" if t["result"] == "WIN" else "red"
                exit_time = t["exit_time"][:16] if t.get("exit_time") else "?"
                trades_table.add_row(
                    exit_time,
                    f"${t['entry_price']:,.2f}",
                    f"${t['exit_price']:,.2f}",
                    f"[{pnl_style}]{t['pnl']:+.4f}[/{pnl_style}]",
                    f"[{result_style}]{t['result']}[/{result_style}]",
                )

            self.console.print(trades_table)
