"""Ultra Test pour ACTIONS — Trouve les meilleures combinaisons sur la bourse."""

from datetime import datetime, timedelta
import yfinance as yf
import pandas as pd
import ta
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
import warnings
warnings.filterwarnings("ignore")

console = Console()
FEE = 0.0005  # 0.05% — frais typiques courtiers actions (plus bas que crypto)


def fetch_stock(ticker, period="6mo", interval="1d"):
    """Télécharge les données d'une action via Yahoo Finance."""
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
    for idx in range(len(df)):
        price = df["close"].iloc[idx]
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
    wr = wins/trades*100 if trades > 0 else 0
    return {"roi": round(roi,3), "trades": trades, "wins": wins, "wr": round(wr,1), "final": round(final,2)}


def bt_dca(df, capital, interval, pct):
    init = capital
    cash = capital
    hold = 0
    trades = 0
    for i in range(0, len(df), interval):
        price = df["close"].iloc[i]
        invest = cash * (pct / 100)
        if invest < 0.01: continue
        amt = invest * (1 - FEE) / price
        cash -= invest
        hold += amt
        trades += 1
    final = cash + hold * df["close"].iloc[-1]
    roi = ((final - init) / init) * 100
    return {"roi": round(roi,3), "trades": trades, "wins": 0, "wr": 0, "final": round(final,2)}


def bt_buy_hold(df, capital):
    """Simple buy and hold pour comparaison."""
    init = capital
    price0 = df["close"].iloc[0]
    pricef = df["close"].iloc[-1]
    amt = init * (1 - FEE) / price0
    final = amt * pricef * (1 - FEE)
    roi = ((final - init) / init) * 100
    return {"roi": round(roi,3), "trades": 1, "wins": 1 if final > init else 0, "wr": 100 if final > init else 0, "final": round(final,2)}


def bt_mean_rev(df, capital, bb_p, bb_s, rsi_l, rsi_h, sl, tp, pos):
    close = df["close"]
    bb_low = ta.volatility.bollinger_lband(close, window=bb_p, window_dev=bb_s)
    bb_up = ta.volatility.bollinger_hband(close, window=bb_p, window_dev=bb_s)
    rsi = ta.momentum.rsi(close, window=14)
    init = capital
    pos_open = None
    wins, losses = 0, 0
    for i in range(bb_p+5, len(df)):
        price = close.iloc[i]
        if pos_open:
            pnl = (price - pos_open["p"]) / pos_open["p"] * 100
            if pnl <= -sl:
                capital += pos_open["a"] * price * (1-FEE); losses += 1; pos_open = None; continue
            if pnl >= tp:
                capital += pos_open["a"] * price * (1-FEE); wins += 1; pos_open = None; continue
        if not pos_open and price <= bb_low.iloc[i] and rsi.iloc[i] < rsi_l:
            inv = capital * (pos/100)
            pos_open = {"p": price, "a": inv*(1-FEE)/price}
            capital -= inv
        elif pos_open and (price >= bb_up.iloc[i] or rsi.iloc[i] > rsi_h):
            rev = pos_open["a"] * price * (1-FEE)
            if rev > pos_open["a"]*pos_open["p"]: wins += 1
            else: losses += 1
            capital += rev; pos_open = None
    if pos_open: capital += pos_open["a"] * close.iloc[-1] * (1-FEE)
    roi = ((capital-init)/init)*100
    t = wins+losses
    return {"roi": round(roi,3), "trades": t, "wins": wins, "wr": round(wins/t*100,1) if t>0 else 0, "final": round(capital,2)}


def bt_ema_cross(df, capital, fast, slow, sl, tp, pos):
    close = df["close"]
    ema_f = ta.trend.ema_indicator(close, window=fast)
    ema_s = ta.trend.ema_indicator(close, window=slow)
    init = capital
    pos_open = None
    wins, losses = 0, 0
    prev_above = False
    for i in range(slow+5, len(df)):
        price = close.iloc[i]
        curr_above = ema_f.iloc[i] > ema_s.iloc[i]
        if pos_open:
            pnl = (price - pos_open["p"]) / pos_open["p"] * 100
            if pnl <= -sl:
                capital += pos_open["a"]*price*(1-FEE); losses += 1; pos_open = None; continue
            if pnl >= tp:
                capital += pos_open["a"]*price*(1-FEE); wins += 1; pos_open = None; continue
        if not pos_open and curr_above and not prev_above:
            inv = capital*(pos/100)
            pos_open = {"p": price, "a": inv*(1-FEE)/price}
            capital -= inv
        elif pos_open and not curr_above and prev_above:
            rev = pos_open["a"]*price*(1-FEE)
            if rev > pos_open["a"]*pos_open["p"]: wins += 1
            else: losses += 1
            capital += rev; pos_open = None
        prev_above = curr_above
    if pos_open: capital += pos_open["a"]*close.iloc[-1]*(1-FEE)
    roi = ((capital-init)/init)*100
    t = wins+losses
    return {"roi": round(roi,3), "trades": t, "wins": wins, "wr": round(wins/t*100,1) if t>0 else 0, "final": round(capital,2)}


