"""Dashboard terminal pour le Grid Bot."""

from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text


class GridDashboard:
    def __init__(self):
        self.console = Console()

    def render(self, price, grid, holdings, cash, equity, initial,
               trades, wins, losses, profit, orders, symbol, dry_run):
        self.console.clear()

        # Header
        mode = "[yellow]DRY-RUN[/yellow]" if dry_run else "[red bold]LIVE[/red bold]"
        header = Text()
        header.append("GRID BOT", style="bold cyan")
        header.append(f"  |  {symbol}  |  ", style="dim")
        header.append(f"Mode: ", style="dim")
        self.console.print(Panel(
            f"[bold cyan]GRID BOT[/bold cyan]  |  {symbol}  |  Mode: {mode}  |  "
            f"{datetime.now().strftime('%H:%M:%S')}",
            style="cyan"
        ))

        # Prix et P&L
        roi = ((equity - initial) / initial) * 100 if initial > 0 else 0
        roi_style = "green" if roi >= 0 else "red"
        pnl = equity - initial

        price_panel = (
            f"[bold]Prix: ${price:.6f}[/bold]  |  "
            f"Equity: [{roi_style}]${equity:.2f}[/{roi_style}]  |  "
            f"P&L: [{roi_style}]{pnl:+.4f}$[/{roi_style}]  |  "
            f"ROI: [{roi_style}]{roi:+.2f}%[/{roi_style}]"
        )
        self.console.print(Panel(price_panel, title="Prix & Performance"))

        # Grille
        if grid and grid.grids:
            grid_table = Table(title="Niveaux de la Grille", show_header=True, header_style="bold magenta")
            grid_table.add_column("Niveau", justify="center", width=8)
            grid_table.add_column("Prix", justify="right", width=14)
            grid_table.add_column("Type", justify="center", width=8)
            grid_table.add_column("État", justify="center", width=10)
            grid_table.add_column("Distance", justify="right", width=10)

            for g in grid.grids:
                level = f"Lvl {g['level']:+d}"
                side = "[green]ACHAT[/green]" if g["side"] == "buy" else "[red]VENTE[/red]"
                state = "[dim]Rempli[/dim]" if g["filled"] else "[bold green]Actif[/bold green]"
                dist = ((g["price"] - price) / price) * 100
                dist_s = "green" if dist > 0 else "red"

                # Marquer le prix actuel
                marker = " ◄" if abs(dist) < 0.2 else ""
                grid_table.add_row(
                    level,
                    f"${g['price']:.6f}{marker}",
                    side,
                    state,
                    f"[{dist_s}]{dist:+.2f}%[/{dist_s}]"
                )

            self.console.print(grid_table)

        # Portfolio
        port_table = Table(title="Portfolio", show_header=True, header_style="bold blue")
        port_table.add_column("Métrique", style="cyan", width=20)
        port_table.add_column("Valeur", justify="right", width=18)

        port_table.add_row("Cash (USDT)", f"${cash:.2f}")
        port_table.add_row("Holdings (DOGE)", f"{holdings:.2f}")
        port_table.add_row("Valeur Holdings", f"${holdings * price:.4f}")
        port_table.add_row("", "")
        port_table.add_row("Trades", str(trades))
        wr = f"{wins/trades*100:.1f}%" if trades > 0 else "N/A"
        port_table.add_row("Wins / Losses", f"[green]{wins}[/green] / [red]{losses}[/red]")
        port_table.add_row("Win Rate", wr)
        p_style = "green" if profit >= 0 else "red"
        port_table.add_row("Profit Grid", f"[{p_style}]{profit:+.4f} USDT[/{p_style}]")

        self.console.print(port_table)

        # Dernières opérations
        if orders:
            ops_table = Table(title="Dernières Opérations", show_header=True, header_style="bold green")
            ops_table.add_column("Heure", style="dim", width=10)
            ops_table.add_column("Action", justify="center", width=8)
            ops_table.add_column("Prix", justify="right", width=12)
            ops_table.add_column("Qté", justify="right", width=10)
            ops_table.add_column("Niveau", justify="center", width=8)
            ops_table.add_column("Profit", justify="right", width=12)

            for o in reversed(orders[-8:]):
                t = o["time"][11:19] if len(o["time"]) > 19 else o["time"]
                side_s = "[green]ACHAT[/green]" if o["side"] == "BUY" else "[red]VENTE[/red]"
                prof = o.get("profit")
                if prof is not None:
                    ps = "green" if prof >= 0 else "red"
                    prof_str = f"[{ps}]{prof:+.4f}[/{ps}]"
                else:
                    prof_str = "-"
                ops_table.add_row(
                    t, side_s, f"${o['price']:.6f}",
                    f"{o['amount']:.2f}", f"Lvl{o['level']:+d}", prof_str
                )
            self.console.print(ops_table)

        self.console.print(f"\n[dim]Rafraîchissement toutes les {30}s | Ctrl+C pour arrêter[/dim]")
