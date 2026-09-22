"""Test ACTIONS IA — Toutes les actions liées à l'intelligence artificielle."""

from datetime import datetime
import yfinance as yf
import pandas as pd
import ta
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
import warnings
warnings.filterwarnings("ignore")

console = Console()
FEE = 0.0005


def fetch_stock(ticker, period="1y"):
    df = yf.download(ticker, period=period, interval="1d", progress=False, auto_adjust=True)
    if df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def bt_buy_hold(df, capital):
    init = capital
    p0 = float(df["Close"].iloc[0]); pf = float(df["Close"].iloc[-1])
    amt = init * (1 - FEE) / p0
    final = amt * pf * (1 - FEE)
    max_dd = 0; peak = p0
    for i in range(len(df)):
        p = float(df["Close"].iloc[i])
        if p > peak: peak = p
        dd = (peak - p)/peak*100 if peak > 0 else 0
        if dd > max_dd: max_dd = dd
    return {"roi": ((final-init)/init)*100, "final": final, "profit": final-init, "drawdown": max_dd}


def bt_dca(df, capital, interval=10, pct=10):
    init = capital; cash = capital; hold = 0; trades = 0
    for i in range(0, len(df), interval):
        price = float(df["Close"].iloc[i])
        invest = cash * (pct/100)
        if invest < 0.01: continue
        amt = invest * (1-FEE) / price
        cash -= invest; hold += amt; trades += 1
    final = cash + hold * float(df["Close"].iloc[-1])
    return {"roi": ((final-init)/init)*100, "final": final, "profit": final-init, "drawdown": 0, "trades": trades}


