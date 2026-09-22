"""Mega Test ACTIONS avec 50€ — Trouve les meilleures combinaisons + projections."""

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


def fetch_stock(ticker, period="6mo", interval="1d"):
    df = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=True)
    if df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.reset_index()
    df = df.rename(columns={"Date": "timestamp", "Datetime": "timestamp",
                            "Open": "open", "High": "high", "Low": "low",
                            "Close": "close", "Volume": "volume"})
    return df[["timestamp", "open", "high", "low", "close", "volume"]].dropna().reset_index(drop=True)


def bt_grid(df, capital, levels, spacing):
    center = df["close"].iloc[len(df)//4]
    per = (capital * 0.5) / (levels * 2)
    grids = []
    for i in range(-levels, levels+1):
        if i == 0: continue
        p = center * (1 + i * spacing / 100)
        grids.append({"price": p, "side": "buy" if i < 0 else "sell", "filled": False, "amt": per/p, "lvl": i})
    cash, hold, trades, wins = capital, 0, 0, 0
    init = capital
    max_dd = 0; peak = capital
    for idx in range(len(df)):
        price = df["close"].iloc[idx]
        equity = cash + hold * price
        if equity > peak: peak = equity
        dd = (peak - equity)/peak*100 if peak > 0 else 0
        if dd > max_dd: max_dd = dd
        for g in grids:
            if g["filled"]: continue
            if g["side"] == "buy" and price <= g["price"]:
                cost = g["amt"] * price * (1 + FEE)
                if cost <= cash:
                    cash -= cost; hold += g["amt"]; g["filled"] = True; trades += 1
                    for g2 in grids:
                        if g2["lvl"] == -g["lvl"]: g2["filled"] = False
            elif g["side"] == "sell" and price >= g["price"] and hold >= g["amt"]:
                rev = g["amt"] * price * (1 - FEE)
                cash += rev; hold -= g["amt"]; g["filled"] = True; trades += 1; wins += 1
                for g2 in grids:
                    if g2["lvl"] == -g["lvl"]: g2["filled"] = False
    final = cash + hold * df["close"].iloc[-1]
    roi = ((final - init) / init) * 100
    return {"roi": round(roi,3), "trades": trades, "wins": wins,
            "wr": round(wins/trades*100, 1) if trades > 0 else 0, "final": round(final,2),
            "profit": round(final - init, 2), "drawdown": round(max_dd,1)}


def bt_dca(df, capital, interval, pct):
    init = capital; cash = capital; hold = 0; trades = 0
    for i in range(0, len(df), interval):
        price = df["close"].iloc[i]
        invest = cash * (pct / 100)
        if invest < 0.01: continue
        amt = invest * (1 - FEE) / price
        cash -= invest; hold += amt; trades += 1
    final = cash + hold * df["close"].iloc[-1]
    roi = ((final - init) / init) * 100
    return {"roi": round(roi,3), "trades": trades, "wins": 1 if final > init else 0,
            "wr": 100 if final > init else 0, "final": round(final,2),
            "profit": round(final - init, 2), "drawdown": 0}


def bt_buy_hold(df, capital):
    init = capital
    price0 = df["close"].iloc[0]; pricef = df["close"].iloc[-1]
    amt = init * (1 - FEE) / price0
    final = amt * pricef * (1 - FEE)
    # Calcul drawdown
    max_dd = 0; peak = price0
    for i in range(len(df)):
        p = df["close"].iloc[i]
        if p > peak: peak = p
        dd = (peak - p)/peak*100 if peak > 0 else 0
        if dd > max_dd: max_dd = dd
    roi = ((final - init) / init) * 100
    return {"roi": round(roi,3), "trades": 1, "wins": 1 if final > init else 0,
            "wr": 100 if final > init else 0, "final": round(final,2),
            "profit": round(final - init, 2), "drawdown": round(max_dd,1)}


def main():
    CAP = 100  # 100 EUROS

    TICKERS = [
        # Tech US (top performers)
        "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "AMD",
        "NFLX", "AVGO", "CRM", "ORCL", "ADBE", "INTC",
        # Autres secteurs US
        "JPM", "V", "WMT", "DIS", "BA", "JNJ", "PG", "KO", "PEP",
        # France
        "MC.PA", "TTE.PA", "AIR.PA", "OR.PA", "BNP.PA", "SAN.PA", "ASML.AS",
        # ETF (les + sûrs)
        "SPY", "QQQ", "VOO", "VTI", "VWCE.DE",
        # Crypto-related actions
        "COIN", "MSTR",
    ]

    console.print(Panel(
        f"[bold cyan]MEGA TEST ACTIONS — Capital de [bold yellow]{CAP}€[/bold yellow][/bold cyan]\n"
        f"3 stratégies × {len(TICKERS)} actions × multiples périodes",
        style="cyan"
    ))

    cache = {}
    console.print("\n[cyan]Téléchargement...[/cyan]")
    for ticker in TICKERS:
        for period in ["3mo", "6mo", "1y", "2y"]:
            try:
                df = fetch_stock(ticker, period, "1d")
                if df is not None and len(df) > 30:
                    cache[f"{ticker}_{period}"] = df
            except: pass
    console.print(f"  [green]{len(cache)} datasets chargés[/green]\n")

    results = []
    total = 0

    # 1. Buy & Hold
    for key, df in cache.items():
        try:
            ticker, period = key.split("_")
            r = bt_buy_hold(df, CAP)
            results.append({"strat":"Buy&Hold","tk":ticker,"per":period,"params":"-",**r})
            total += 1
        except: pass

    # 2. DCA
    for key, df in cache.items():
        try:
            ticker, period = key.split("_")
            for inter in [5, 10, 20, 30]:
                for pct in [5, 10, 15, 20]:
                    r = bt_dca(df, CAP, inter, pct)
                    results.append({"strat":"DCA","tk":ticker,"per":period,
                        "params":f"Every{inter}d {pct}%",**r})
                    total += 1
        except: pass

    # 3. Grid Trading
    for key, df in cache.items():
        try:
            ticker, period = key.split("_")
            for lvl in [3, 5, 8, 10]:
                for sp in [0.5, 1.0, 1.5, 2.0, 3.0]:
                    r = bt_grid(df, CAP, lvl, sp)
                    results.append({"strat":"Grid","tk":ticker,"per":period,
                        "params":f"Lvl{lvl} Sp{sp}%",**r})
                    total += 1
        except: pass

    results.sort(key=lambda x: x["roi"], reverse=True)
    console.print(f"[bold green]Tests terminés: {total}[/bold green]\n")

    # === TOP 20 ===
    top = Table(title=f"🏆 TOP 20 — Quel profit avec {CAP}€ ?", show_header=True, header_style="bold green")
    top.add_column("#", width=3)
    top.add_column("Stratégie", width=10)
    top.add_column("Action", width=8)
    top.add_column("Période", width=7)
    top.add_column("Capital final", justify="right", width=14)
    top.add_column("Profit", justify="right", width=11)
    top.add_column("ROI", justify="right", width=9)
    top.add_column("Drawdown", justify="right", width=10)
    top.add_column("Paramètres", width=22)

    for i, r in enumerate(results[:20], 1):
        s = "green" if r["roi"] >= 0 else "red"
        top.add_row(str(i), r["strat"], r["tk"], r["per"],
            f"[bold {s}]{r['final']}€[/bold {s}]",
            f"[{s}]{r['profit']:+}€[/{s}]",
            f"[{s}]{r['roi']:+.1f}%[/{s}]",
            f"[red]{r['drawdown']}%[/red]",
            r["params"])
    console.print(top)

    # === MEILLEURS PAR PÉRIODE ===
    console.print()
    for period in ["3mo", "6mo", "1y", "2y"]:
        period_results = [r for r in results if r["per"] == period]
        if not period_results: continue
        period_results.sort(key=lambda x: x["roi"], reverse=True)
        best = period_results[0]
        median = period_results[len(period_results)//2]

        period_label = {"3mo": "3 MOIS", "6mo": "6 MOIS", "1y": "1 AN", "2y": "2 ANS"}[period]
        s_b = "green" if best["roi"] >= 0 else "red"
        s_m = "green" if median["roi"] >= 0 else "red"

        console.print(Panel(
            f"[bold cyan]Sur {period_label} :[/bold cyan]\n"
            f"  🏆 [bold]MEILLEUR :[/bold] {best['strat']} sur {best['tk']} → "
            f"[bold {s_b}]{CAP}€ → {best['final']}€ ({best['roi']:+.1f}%)[/bold {s_b}]\n"
            f"  📊 [dim]Médian :[/dim] {median['strat']} sur {median['tk']} → "
            f"[{s_m}]{CAP}€ → {median['final']}€ ({median['roi']:+.1f}%)[/{s_m}]",
            border_style="cyan"
        ))

    # === RÉSUMÉ STRATÉGIES ===
    console.print()
    strats = {}
    for r in results:
        s = r["strat"]
        if s not in strats: strats[s] = {"n":0,"win":0,"roi":0,"profit":0,"best":-999,"best_r":None}
        strats[s]["n"] += 1
        strats[s]["roi"] += r["roi"]
        strats[s]["profit"] += r["profit"]
        if r["roi"] > 0: strats[s]["win"] += 1
        if r["roi"] > strats[s]["best"]:
            strats[s]["best"] = r["roi"]; strats[s]["best_r"] = r

    st = Table(title=f"📊 RÉSUMÉ PAR STRATÉGIE (Capital: {CAP}€)", show_header=True, header_style="bold yellow")
    st.add_column("Stratégie", width=12)
    st.add_column("Tests", justify="right", width=7)
    st.add_column("% Rent.", justify="right", width=8)
    st.add_column("ROI Moyen", justify="right", width=10)
    st.add_column("Profit Moyen", justify="right", width=12)
    st.add_column("Best ROI", justify="right", width=9)
    st.add_column("Best Profit", justify="right", width=12)

    for s in sorted(strats, key=lambda x: strats[x]["best"], reverse=True):
        d = strats[s]
        avg = d["roi"]/d["n"]
        avg_p = d["profit"]/d["n"]
        pct = d["win"]/d["n"]*100
        a_s = "green" if avg >= 0 else "red"
        b_s = "green" if d["best"] >= 0 else "red"
        st.add_row(s, str(d["n"]), f"{pct:.0f}%",
            f"[{a_s}]{avg:+.1f}%[/{a_s}]",
            f"[{a_s}]{avg_p:+.2f}€[/{a_s}]",
            f"[{b_s}]{d['best']:+.1f}%[/{b_s}]",
            f"[{b_s}]{d['best_r']['profit']:+.2f}€[/{b_s}]")
    console.print(st)

    # === TOP ACTIONS ===
    console.print()
    tk_stats = {}
    for r in results:
        c = r["tk"]
        if c not in tk_stats: tk_stats[c] = {"n":0,"win":0,"roi":0,"best":-999,"best_r":None}
        tk_stats[c]["n"] += 1
        tk_stats[c]["roi"] += r["roi"]
        if r["roi"] > 0: tk_stats[c]["win"] += 1
        if r["roi"] > tk_stats[c]["best"]:
            tk_stats[c]["best"] = r["roi"]; tk_stats[c]["best_r"] = r

    ct = Table(title="📈 TOP 15 ACTIONS", show_header=True, header_style="bold cyan")
    ct.add_column("Action", width=10)
    ct.add_column("% Rent.", justify="right", width=8)
    ct.add_column("ROI Moy.", justify="right", width=9)
    ct.add_column("Best ROI", justify="right", width=10)
    ct.add_column("Best Profit", justify="right", width=12)
    ct.add_column("Sur quoi ?", width=20)

    sorted_tk = sorted(tk_stats, key=lambda x: tk_stats[x]["best"], reverse=True)[:15]
    for c in sorted_tk:
        d = tk_stats[c]
        avg = d["roi"]/d["n"]
        pct = d["win"]/d["n"]*100
        a_s = "green" if avg >= 0 else "red"
        b_s = "green" if d["best"] >= 0 else "red"
        bd = d["best_r"]
        ct.add_row(c, f"{pct:.0f}%",
            f"[{a_s}]{avg:+.1f}%[/{a_s}]",
            f"[{b_s}]{d['best']:+.1f}%[/{b_s}]",
            f"[{b_s}]{bd['profit']:+.2f}€[/{b_s}]",
            f"{bd['strat']} {bd['per']}")
    console.print(ct)

    # === RECOMMANDATIONS ===
    best_overall = results[0]
    safe_choice = None
    for r in results:
        if r["strat"] == "Grid" and r["roi"] > 5 and r["drawdown"] < 10:
            safe_choice = r
            break

    console.print()
    console.print(Panel(
        f"[bold]Tests: {total} | Rentables: {sum(1 for r in results if r['roi'] > 0)} ({sum(1 for r in results if r['roi'] > 0)/total*100:.0f}%)[/bold]\n\n"
        f"[bold green]🏆 MEILLEUR PROFIT POTENTIEL avec {CAP}€:[/bold green]\n"
        f"  Stratégie : [bold]{best_overall['strat']}[/bold] sur [bold]{best_overall['tk']}[/bold]\n"
        f"  Période   : {best_overall['per']}\n"
        f"  ROI       : [bold green]{best_overall['roi']:+.1f}%[/bold green]\n"
        f"  Profit    : [bold green]{CAP}€ → {best_overall['final']}€ (+{best_overall['profit']}€)[/bold green]\n"
        f"  Drawdown  : [yellow]{best_overall['drawdown']}%[/yellow] (perte max temporaire)\n\n"
        + (
            f"[bold cyan]🛡️  CHOIX SÉCURISÉ (Grid, drawdown < 10%):[/bold cyan]\n"
            f"  {safe_choice['strat']} sur {safe_choice['tk']} ({safe_choice['per']})\n"
            f"  ROI: [green]{safe_choice['roi']:+.1f}%[/green] | Profit: [green]+{safe_choice['profit']}€[/green] | "
            f"Drawdown: [yellow]{safe_choice['drawdown']}%[/yellow]"
            if safe_choice else "[dim]Aucun choix sécurisé identifié[/dim]"
        ),
        title="💰 VERDICT FINAL", border_style="green",
    ))


if __name__ == "__main__":
    main()
