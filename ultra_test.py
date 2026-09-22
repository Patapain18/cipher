"""Ultra Test — 2000+ tests pour trouver LA meilleure config."""

from datetime import datetime, timedelta
import ccxt
import pandas as pd
import ta
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()
FEE = 0.001


def fetch(symbol, timeframe, months):
    ex = ccxt.binance({"enableRateLimit": True})
    since = int((datetime.now() - timedelta(days=months * 30)).timestamp() * 1000)
    all_d = []
    while True:
        d = ex.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
        if not d: break
        all_d.extend(d)
        since = d[-1][0] + 1
        if len(d) < 1000: break
    df = pd.DataFrame(all_d, columns=["timestamp","open","high","low","close","volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df.drop_duplicates(subset=["timestamp"]).reset_index(drop=True)


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


def bt_dca(df, capital, interval, dca_amount_pct):
    """Dollar Cost Averaging — achète à intervalles réguliers."""
    init = capital
    cash = capital
    holdings = 0
    trades = 0
    for i in range(0, len(df), interval):
        price = df["close"].iloc[i]
        invest = cash * (dca_amount_pct / 100)
        if invest < 0.01: continue
        amt = invest * (1 - FEE) / price
        cash -= invest
        holdings += amt
        trades += 1
    final = cash + holdings * df["close"].iloc[-1]
    roi = ((final - init) / init) * 100
    return {"roi": round(roi,3), "trades": trades, "wins": 0, "wr": 0, "final": round(final,2)}


def bt_mean_reversion(df, capital, bb_period, bb_std, rsi_period, rsi_low, rsi_high, sl, tp, pos_pct):
    """Mean Reversion — achète quand le prix est trop bas, vend quand trop haut."""
    close = df["close"]
    bb_lower = ta.volatility.bollinger_lband(close, window=bb_period, window_dev=bb_std)
    bb_upper = ta.volatility.bollinger_hband(close, window=bb_period, window_dev=bb_std)
    rsi = ta.momentum.rsi(close, window=rsi_period)
    init = capital
    position = None
    wins, losses = 0, 0
    for i in range(bb_period + 5, len(df)):
        price = close.iloc[i]
        if position:
            pnl_pct = (price - position["p"]) / position["p"] * 100
            if pnl_pct <= -sl:
                capital += position["a"] * price * (1 - FEE); losses += 1; position = None; continue
            if pnl_pct >= tp:
                capital += position["a"] * price * (1 - FEE); wins += 1; position = None; continue
        if not position and price <= bb_lower.iloc[i] and rsi.iloc[i] < rsi_low:
            inv = capital * (pos_pct/100)
            position = {"p": price, "a": inv*(1-FEE)/price}
            capital -= inv
        elif position and (price >= bb_upper.iloc[i] or rsi.iloc[i] > rsi_high):
            rev = position["a"] * price * (1-FEE)
            if rev > position["a"]*position["p"]: wins += 1
            else: losses += 1
            capital += rev; position = None
    if position: capital += position["a"]*close.iloc[-1]*(1-FEE)
    roi = ((capital-init)/init)*100
    t = wins+losses
    return {"roi": round(roi,3), "trades": t, "wins": wins, "wr": round(wins/t*100,1) if t>0 else 0, "final": round(capital,2)}


def bt_breakout(df, capital, lookback, volume_mult, sl, tp, pos_pct):
    """Breakout — achète quand le prix casse un plus haut avec volume."""
    close = df["close"]
    high = df["high"]
    volume = df["volume"]
    vol_sma = ta.trend.sma_indicator(volume, window=20)
    init = capital
    position = None
    wins, losses = 0, 0
    for i in range(lookback + 20, len(df)):
        price = close.iloc[i]
        if position:
            pnl_pct = (price - position["p"]) / position["p"] * 100
            if pnl_pct <= -sl:
                capital += position["a"]*price*(1-FEE); losses += 1; position = None; continue
            if pnl_pct >= tp:
                capital += position["a"]*price*(1-FEE); wins += 1; position = None; continue
        prev_high = high.iloc[i-lookback:i].max()
        vol_ok = volume.iloc[i] > vol_sma.iloc[i] * volume_mult
        if not position and price > prev_high and vol_ok:
            inv = capital * (pos_pct/100)
            position = {"p": price, "a": inv*(1-FEE)/price}
            capital -= inv
        elif position and price < close.iloc[i-lookback:i].min():
            rev = position["a"]*price*(1-FEE)
            if rev > position["a"]*position["p"]: wins += 1
            else: losses += 1
            capital += rev; position = None
    if position: capital += position["a"]*close.iloc[-1]*(1-FEE)
    roi = ((capital-init)/init)*100
    t = wins+losses
    return {"roi": round(roi,3), "trades": t, "wins": wins, "wr": round(wins/t*100,1) if t>0 else 0, "final": round(capital,2)}


def bt_dual_ma_cross(df, capital, fast, slow, sl, tp, pos_pct):
    """Double EMA Cross — signal quand EMA rapide croise EMA lente."""
    close = df["close"]
    ema_f = ta.trend.ema_indicator(close, window=fast)
    ema_s = ta.trend.ema_indicator(close, window=slow)
    init = capital
    position = None
    wins, losses = 0, 0
    prev_above = False
    for i in range(slow + 5, len(df)):
        price = close.iloc[i]
        curr_above = ema_f.iloc[i] > ema_s.iloc[i]
        if position:
            pnl_pct = (price - position["p"]) / position["p"] * 100
            if pnl_pct <= -sl:
                capital += position["a"]*price*(1-FEE); losses += 1; position = None; continue
            if pnl_pct >= tp:
                capital += position["a"]*price*(1-FEE); wins += 1; position = None; continue
        # Cross haussier
        if not position and curr_above and not prev_above:
            inv = capital*(pos_pct/100)
            position = {"p": price, "a": inv*(1-FEE)/price}
            capital -= inv
        # Cross baissier
        elif position and not curr_above and prev_above:
            rev = position["a"]*price*(1-FEE)
            if rev > position["a"]*position["p"]: wins += 1
            else: losses += 1
            capital += rev; position = None
        prev_above = curr_above
    if position: capital += position["a"]*close.iloc[-1]*(1-FEE)
    roi = ((capital-init)/init)*100
    t = wins+losses
    return {"roi": round(roi,3), "trades": t, "wins": wins, "wr": round(wins/t*100,1) if t>0 else 0, "final": round(capital,2)}


def main():
    CAP = 20
    SYMS = ["BTC/USDT","ETH/USDT","SOL/USDT","BNB/USDT","XRP/USDT",
            "DOGE/USDT","AVAX/USDT","ADA/USDT","LINK/USDT","DOT/USDT","NEAR/USDT","UNI/USDT"]
    TFS = ["15m","1h","4h"]
    MONTHS = [1, 3, 6]

    console.print(Panel("[bold cyan]ULTRA TEST — 2000+ combinaisons[/bold cyan]\n6 stratégies × 12 cryptos × 3 timeframes × 3 périodes", style="cyan"))

    # Charger données
    cache = {}
    for sym in SYMS:
        for tf in TFS:
            for m in MONTHS:
                if tf == "15m" and m > 1: continue  # 15min limité à 1 mois
                key = f"{sym}_{tf}_{m}"
                try:
                    console.print(f"  {sym} {tf} {m}m...", end=" ")
                    cache[key] = fetch(sym, tf, m)
                    console.print(f"[green]✓ {len(cache[key])}[/green]")
                except Exception as e:
                    console.print(f"[red]✗[/red]")

    results = []
    total_tests = 0

    # === 1. GRID TRADING (élargi) ===
    console.print("\n[yellow]1/6 Grid Trading (240 tests)...[/yellow]")
    for sym in SYMS:
        for tf in ["1h","4h"]:
            for m in MONTHS:
                if tf == "15m" and m > 1: continue
                key = f"{sym}_{tf}_{m}"
                if key not in cache: continue
                for lvl in [3, 5, 8, 10, 15, 20]:
                    for sp in [0.2, 0.3, 0.5, 0.8, 1.0, 1.5]:
                        try:
                            r = bt_grid(cache[key], CAP, lvl, sp)
                            results.append({"strat":"Grid","sym":sym,"tf":tf,"m":m,"params":f"Lvl{lvl} Sp{sp}%",**r})
                            total_tests += 1
                        except: pass

    # === 2. MEAN REVERSION ===
    console.print(f"[yellow]2/6 Mean Reversion...[/yellow]")
    for sym in SYMS:
        for tf in TFS:
            for m in MONTHS:
                if tf == "15m" and m > 1: continue
                key = f"{sym}_{tf}_{m}"
                if key not in cache: continue
                for bb_p in [15, 20, 25]:
                    for bb_s in [1.5, 2.0, 2.5]:
                        for rsi_l, rsi_h in [(20,80),(25,75),(30,70)]:
                            for sl in [1.5, 2, 3]:
                                for tp in [2, 3, 4]:
                                    try:
                                        r = bt_mean_reversion(cache[key], CAP, bb_p, bb_s, 14, rsi_l, rsi_h, sl, tp, 5)
                                        results.append({"strat":"MeanRev","sym":sym,"tf":tf,"m":m,
                                            "params":f"BB{bb_p}/{bb_s} RSI({rsi_l}-{rsi_h}) SL{sl}% TP{tp}%",**r})
                                        total_tests += 1
                                    except: pass

    # === 3. BREAKOUT ===
    console.print(f"[yellow]3/6 Breakout...[/yellow]")
    for sym in SYMS:
        for tf in TFS:
            for m in MONTHS:
                if tf == "15m" and m > 1: continue
                key = f"{sym}_{tf}_{m}"
                if key not in cache: continue
                for lb in [10, 20, 30, 50]:
                    for vm in [1.2, 1.5, 2.0]:
                        for sl in [1.5, 2, 3]:
                            for tp in [3, 5, 8]:
                                try:
                                    r = bt_breakout(cache[key], CAP, lb, vm, sl, tp, 5)
                                    results.append({"strat":"Breakout","sym":sym,"tf":tf,"m":m,
                                        "params":f"LB{lb} Vol{vm}x SL{sl}% TP{tp}%",**r})
                                    total_tests += 1
                                except: pass

    # === 4. DUAL EMA CROSS ===
    console.print(f"[yellow]4/6 Dual EMA Cross...[/yellow]")
    for sym in SYMS:
        for tf in TFS:
            for m in MONTHS:
                if tf == "15m" and m > 1: continue
                key = f"{sym}_{tf}_{m}"
                if key not in cache: continue
                for fast in [5, 8, 10, 12, 15]:
                    for slow in [20, 25, 30, 40, 50]:
                        if fast >= slow: continue
                        for sl in [1.5, 2, 3, 4]:
                            for tp in [2, 3, 5, 8]:
                                try:
                                    r = bt_dual_ma_cross(cache[key], CAP, fast, slow, sl, tp, 5)
                                    results.append({"strat":"EMA Cross","sym":sym,"tf":tf,"m":m,
                                        "params":f"EMA{fast}/{slow} SL{sl}% TP{tp}%",**r})
                                    total_tests += 1
                                except: pass

    # === 5. DCA ===
    console.print(f"[yellow]5/6 DCA (Dollar Cost Averaging)...[/yellow]")
    for sym in SYMS:
        for tf in ["1h"]:
            for m in MONTHS:
                key = f"{sym}_{tf}_{m}"
                if key not in cache: continue
                for interval in [6, 12, 24, 48, 72, 168]:
                    for pct in [2, 3, 5, 8, 10, 15]:
                        try:
                            r = bt_dca(cache[key], CAP, interval, pct)
                            results.append({"strat":"DCA","sym":sym,"tf":tf,"m":m,
                                "params":f"Every{interval}h {pct}%",**r})
                            total_tests += 1
                        except: pass

    # === 6. GRID avec centre dynamique ===
    console.print(f"[yellow]6/6 Grid Dynamique...[/yellow]")
    for sym in SYMS:
        for tf in ["1h"]:
            for m in MONTHS:
                key = f"{sym}_{tf}_{m}"
                if key not in cache: continue
                df = cache[key]
                for start_frac in [0.1, 0.2, 0.3, 0.5]:
                    center_idx = int(len(df) * start_frac)
                    for lvl in [5, 10, 15]:
                        for sp in [0.3, 0.5, 1.0]:
                            try:
                                sub_df = df.iloc[center_idx:].reset_index(drop=True)
                                r = bt_grid(sub_df, CAP, lvl, sp)
                                results.append({"strat":"Grid Dyn","sym":sym,"tf":tf,"m":m,
                                    "params":f"Start{int(start_frac*100)}% Lvl{lvl} Sp{sp}%",**r})
                                total_tests += 1
                            except: pass

    if total_tests % 500 == 0:
        console.print(f"  {total_tests} tests...")

    # === RÉSULTATS ===
    results.sort(key=lambda x: x["roi"], reverse=True)
    console.print(f"\n[bold green]Tests terminés: {total_tests} combinaisons testées[/bold green]\n")

    # TOP 30
    top = Table(title=f"TOP 30 MEILLEURES COMBINAISONS (sur {total_tests})", show_header=True, header_style="bold green")
    top.add_column("#", width=3)
    top.add_column("Stratégie", width=11)
    top.add_column("Crypto", width=11)
    top.add_column("TF", width=4)
    top.add_column("M", width=3)
    top.add_column("ROI", justify="right", width=9)
    top.add_column("WR", justify="right", width=6)
    top.add_column("Trades", justify="right", width=7)
    top.add_column("Final $", justify="right", width=8)
    top.add_column("Paramètres", width=38)

    for i, r in enumerate(results[:30], 1):
        s = "green" if r["roi"] >= 0 else "red"
        top.add_row(str(i), r["strat"], r["sym"], r["tf"], str(r["m"]),
            f"[{s}]{r['roi']:+.2f}%[/{s}]", f"{r['wr']}%", str(r["trades"]), f"${r['final']}", r["params"])
    console.print(top)

    # RÉSUMÉ PAR STRATÉGIE
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
    st.add_column("Stratégie", width=13)
    st.add_column("Tests", justify="right", width=7)
    st.add_column("Rentables", justify="right", width=10)
    st.add_column("% Rent.", justify="right", width=8)
    st.add_column("ROI Moy.", justify="right", width=9)
    st.add_column("Best ROI", justify="right", width=9)
    st.add_column("Best Crypto", width=11)

    for s in sorted(strats, key=lambda x: strats[x]["best"], reverse=True):
        d = strats[s]
        avg = d["roi"]/d["n"]
        pct = d["win"]/d["n"]*100
        a_s = "green" if avg >= 0 else "red"
        b_s = "green" if d["best"] >= 0 else "red"
        best_sym = d["best_r"]["sym"] if d["best_r"] else "?"
        st.add_row(s, str(d["n"]), str(d["win"]), f"{pct:.0f}%",
            f"[{a_s}]{avg:+.2f}%[/{a_s}]", f"[{b_s}]{d['best']:+.2f}%[/{b_s}]", best_sym)
    console.print(st)

    # RÉSUMÉ PAR CRYPTO
    console.print()
    cryptos = {}
    for r in results:
        c = r["sym"]
        if c not in cryptos: cryptos[c] = {"n":0,"win":0,"roi":0,"best":-999}
        cryptos[c]["n"] += 1
        cryptos[c]["roi"] += r["roi"]
        if r["roi"] > 0: cryptos[c]["win"] += 1
        if r["roi"] > cryptos[c]["best"]: cryptos[c]["best"] = r["roi"]

    ct = Table(title="RÉSUMÉ PAR CRYPTO", show_header=True, header_style="bold cyan")
    ct.add_column("Crypto", width=12)
    ct.add_column("Tests", justify="right", width=7)
    ct.add_column("Rentables", justify="right", width=10)
    ct.add_column("% Rent.", justify="right", width=8)
    ct.add_column("ROI Moy.", justify="right", width=9)
    ct.add_column("Best ROI", justify="right", width=9)

    for c in sorted(cryptos, key=lambda x: cryptos[x]["best"], reverse=True):
        d = cryptos[c]
        avg = d["roi"]/d["n"]
        pct = d["win"]/d["n"]*100
        a_s = "green" if avg >= 0 else "red"
        b_s = "green" if d["best"] >= 0 else "red"
        ct.add_row(c, str(d["n"]), str(d["win"]), f"{pct:.0f}%",
            f"[{a_s}]{avg:+.2f}%[/{a_s}]", f"[{b_s}]{d['best']:+.2f}%[/{b_s}]")
    console.print(ct)

    # RÉSUMÉ PAR TIMEFRAME
    console.print()
    tfs = {}
    for r in results:
        t = r["tf"]
        if t not in tfs: tfs[t] = {"n":0,"win":0,"roi":0,"best":-999}
        tfs[t]["n"] += 1
        tfs[t]["roi"] += r["roi"]
        if r["roi"] > 0: tfs[t]["win"] += 1
        if r["roi"] > tfs[t]["best"]: tfs[t]["best"] = r["roi"]

    tt = Table(title="RÉSUMÉ PAR TIMEFRAME", show_header=True, header_style="bold magenta")
    tt.add_column("Timeframe", width=10)
    tt.add_column("Tests", justify="right", width=7)
    tt.add_column("% Rentable", justify="right", width=10)
    tt.add_column("ROI Moyen", justify="right", width=10)
    tt.add_column("Best ROI", justify="right", width=10)

    for t in sorted(tfs, key=lambda x: tfs[x]["best"], reverse=True):
        d = tfs[t]
        avg = d["roi"]/d["n"]
        pct = d["win"]/d["n"]*100
        tt.add_row(t, str(d["n"]), f"{pct:.0f}%", f"{avg:+.2f}%", f"{d['best']:+.2f}%")
    console.print(tt)

    # VERDICT
    best = results[0]
    profitable = sum(1 for r in results if r["roi"] > 0)
    console.print()
    console.print(Panel(
        f"[bold]{total_tests} tests | {profitable} rentables ({profitable/total_tests*100:.0f}%)[/bold]\n\n"
        f"[bold cyan]🏆 MEILLEURE COMBINAISON ABSOLUE:[/bold cyan]\n"
        f"  Stratégie : [bold green]{best['strat']}[/bold green]\n"
        f"  Crypto    : [bold]{best['sym']}[/bold]\n"
        f"  Timeframe : {best['tf']} | Période: {best['m']} mois\n"
        f"  ROI       : [bold green]{best['roi']:+.2f}%[/bold green]\n"
        f"  Win Rate  : {best['wr']}% | Trades: {best['trades']}\n"
        f"  Params    : {best['params']}\n"
        f"  Capital   : $20.00 → [bold green]${best['final']}[/bold green]",
        title="🏆 VERDICT FINAL", border_style="green",
    ))


if __name__ == "__main__":
    main()
