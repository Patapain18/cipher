"""
Point de la semaine — ce qui s'est passé en Bourse, en faits.

CE QUE C'EST
    Un résumé de la semaine boursière écoulée : les indices, l'euro face
    au dollar, les plus fortes hausses et baisses des 49 valeurs, les
    secteurs, le classement par momentum, les résultats d'entreprises de
    la semaine qui vient et tes positions.

CE QUE CE N'EST PAS
    Aucune prédiction, aucun « achète », « vends » ou « garde ». Le projet
    a mesuré que les signaux de court terme ne prédisent rien : ce point
    décrit le passé, c'est tout.

QUELLE SEMAINE
    Le week-end, la semaine qui vient de se finir. En semaine, la
    dernière semaine COMPLÈTE : si l'appli Claude était fermée dimanche
    et ne lance la tâche que mardi, le point porte quand même sur une
    semaine entière, pas sur deux séances.

SORTIES
    data/point_semaine.json               le dernier point, lu par le
                                          tableau de bord et la page téléphone
    data/points_semaine/AAAA-MM-JJ.json   un fichier par semaine, daté de
                                          sa dernière séance

LANCER
    python3 point_semaine.py                        # la dernière semaine complète
    python3 point_semaine.py --semaine 2026-10-30   # la semaine qui contient ce jour
"""

import argparse
import json
import os
import sys
import warnings
from datetime import date, datetime, timedelta

import pandas as pd
import yfinance as yf

# Le dossier du projet, d'où qu'on lance : data/ est relatif à ce dossier.
ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
warnings.filterwarnings("ignore")

import stock_engine as engine     # noqa: E402
from rich.console import Console  # noqa: E402
from rich.table import Table      # noqa: E402

console = Console()

SORTIE = os.path.join("data", "point_semaine.json")
HISTORIQUE = os.path.join("data", "points_semaine")
POSITIONS = os.path.join("data", "stock_positions.json")
REEQUILIBRAGE = os.path.join("data", "rebalance_state.json")
REEQUILIBRAGE_JOURS = 14
TOP_N = 12
INDICES = [("S&P 500", "^GSPC"), ("Nasdaq", "^IXIC")]
CHANGE = "EURUSD=X"

AVERTISSEMENT = ("Des faits sur la semaine passée. Aucune prédiction, "
                 "aucun conseil d'achat ou de vente.")
RAPPEL_CLASSEMENT = ("Un classement, pas un ordre d'achat. Mesuré sur 9 ans, le momentum "
                     "bat le hasard, mais pas le simple panier des 49 une fois le risque compté.")

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
        "août", "septembre", "octobre", "novembre", "décembre"]


# ══════════════════════════════════════════════════════════════════
#  LES DONNÉES
# ══════════════════════════════════════════════════════════════════

def _jours(index):
    """Des dates sans fuseau ni heure, pour que toutes les séries s'alignent."""
    index = pd.DatetimeIndex(index)
    if index.tz is not None:
        index = index.tz_convert(None)
    return index.normalize()


def telecharger(symboles, period="2y"):
    """
    Une seule requête groupée pour tous les symboles, comme le bot.

    Deux ans d'historique : le momentum 12-1 en demande 273 séances.
    Renvoie {symbole: DataFrame}, sans ceux pour lesquels rien n'est revenu.
    """
    brut = yf.download(symboles, period=period, interval="1d", progress=False,
                       auto_adjust=True, group_by="ticker", threads=True)
    if brut is None or brut.empty:
        return {}
    sortie = {}
    for s in symboles:
        try:
            df = brut[s] if isinstance(brut.columns, pd.MultiIndex) else brut
        except KeyError:
            continue
        df = df.dropna(how="all")
        if df.empty:
            continue
        df = df[~df.index.duplicated()]
        df.index = _jours(df.index)
        sortie[s] = df
    return sortie


def semaine_cible(seances, jour=None, aujourdhui=None):
    """
    La semaine à raconter, en dates de séances.

    Renvoie (reference, debut, fin, complete) : `reference` est la
    dernière séance de la semaine d'avant, celle qui sert de point de
    départ aux variations ; `debut` et `fin` encadrent la semaine.
    """
    aujourdhui = aujourdhui or date.today()
    if jour is None:
        # Le week-end : la semaine qui vient de finir. En semaine : la
        # précédente, la dernière à être complète.
        jour = aujourdhui if aujourdhui.weekday() >= 5 else aujourdhui - timedelta(days=7)
    lundi = pd.Timestamp(jour - timedelta(days=jour.weekday()))
    dimanche = lundi + pd.Timedelta(days=6)
    dans_la_semaine = seances[(seances >= lundi) & (seances <= dimanche)]
    avant = seances[seances < lundi]
    if dans_la_semaine.empty or avant.empty:
        raise SystemExit(f"Aucune séance la semaine du {lundi.date()} dans les données.")
    vendredi = (lundi + pd.Timedelta(days=4)).date()
    return avant[-1], dans_la_semaine[0], dans_la_semaine[-1], aujourdhui > vendredi