def main():
    CAP = 100

    # Toutes les actions IA classées par catégorie
    AI_STOCKS = {
        # ━━━ Hardware / Puces IA ━━━
        "🔧 Hardware IA": {
            "NVDA": "Nvidia (GPU IA #1)",
            "AMD": "AMD (concurrent Nvidia)",
            "AVGO": "Broadcom (puces IA)",
            "INTC": "Intel (CPU & IA)",
            "TSM": "TSMC (fonderie #1 mondial)",
            "ARM": "ARM (architecture IA mobile)",
            "SMCI": "Super Micro (serveurs IA)",
            "DELL": "Dell (serveurs IA)",
            "ANET": "Arista Networks (réseau IA)",
            "MU": "Micron (mémoire IA)",
        },
        # ━━━ Logiciels IA / Cloud ━━━
        "💻 Software & Cloud IA": {
            "MSFT": "Microsoft (Copilot, OpenAI)",
            "GOOGL": "Google (Gemini, DeepMind)",
            "META": "Meta (LLaMA, Reality Labs)",
            "AMZN": "Amazon (AWS, Alexa)",
            "ORCL": "Oracle (Cloud IA)",
            "CRM": "Salesforce (Einstein AI)",
            "ADBE": "Adobe (Firefly AI)",
            "NOW": "ServiceNow (IA entreprise)",
            "PLTR": "Palantir (IA défense/data)",
            "SNOW": "Snowflake (data IA)",
        },
        # ━━━ Pure-play IA ━━━
        "🤖 Pure IA": {
            "AI": "C3.ai (IA entreprise)",
            "BBAI": "BigBear.ai (IA défense)",
            "SOUN": "SoundHound AI (voix)",
            "PATH": "UiPath (RPA + IA)",
            "PLTR": "Palantir",
        },
        # ━━━ Robotique & Auto IA ━━━
        "🚗 Robotique & Auto IA": {
            "TSLA": "Tesla (FSD, Optimus)",
            "ABBV": "AbbVie (IA pharma)",
        },
        # ━━━ Cybersec IA ━━━
        "🔒 Cybersécurité IA": {
            "CRWD": "CrowdStrike (IA threat)",
            "PANW": "Palo Alto (IA sec)",
            "DDOG": "Datadog (IA monitoring)",
        },
        # ━━━ ETF IA ━━━
        "📊 ETF IA (diversifiés)": {
            "BOTZ": "Global X Robotics & AI ETF",
            "AIQ": "Global X Artificial Intelligence ETF",
            "ROBO": "ROBO Global Robotics ETF",
            "QTUM": "Defiance Quantum ETF",
            "ARKQ": "ARK Autonomous & Robotics",
        },
    }

    console.print(Panel(
        f"[bold cyan]🤖 TEST ACTIONS IA — Capital: [bold yellow]{CAP}€[/bold yellow][/bold cyan]\n"
        f"Toutes les actions liées à l'IA",
        style="cyan"
    ))

    # Charger données pour chaque action
    all_results = []
    console.print("\n[cyan]Téléchargement...[/cyan]")

    for category, stocks in AI_STOCKS.items():
        for ticker, name in stocks.items():
            for period in ["3mo", "6mo", "1y", "2y"]:
                try:
                    df = fetch_stock(ticker, period)
                    if df is None or len(df) < 30:
                        continue

                    # Buy & Hold
                    bh = bt_buy_hold(df, CAP)
                    all_results.append({
                        "category": category, "ticker": ticker, "name": name,
                        "period": period, "strat": "Buy&Hold", **bh
                    })

                    # DCA
                    dca = bt_dca(df, CAP, interval=10, pct=15)
                    all_results.append({
                        "category": category, "ticker": ticker, "name": name,
                        "period": period, "strat": "DCA", **dca
                    })
                except Exception as e:
                    pass

    console.print(f"  [green]{len(all_results)} tests effectués[/green]\n")

    # === TABLEAU PAR CATÉGORIE (1 an) ===
    for category, stocks in AI_STOCKS.items():
        cat_results = [r for r in all_results if r["category"] == category and r["period"] == "1y" and r["strat"] == "Buy&Hold"]
        if not cat_results: continue
        cat_results.sort(key=lambda x: x["roi"], reverse=True)

        t = Table(title=f"{category} — Buy & Hold sur 1 an", show_header=True, header_style="bold magenta")
        t.add_column("Ticker", style="bold", width=8)
        t.add_column("Description", width=28)
        t.add_column("100€ deviennent", justify="right", width=15)
        t.add_column("Profit", justify="right", width=11)
        t.add_column("ROI", justify="right", width=9)
        t.add_column("Drawdown", justify="right", width=10)

        for r in cat_results:
            s = "green" if r["roi"] >= 0 else "red"
            t.add_row(
                r["ticker"], r["name"],
                f"[bold {s}]{r['final']:.2f}€[/bold {s}]",
                f"[{s}]{r['profit']:+.2f}€[/{s}]",
                f"[{s}]{r['roi']:+.1f}%[/{s}]",
                f"[red]{r['drawdown']:.1f}%[/red]",
            )
        console.print(t)
        console.print()

    # === TOP 20 IA ===
    all_results.sort(key=lambda x: x["roi"], reverse=True)
    top = Table(title=f"🏆 TOP 20 ACTIONS IA — Toutes périodes (Capital {CAP}€)", show_header=True, header_style="bold green")
    top.add_column("#", width=3)
    top.add_column("Ticker", style="bold", width=8)
    top.add_column("Description", width=25)
    top.add_column("Période", width=7)
    top.add_column("Stratégie", width=10)
    top.add_column("100€ →", justify="right", width=12)
    top.add_column("Profit", justify="right", width=12)
    top.add_column("ROI", justify="right", width=9)

    for i, r in enumerate(all_results[:20], 1):
        s = "green" if r["roi"] >= 0 else "red"
        top.add_row(
            str(i), r["ticker"], r["name"][:23], r["period"], r["strat"],
            f"[bold {s}]{r['final']:.2f}€[/bold {s}]",
            f"[{s}]{r['profit']:+.2f}€[/{s}]",
            f"[{s}]{r['roi']:+.1f}%[/{s}]"
        )
    console.print(top)
    console.print()

    # === RÉSUMÉ PAR CATÉGORIE (1 an, Buy&Hold) ===
    cat_summary = {}
    for r in all_results:
        if r["period"] == "1y" and r["strat"] == "Buy&Hold":
            cat = r["category"]
            if cat not in cat_summary: cat_summary[cat] = []
            cat_summary[cat].append(r["roi"])

    summary = Table(title="📊 PERFORMANCE PAR CATÉGORIE (1 an)", show_header=True, header_style="bold yellow")
    summary.add_column("Catégorie", width=30)
    summary.add_column("Actions testées", justify="right", width=15)
    summary.add_column("ROI Moyen", justify="right", width=12)
    summary.add_column("ROI Max", justify="right", width=12)
    summary.add_column("ROI Min", justify="right", width=12)

    for cat in sorted(cat_summary, key=lambda x: sum(cat_summary[x])/len(cat_summary[x]) if cat_summary[x] else 0, reverse=True):
        rois = cat_summary[cat]
        avg = sum(rois)/len(rois) if rois else 0
        a_s = "green" if avg >= 0 else "red"
        max_s = "green" if max(rois) >= 0 else "red"
        min_s = "green" if min(rois) >= 0 else "red"
        summary.add_row(
            cat, str(len(rois)),
            f"[{a_s}]{avg:+.1f}%[/{a_s}]",
            f"[{max_s}]{max(rois):+.1f}%[/{max_s}]",
            f"[{min_s}]{min(rois):+.1f}%[/{min_s}]"
        )
    console.print(summary)
    console.print()

    # === MEILLEUR PORTEFEUILLE IA DIVERSIFIÉ ===
    # Prendre la meilleure action de chaque catégorie majeure (1 an)
    best_per_cat = {}
    for r in all_results:
        if r["period"] != "1y" or r["strat"] != "Buy&Hold": continue
        cat = r["category"]
        if cat not in best_per_cat or r["roi"] > best_per_cat[cat]["roi"]:
            best_per_cat[cat] = r

    portfolio_text = "[bold cyan]🎯 PORTEFEUILLE IA DIVERSIFIÉ (100€)[/bold cyan]\n"
    portfolio_text += "Le meilleur de chaque catégorie sur 1 an :\n\n"

    n_cats = len(best_per_cat)
    per_cat = CAP / n_cats if n_cats > 0 else 0
    total_final = 0
    for cat, r in best_per_cat.items():
        amount_at_end = (per_cat / r["final"]) * r["final"] * (r["roi"]/100 + 1)
        total_final += amount_at_end
        portfolio_text += f"  {cat}\n"
        portfolio_text += f"    [bold]{r['ticker']}[/bold] - {r['name'][:40]}\n"
        portfolio_text += f"    Investit: {per_cat:.2f}€ → Devient: [green]{amount_at_end:.2f}€[/green] (ROI {r['roi']:+.1f}%)\n\n"

    portfolio_text += f"[bold]TOTAL : 100€ → [green]{total_final:.2f}€[/green] ([green]{((total_final-CAP)/CAP*100):+.1f}%[/green])[/bold]"

    console.print(Panel(portfolio_text, title="💼 PORTEFEUILLE OPTIMAL", border_style="cyan"))


if __name__ == "__main__":
    main()