def main():
    CAP = 20

    # Tickers populaires : tech US, France, ETF
    TICKERS = [
        # Tech US (très volatiles, idéal pour grid)
        "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "AMD",
        # Autres secteurs US
        "JPM", "V", "WMT", "DIS", "NFLX", "BA",
        # France (Yahoo: .PA)
        "MC.PA", "TTE.PA", "AIR.PA", "OR.PA", "BNP.PA",
        # ETF
        "SPY", "QQQ", "VOO",
    ]
    PERIODS = [("3mo", "1h"), ("6mo", "1d"), ("1y", "1d"), ("2y", "1d")]

    console.print(Panel(
        "[bold cyan]ULTRA TEST ACTIONS — Stratégies sur la bourse[/bold cyan]\n"
        f"5 stratégies × {len(TICKERS)} actions × {len(PERIODS)} périodes",
        style="cyan"
    ))

    # Charger toutes les données
    cache = {}
    console.print("\n[cyan]Téléchargement des données...[/cyan]")
    for ticker in TICKERS:
        for period, interval in PERIODS:
            key = f"{ticker}_{period}_{interval}"
            try:
                df = fetch_stock(ticker, period, interval)
                if df is not None and len(df) > 50:
                    cache[key] = df
                    console.print(f"  [green]✓[/green] {ticker} {period} {interval} ({len(df)} points)")
            except Exception as e:
                pass

    console.print(f"\n[cyan]Données chargées: {len(cache)} datasets[/cyan]\n")

    results = []
    total = 0

    # 1. Buy & Hold (référence)
    console.print("[yellow]1/5 Buy & Hold (référence)...[/yellow]")
    for key, df in cache.items():
        try:
            ticker, period, interval = key.split("_")
            r = bt_buy_hold(df, CAP)
            results.append({"strat":"Buy&Hold","tk":ticker,"per":period,"int":interval,"params":"-",**r})
            total += 1
        except: pass

    # 2. GRID
    console.print("[yellow]2/5 Grid Trading...[/yellow]")
    for key, df in cache.items():
        try:
            ticker, period, interval = key.split("_")
            for lvl in [3, 5, 8, 10, 15]:
                for sp in [0.3, 0.5, 0.8, 1.0, 1.5, 2.0]:
                    r = bt_grid(df, CAP, lvl, sp)
                    results.append({"strat":"Grid","tk":ticker,"per":period,"int":interval,
                        "params":f"Lvl{lvl} Sp{sp}%",**r})
                    total += 1
        except: pass

    # 3. DCA
    console.print("[yellow]3/5 DCA...[/yellow]")
    for key, df in cache.items():
        try:
            ticker, period, interval = key.split("_")
            for inter in [5, 10, 20, 30]:
                for pct in [3, 5, 10, 15]:
                    r = bt_dca(df, CAP, inter, pct)
                    results.append({"strat":"DCA","tk":ticker,"per":period,"int":interval,
                        "params":f"Every{inter}d {pct}%",**r})
                    total += 1
        except: pass

    # 4. Mean Reversion
    console.print("[yellow]4/5 Mean Reversion...[/yellow]")
    for key, df in cache.items():
        try:
            ticker, period, interval = key.split("_")
            for bb_p in [15, 20]:
                for bb_s in [1.5, 2.0]:
                    for rsi_l, rsi_h in [(25,75),(30,70)]:
                        for sl in [3, 5]:
                            for tp in [4, 8]:
                                r = bt_mean_rev(df, CAP, bb_p, bb_s, rsi_l, rsi_h, sl, tp, 10)
                                results.append({"strat":"MeanRev","tk":ticker,"per":period,"int":interval,
                                    "params":f"BB{bb_p}/{bb_s} RSI({rsi_l}-{rsi_h}) SL{sl}% TP{tp}%",**r})
                                total += 1
        except: pass

    # 5. EMA Cross
    console.print("[yellow]5/5 EMA Cross...[/yellow]")
    for key, df in cache.items():
        try:
            ticker, period, interval = key.split("_")
            for fast in [5, 10, 20]:
                for slow in [20, 50, 100]:
                    if fast >= slow: continue
                    for sl in [3, 5]:
                        for tp in [5, 10]:
                            r = bt_ema_cross(df, CAP, fast, slow, sl, tp, 10)
                            results.append({"strat":"EMA Cross","tk":ticker,"per":period,"int":interval,
                                "params":f"EMA{fast}/{slow} SL{sl}% TP{tp}%",**r})
                            total += 1
        except: pass

    results.sort(key=lambda x: x["roi"], reverse=True)
    console.print(f"\n[bold green]Tests terminés: {total} combinaisons[/bold green]\n")

    # TOP 30
    top = Table(title=f"TOP 30 MEILLEURES (sur {total})", show_header=True, header_style="bold green")
    top.add_column("#", width=3)
    top.add_column("Stratégie", width=10)
    top.add_column("Action", width=8)
    top.add_column("Période", width=7)
    top.add_column("Int", width=4)
    top.add_column("ROI", justify="right", width=9)
    top.add_column("WR", justify="right", width=6)
    top.add_column("Trades", justify="right", width=7)
    top.add_column("Final $", justify="right", width=8)
    top.add_column("Paramètres", width=35)

    for i, r in enumerate(results[:30], 1):
        s = "green" if r["roi"] >= 0 else "red"
        top.add_row(str(i), r["strat"], r["tk"], r["per"], r["int"],
            f"[{s}]{r['roi']:+.2f}%[/{s}]", f"{r['wr']}%", str(r["trades"]), f"${r['final']}", r["params"])
    console.print(top)

    # PAR STRATÉGIE
    console.print()
    strats = {}
    for r in results:
        s = r["strat"]
        if s not in strats: strats[s] = {"n":0,"win":0,"roi":0,"best":-999,"best_r":None}
        strats[s]["n"] += 1
        strats[s]["roi"] += r["roi"]
        if r["roi"] > 0: strats[s]["win"] += 1
        if r["roi"] > strats[s]["best"]:
            strats[s]["best"] = r["roi"]
            strats[s]["best_r"] = r

    st = Table(title="RÉSUMÉ PAR STRATÉGIE", show_header=True, header_style="bold yellow")
    st.add_column("Stratégie", width=12)
    st.add_column("Tests", justify="right", width=7)
    st.add_column("Rentables", justify="right", width=10)
    st.add_column("% Rent.", justify="right", width=8)
    st.add_column("ROI Moy.", justify="right", width=9)
    st.add_column("Best ROI", justify="right", width=9)
    st.add_column("Best Action", width=12)

    for s in sorted(strats, key=lambda x: strats[x]["best"], reverse=True):
        d = strats[s]
        avg = d["roi"]/d["n"]
        pct = d["win"]/d["n"]*100
        a_s = "green" if avg >= 0 else "red"
        b_s = "green" if d["best"] >= 0 else "red"
        best_tk = d["best_r"]["tk"] if d["best_r"] else "?"
        st.add_row(s, str(d["n"]), str(d["win"]), f"{pct:.0f}%",
            f"[{a_s}]{avg:+.2f}%[/{a_s}]", f"[{b_s}]{d['best']:+.2f}%[/{b_s}]", best_tk)
    console.print(st)

    # PAR ACTION
    console.print()
    tickers_stats = {}
    for r in results:
        c = r["tk"]
        if c not in tickers_stats: tickers_stats[c] = {"n":0,"win":0,"roi":0,"best":-999}
        tickers_stats[c]["n"] += 1
        tickers_stats[c]["roi"] += r["roi"]
        if r["roi"] > 0: tickers_stats[c]["win"] += 1
        if r["roi"] > tickers_stats[c]["best"]: tickers_stats[c]["best"] = r["roi"]

    ct = Table(title="RÉSUMÉ PAR ACTION", show_header=True, header_style="bold cyan")
    ct.add_column("Action", width=10)
    ct.add_column("Tests", justify="right", width=7)
    ct.add_column("% Rent.", justify="right", width=8)
    ct.add_column("ROI Moy.", justify="right", width=9)
    ct.add_column("Best ROI", justify="right", width=9)

    for c in sorted(tickers_stats, key=lambda x: tickers_stats[x]["best"], reverse=True):
        d = tickers_stats[c]
        avg = d["roi"]/d["n"]
        pct = d["win"]/d["n"]*100
        a_s = "green" if avg >= 0 else "red"
        b_s = "green" if d["best"] >= 0 else "red"
        ct.add_row(c, str(d["n"]), f"{pct:.0f}%",
            f"[{a_s}]{avg:+.2f}%[/{a_s}]", f"[{b_s}]{d['best']:+.2f}%[/{b_s}]")
    console.print(ct)

    # VERDICT
    best = results[0]
    profitable = sum(1 for r in results if r["roi"] > 0)
    console.print()
    console.print(Panel(
        f"[bold]{total} tests | {profitable} rentables ({profitable/total*100:.0f}%)[/bold]\n\n"
        f"[bold cyan]🏆 MEILLEURE COMBINAISON ABSOLUE:[/bold cyan]\n"
        f"  Stratégie : [bold green]{best['strat']}[/bold green]\n"
        f"  Action    : [bold]{best['tk']}[/bold]\n"
        f"  Période   : {best['per']} | Intervalle: {best['int']}\n"
        f"  ROI       : [bold green]{best['roi']:+.2f}%[/bold green]\n"
        f"  Win Rate  : {best['wr']}% | Trades: {best['trades']}\n"
        f"  Params    : {best['params']}\n"
        f"  Capital   : $20.00 → [bold green]${best['final']}[/bold green]",
        title="🏆 VERDICT FINAL ACTIONS", border_style="green",
    ))


if __name__ == "__main__":
    main()