def _cours(df, jour):
    """La clôture de ce jour-là, ou la dernière connue avant (jours fériés, change)."""
    return float(df["Close"].asof(jour))


def _variation(df, reference, fin):
    return _cours(df, fin) / _cours(df, reference) - 1


# ══════════════════════════════════════════════════════════════════
#  LE POINT
# ══════════════════════════════════════════════════════════════════

def construire(jour=None):
    """Calcule le point de la semaine. Renvoie un dict prêt à écrire en JSON."""
    actions = list(engine.UNIVERSE_TICKERS)
    with console.status("Téléchargement des cours…"):
        donnees = telecharger(actions + [s for _, s in INDICES] + [CHANGE])

    if INDICES[0][1] not in donnees or CHANGE not in donnees:
        raise SystemExit("Le S&P 500 ou le cours euro/dollar n'a pas pu être téléchargé.")
    seances = donnees[INDICES[0][1]].index
    reference, debut, fin, complete = semaine_cible(seances, jour)
    fx = donnees[CHANGE]
    fx_ref, fx_fin = _cours(fx, reference), _cours(fx, fin)

    # 1. Les marchés
    marches = []
    for nom, symbole in INDICES:
        df = donnees.get(symbole)
        if df is None:
            continue
        var_usd = _variation(df, reference, fin)
        marches.append({
            "nom": nom, "symbole": symbole,
            "cloture": round(_cours(df, fin), 2),
            "variation_dollars": var_usd,
            "variation_euros": (1 + var_usd) * fx_ref / fx_fin - 1,
        })
    euro = {"cours": round(fx_fin, 4), "variation": fx_fin / fx_ref - 1}

    # 2. Les 49 valeurs et leurs secteurs
    presentes = [t for t in actions if t in donnees and len(donnees[t]) >= 300]
    manquantes = [t for t in actions if t not in presentes]
    lignes = [{"ticker": t, "secteur": engine.sector_of(t),
               "variation": _variation(donnees[t], reference, fin)} for t in presentes]
    lignes.sort(key=lambda x: x["variation"], reverse=True)
    secteurs = {}
    for x in lignes:
        secteurs.setdefault(x["secteur"], []).append(x["variation"])
    secteurs = sorted(({"secteur": s, "variation": sum(v) / len(v), "valeurs": len(v)}
                       for s, v in secteurs.items()),
                      key=lambda x: x["variation"], reverse=True)

    # 3. Le classement par momentum, à la fin de la semaine et à la fin de
    #    la précédente : le même calcul que le bot (engine.analyze_series).
    with console.status("Le moteur recalcule le momentum des 49 valeurs…"):
        momentum = {}
        for t in presentes:
            par_date = {r["date"]: r.get("mom_12_1") for r in engine.analyze_series(donnees[t])}
            momentum[t] = (par_date.get(fin), par_date.get(reference))

    def top(colonne):
        notes = [(t, m[colonne]) for t, m in momentum.items() if m[colonne] is not None]
        notes.sort(key=lambda x: x[1], reverse=True)
        return notes

    maintenant, avant = top(0), top(1)
    rang_avant = {t: i + 1 for i, (t, _) in enumerate(avant)}
    top_maintenant = [t for t, _ in maintenant[:TOP_N]]
    top_avant = [t for t, _ in avant[:TOP_N]]
    classement = {
        "top": [{"rang": i + 1, "ticker": t, "secteur": engine.sector_of(t),
                 "momentum": m, "rang_semaine_derniere": rang_avant.get(t)}
                for i, (t, m) in enumerate(maintenant[:TOP_N])],
        "entrent": [t for t in top_maintenant if t not in top_avant],
        "sortent": [t for t in top_avant if t not in top_maintenant],
        "classees": len(maintenant),
        "rappel": RAPPEL_CLASSEMENT,
    }
    try:
        with open(REEQUILIBRAGE) as f:
            dernier = json.load(f).get("last_rebalance")
        dernier = datetime.fromisoformat(dernier).date()
        classement["dernier_reequilibrage"] = dernier.isoformat()
        classement["prochain_reequilibrage"] = (dernier + timedelta(days=REEQUILIBRAGE_JOURS)).isoformat()
    except (OSError, ValueError, TypeError):
        pass

    # 4. Les résultats d'entreprises de la semaine qui vient
    lundi_suivant = (fin - pd.Timedelta(days=fin.weekday()) + pd.Timedelta(days=7)).date()
    dimanche_suivant = lundi_suivant + timedelta(days=6)
    a_venir = []
    for t, d in engine._lire_cache_earnings().get("dates", {}).items():
        try:
            jour_pub = datetime.strptime(d, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        if lundi_suivant <= jour_pub <= dimanche_suivant and t in actions:
            a_venir.append({"ticker": t, "secteur": engine.sector_of(t), "date": d,
                            "jour": JOURS[jour_pub.weekday()]})
    a_venir.sort(key=lambda x: (x["date"], x["ticker"]))

    # 5. Tes positions, en euros : le cours du dollar le jour de l'achat
    #    compte aussi dans ce que tu as gagné ou perdu.
    positions = None
    try:
        with open(POSITIONS) as f:
            carnet = json.load(f)
    except (OSError, ValueError):
        carnet = {}
    if carnet:
        positions = {"lignes": [], "valeur_euros": 0.0, "cout_euros": 0.0}
        for t, pos in sorted(carnet.items()):
            df = donnees.get(t)
            if df is None:
                continue
            prix = _cours(df, fin)
            calcul = engine.pnl_position(pos, prix, t)
            if not calcul:
                continue
            achat = pd.Timestamp(str(pos.get("entry_date", ""))[:10] or fin)
            valeur = calcul["quantity"] * prix / fx_fin
            cout = calcul["quantity"] * calcul["entry_price"] / _cours(fx, achat)
            positions["lignes"].append({
                "ticker": t, "valeur_euros": valeur, "cout_euros": cout,
                "gain_euros": valeur - cout,
                "variation_semaine": _variation(df, reference, fin),
                "rang_momentum": next((i + 1 for i, (x, _) in enumerate(maintenant) if x == t), None),
            })
            positions["valeur_euros"] += valeur
            positions["cout_euros"] += cout
        positions["gain_euros"] = positions["valeur_euros"] - positions["cout_euros"]

    return {
        "semaine": {
            "reference": reference.date().isoformat(),
            "debut": debut.date().isoformat(),
            "fin": fin.date().isoformat(),
            "seances": int(((seances >= debut) & (seances <= fin)).sum()),
            "complete": bool(complete),
        },
        "calcule_le": datetime.now().isoformat(timespec="seconds"),
        "avertissement": AVERTISSEMENT,
        "marches": marches,
        "euro_dollar": euro,
        "valeurs": {
            "hausses": lignes[:5],
            "baisses": lignes[::-1][:5],
            "suivies": len(presentes),
            "manquantes": manquantes,
        },
        "secteurs": secteurs,
        "classement": classement,
        "resultats_a_venir": a_venir,
        "positions": positions,
        "sources": "Cours ajustés de Yahoo Finance (yfinance) ; calendrier des résultats du bot.",
    }


def en_lignes(point):
    """
    Le point à plat, une ligne par chiffre : le format que lit la page
    téléphone (un tableau d'objets). Chaque ligne dit à quel bloc elle
    appartient (`bloc`) et qui elle est dans ce bloc (`cle`) : le couple
    est unique, c'est ce qui permet à la page de citer chaque chiffre.
    """
    s = point["semaine"]
    lignes = [{"bloc": "semaine", "cle": "semaine", "debut": s["debut"], "fin": s["fin"],
               "seances": s["seances"], "calcule_le": point["calcule_le"]}]
    for m in point["marches"]:
        lignes.append({"bloc": "marche", "cle": m["nom"], "cloture": m["cloture"],
                       "variation_dollars": m["variation_dollars"],
                       "variation_euros": m["variation_euros"]})
    e = point["euro_dollar"]
    lignes.append({"bloc": "change", "cle": "EUR/USD", "cours": e["cours"], "variation": e["variation"]})
    for sens in ("hausses", "baisses"):
        for i, x in enumerate(point["valeurs"][sens], start=1):
            lignes.append({"bloc": sens[:-1], "cle": x["ticker"], "rang": i,
                           "secteur": x["secteur"], "variation": x["variation"]})
    for i, x in enumerate(point["secteurs"], start=1):
        lignes.append({"bloc": "secteur", "cle": x["secteur"], "rang": i,
                       "variation": x["variation"], "valeurs": x["valeurs"]})
    c = point["classement"]
    for x in c["top"]:
        lignes.append({"bloc": "top12", "cle": x["ticker"], "rang": x["rang"], "secteur": x["secteur"],
                       "momentum": x["momentum"], "rang_semaine_derniere": x["rang_semaine_derniere"]})
    lignes += [{"bloc": "entre", "cle": t} for t in c["entrent"]]
    lignes += [{"bloc": "sort", "cle": t} for t in c["sortent"]]
    if c.get("dernier_reequilibrage"):
        lignes.append({"bloc": "reequilibrage", "cle": "bot", "dernier": c["dernier_reequilibrage"],
                       "prochain": c["prochain_reequilibrage"]})
    for x in point["resultats_a_venir"]:
        lignes.append({"bloc": "resultat", "cle": x["ticker"], "date": x["date"],
                       "jour": x["jour"], "secteur": x["secteur"]})
    p = point["positions"]
    if p and p["lignes"]:
        for x in p["lignes"]:
            lignes.append({"bloc": "position", "cle": x["ticker"], "valeur_euros": x["valeur_euros"],
                           "gain_euros": x["gain_euros"], "variation_semaine": x["variation_semaine"],
                           "rang_momentum": x["rang_momentum"]})
        lignes.append({"bloc": "positions_total", "cle": "total", "valeur_euros": p["valeur_euros"],
                       "cout_euros": p["cout_euros"], "gain_euros": p["gain_euros"]})
    return lignes


def enregistrer(point):
    """Écrit le point du jour et sa copie datée, sans jamais laisser un fichier à moitié écrit."""
    os.makedirs(HISTORIQUE, exist_ok=True)
    texte = json.dumps(point, indent=2, ensure_ascii=False)
    for chemin in (SORTIE, os.path.join(HISTORIQUE, point["semaine"]["fin"] + ".json")):
        tmp = chemin + ".tmp"
        with open(tmp, "w") as f:
            f.write(texte)
        os.replace(tmp, chemin)


# ══════════════════════════════════════════════════════════════════
#  L'AFFICHAGE
# ══════════════════════════════════════════════════════════════════

def pct(x, decimales=1):
    """0.0123 → « +1,2 % »."""
    return f"{x * 100:+.{decimales}f} %".replace(".", ",").replace("-", "−")


def nombre(x, decimales=0):
    texte = f"{x:,.{decimales}f}".replace(",", " ").replace(".", ",")
    return texte.replace("-", "−")


def date_longue(iso):
    d = date.fromisoformat(iso)
    return f"{JOURS[d.weekday()]} {d.day} {MOIS[d.month - 1]}"


def couleur(x):
    return "green" if x >= 0 else "red"


def afficher(point):
    s = point["semaine"]
    titre = f"Du {date_longue(s['debut'])} au {date_longue(s['fin'])} {s['fin'][:4]}"
    console.print(f"\n[bold cyan]📅 Point de la semaine[/bold cyan] · {titre} "
                  f"· {s['seances']} séances" + ("" if s["complete"] else " [yellow](semaine en cours)[/yellow]"))
    console.print(f"[dim]{point['avertissement']}[/dim]\n")

    console.print("[bold]Les marchés[/bold]")
    for m in point["marches"]:
        console.print(f"  {m['nom']:<8} {nombre(m['cloture']):>8} pts   "
                      f"[{couleur(m['variation_dollars'])}]{pct(m['variation_dollars'])}[/] en dollars · "
                      f"[{couleur(m['variation_euros'])}]{pct(m['variation_euros'])}[/] en euros")
    e = point["euro_dollar"]
    sens = "pris" if e["variation"] >= 0 else "perdu"
    console.print(f"  1 € = {nombre(e['cours'], 4)} $ : l'euro a {sens} "
                  f"{pct(abs(e['variation'])).lstrip('+')} face au dollar")

    v = point["valeurs"]
    t = Table(title=f"Tes {v['suivies']} valeurs : la semaine", header_style="bold magenta")
    for col in ("Plus fortes hausses", "", "Plus fortes baisses", " "):
        t.add_column(col, justify="right" if col.strip() == "" else "left")
    for h, b in zip(v["hausses"], v["baisses"]):
        t.add_row(f"{h['ticker']} ({h['secteur']})", f"[green]{pct(h['variation'])}[/]",
                  f"{b['ticker']} ({b['secteur']})", f"[red]{pct(b['variation'])}[/]")
    console.print()
    console.print(t)
    if v["manquantes"]:
        console.print(f"[yellow]Sans données cette semaine : {', '.join(v['manquantes'])}[/yellow]")

    console.print("\n[bold]Les secteurs[/bold] [dim](moyenne de leurs valeurs)[/dim]")
    console.print("  " + " · ".join(f"{x['secteur']} [{couleur(x['variation'])}]{pct(x['variation'])}[/]"
                                    for x in point["secteurs"]))

    c = point["classement"]
    console.print(f"\n[bold]Le classement par momentum[/bold] [dim]({c['classees']} valeurs classées)[/dim]")
    console.print("  " + " · ".join(f"{x['rang']}. {x['ticker']}" for x in c["top"]))
    console.print(f"  Entrent dans le top {TOP_N} : {', '.join(c['entrent']) or 'aucune'} · "
                  f"en sortent : {', '.join(c['sortent']) or 'aucune'}")
    if c.get("dernier_reequilibrage"):
        console.print(f"  [dim]Le top {TOP_N} du bot : dernier rééquilibrage le "
                      f"{date_longue(c['dernier_reequilibrage'])}, prochain le "
                      f"{date_longue(c['prochain_reequilibrage'])}.[/dim]")
    console.print(f"  [dim]{c['rappel']}[/dim]")

    console.print("\n[bold]Résultats d'entreprises la semaine prochaine[/bold]")
    if point["resultats_a_venir"]:
        for r in point["resultats_a_venir"]:
            console.print(f"  {date_longue(r['date'])} : {r['ticker']} ({r['secteur']})")
        console.print("  [dim]Une publication annonce un gros mouvement probable, pas son sens.[/dim]")
    else:
        console.print("  [dim]Aucune parmi tes 49 valeurs, d'après le calendrier du bot.[/dim]")

    p = point["positions"]
    console.print("\n[bold]Tes positions[/bold]")
    if not p or not p["lignes"]:
        console.print("  [dim]Aucune enregistrée dans le carnet.[/dim]")
    else:
        for x in p["lignes"]:
            rang = f"{x['rang_momentum']}ᵉ au momentum" if x["rang_momentum"] else "sans rang"
            console.print(f"  {x['ticker']:<6} {nombre(x['valeur_euros'], 2)} € · "
                          f"[{couleur(x['gain_euros'])}]{nombre(x['gain_euros'], 2)} €[/] depuis l'achat · "
                          f"{pct(x['variation_semaine'])} cette semaine · {rang}")
        console.print(f"  Total : {nombre(p['valeur_euros'], 2)} € pour {nombre(p['cout_euros'], 2)} € "
                      f"investis ([{couleur(p['gain_euros'])}]{nombre(p['gain_euros'], 2)} €[/])")
    console.print(f"\n[dim]{point['sources']}[/dim]")


def main():
    parser = argparse.ArgumentParser(description="Le point de la semaine boursière, en faits.")
    parser.add_argument("--semaine", type=date.fromisoformat, default=None,
                        help="un jour de la semaine voulue (AAAA-MM-JJ) ; par défaut la dernière complète")
    parser.add_argument("--silencieux", action="store_true",
                        help="enregistrer sans rien afficher (pour le bot et la tâche programmée)")
    parser.add_argument("--lignes", metavar="FICHIER",
                        help="écrire aussi le point à plat, une ligne par chiffre (pour la page "
                             "téléphone) ; {fin} dans le nom devient la date de la dernière séance")
    args = parser.parse_args()

    point = construire(args.semaine)
    if args.lignes:
        if not point["semaine"]["complete"]:
            console.print("[yellow]Semaine en cours : pas de lignes pour la page téléphone.[/yellow]")
            sys.exit(3)
        chemin = args.lignes.replace("{fin}", point["semaine"]["fin"])
        os.makedirs(os.path.dirname(chemin) or ".", exist_ok=True)
        tmp = chemin + ".tmp"
        with open(tmp, "w") as f:
            json.dump(en_lignes(point), f, indent=1, ensure_ascii=False)
        os.replace(tmp, chemin)
        # Une ligne lisible par la tâche programmée : où est le fichier.
        print(f"LIGNES {chemin}")
    # Une semaine en cours s'affiche, mais ne s'enregistre pas : le
    # tableau de bord et la page téléphone ne doivent montrer que des
    # semaines finies.
    if point["semaine"]["complete"]:
        enregistrer(point)
    if not args.silencieux:
        afficher(point)
        console.print(f"[dim]Enregistré dans {SORTIE}[/dim]" if point["semaine"]["complete"]
                      else "[dim]Semaine en cours : rien n'est enregistré.[/dim]")


if __name__ == "__main__":
    main()
