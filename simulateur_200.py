"""
Simulateur 200 € — ce qu'il te serait resté, frais et impôts payés.

LA QUESTION
    Avec 200 € au départ puis 50 € par mois chez Revolut, combien te
    serait-il resté selon ta façon de jouer ? Le reste du projet mesure
    des signaux en pourcentages, sans frais fixes ni impôt. Ici, on rejoue
    le passé avec un vrai petit portefeuille : 1 € par ordre, le cours
    euro/dollar de chaque jour, et la flat tax sur les gains.

LES FAÇONS DE JOUER
    A. Signaux techniques : acheter ce que le moteur dit « favorable »,
       vendre ce qu'il dit « défavorable ». C'est « vendre au bon moment ».
    B. Top momentum : garder les mieux classées (12 ou 3), revues toutes
       les 10 séances, au rythme des notifications du bot.
    C. Panier des 49 : chaque versement achète, par tranches de 50 €, les
       valeurs les moins représentées, et on garde tout.
    D. ETF S&P 500 coté en euros (simulé avec SPY converti en euros) :
       chaque versement l'achète, on garde tout. Ordre normal, donc 1 €
       minimum, et pas de frais de change puisqu'il se paie en euros.
    E. Le même ETF par un plan d'investissement programmé : 0,25 % sans
       minimum, d'après une page d'aide de Revolut à confirmer pour la France.

CE QUI REND LA MESURE HONNÊTE
    - On ne part pas d'une seule date : chaque mois possible est un
      départ, et on regarde la médiane, le pire et le meilleur.
    - Une décision prise sur la clôture d'un jour s'exécute à la clôture
      du lendemain : aucune information du futur.
    - Tout est compté en euros, avec le vrai cours euro/dollar du jour.
    - L'impôt d'une année se paie l'année suivante, depuis ton compte
      courant, comme en vrai. Les pertes se reportent sur dix ans.

CE QUE ÇA NE DIT PAS
    Ce n'est pas un conseil d'investissement, c'est une mesure du passé,
    avec ses biais. Ils sont rappelés à la fin du rapport.

LANCER
    python3 simulateur_200.py                          # le rapport
    python3 simulateur_200.py --initial 200 --mensuel 50
    python3 simulateur_200.py --sans-change            # si Revolut gardait tes dollars
    python3 simulateur_200.py --verifier               # les cas calculés à la main
"""

import argparse
import json
import os
import sys
import warnings
from dataclasses import dataclass, replace
from datetime import datetime
from functools import partial

import numpy as np
import pandas as pd

# Le dossier du projet, d'où qu'on lance : les cours en cache vivent dans
# data/market, un chemin relatif à ce dossier.
ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
warnings.filterwarnings("ignore")

import stock_engine as engine   # noqa: E402
import stock_eval               # noqa: E402
from rich.console import Console  # noqa: E402
from rich.table import Table      # noqa: E402

console = Console()
RAPPORT = os.path.join("data", "simulateur_200.json")
RAPPORT_SANS_CHANGE = os.path.join("data", "simulateur_200_sans_change.json")


# ══════════════════════════════════════════════════════════════════
#  LES RÉGLAGES
# ══════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Reglages:
    """
    Tous les chiffres qui viennent du monde réel, au même endroit.

    Revolut, formule Standard : 1 € par ordre, vérifié dans l'appli le
    05/11/2026, et aucun ordre gratuit. Le reste vient des sources lues le
    même jour. Ce sont des réglages, pas des vérités : si la grille
    change, on les change ici.
    """
    versement_initial: float = 200.0     # €, le premier jour
    versement_mensuel: float = 50.0      # €, le premier jour de bourse de chaque mois
    frais_minimum: float = 1.0           # € par ordre
    frais_taux: float = 0.0025           # 0,25 % : ne compte qu'au-delà de 400 €
    frais_taux_plan: float = 0.0025      # plan programmé : 0,25 % sans minimum (à confirmer)
    change_gratuit_mois: float = 1000.0  # € convertis sans frais chaque mois, en semaine
    change_taux: float = 0.01            # au-delà de ce quota
    taux_impot: float = 0.314            # flat tax 2026 : 12,8 % + 18,6 %
    report_pertes_ans: int = 10          # une perte s'impute sur les dix années suivantes
    ordre_minimum: float = 10.0          # € : personne ne paie 1 € pour acheter 3 €
    seances_reequilibrage: int = 10      # le rythme du bot : 14 jours ≈ 10 séances


REGLAGES = Reglages()

# Les codes du moteur, dans le même ordre que l'état affiché par le bot :
# neutre, mitigé, favorable, défavorable.
CODES = {"HOLD": 0, "WATCH": 1, "BUY": 2, "SELL": 3}


def frais_ordre(montant, r, plan=False):
    """
    Le frais d'un ordre de `montant` euros.

    Ordre normal : 0,25 % du montant avec 1 € minimum, donc 1 € pour
    tout ordre de moins de 400 €. Plan programmé : 0,25 % sans minimum,
    soit 12,5 centimes sur 50 €.
    """
    if plan:
        return montant * r.frais_taux_plan
    return max(r.frais_minimum, montant * r.frais_taux)


# ══════════════════════════════════════════════════════════════════
#  LE PORTEFEUILLE
# ══════════════════════════════════════════════════════════════════

class Portefeuille:
    """
    Un petit compte-titres en euros, qui achète des fractions d'actions.

    Il tient les comptes que tiendrait le fisc : le prix de revient de
    chaque ligne (frais d'achat compris), les gains réalisés à chaque
    vente, les pertes à reporter, et l'argent sorti de ta poche.

    Les lignes sont repérées par le numéro de leur colonne dans les
    tableaux de cours. `quand` vaut (année, mois).
    """

    def __init__(self, r, en_euros=()):
        self.r = r
        self.en_euros = set(en_euros)  # lignes cotées en euros : pas de change
        self.cash = 0.0        # € en attente sur le compte
        self.parts = {}        # colonne → parts détenues (fractions permises)
        self.revient = {}      # colonne → prix de revient en €, frais compris
        self.verse = 0.0       # € versés pour investir
        self.impots = 0.0      # € d'impôt payés en cours de route, de ta poche
        self.frais = 0.0       # € de frais : ordres et change
        self.frais_change = 0.0  # dont le change seul
        self.ordres = 0
        self.gains = {}        # année → plus-values nettes réalisées en €
        self.pertes = []       # [année, montant] encore reportables
        self._change = {}      # (année, mois) → € déjà convertis ce mois-là

    def verser(self, montant):
        self.cash += montant
        self.verse += montant

    def _frais_change(self, j, montant, quand):
        """
        Le change est gratuit jusqu'à 1 000 € par mois, puis 1 %. Une ligne
        cotée en euros n'en paie pas.
        """
        if j in self.en_euros:
            return 0.0
        deja = self._change.get(quand, 0.0)
        limite = self.r.change_gratuit_mois
        hors_quota = max(0.0, deja + montant - limite) - max(0.0, deja - limite)
        self._change[quand] = deja + montant
        frais = hors_quota * self.r.change_taux
        self.frais_change += frais
        return frais

    def acheter(self, j, montant, prix_usd, eurusd, quand, plan=False):
        """Achète pour `montant` € de la ligne j. Renvoie False si l'ordre est trop petit."""
        montant = min(montant, self.cash)
        if montant < self.r.ordre_minimum:
            return False
        frais = frais_ordre(montant, self.r, plan)
        change = self._frais_change(j, montant - frais, quand)
        net = montant - frais - change
        self.parts[j] = self.parts.get(j, 0.0) + net * eurusd / prix_usd
        self.revient[j] = self.revient.get(j, 0.0) + montant
        self.cash -= montant
        self.frais += frais + change
        self.ordres += 1
        return True

    def vendre(self, j, prix_usd, eurusd, quand):
        """Vend toute la ligne j. Le gain (ou la perte) compte pour l'année."""
        parts = self.parts.pop(j, 0.0)
        if parts <= 0:
            return
        brut = parts * prix_usd / eurusd
        change = self._frais_change(j, brut, quand)
        frais = frais_ordre(brut, self.r)
        produit = brut - change - frais
        annee = quand[0]
        self.gains[annee] = self.gains.get(annee, 0.0) + produit - self.revient.pop(j)
        self.cash += produit
        self.frais += frais + change
        self.ordres += 1

    def valeur(self, prix_usd, eurusd):
        """Ce que vaut le compte ce jour-là, en €, avant frais de sortie."""
        return self.cash + sum(p * prix_usd[j] for j, p in self.parts.items()) / eurusd

    def impot_de_l_annee(self, annee):
        """
        L'impôt sur les gains nets de l'année.

        Gains et pertes d'une même année se compensent. Une perte nette se
        reporte et s'impute sur les gains des dix années suivantes, les
        plus anciennes d'abord.
        """
        net = self.gains.pop(annee, 0.0)
        self.pertes = [[a, m] for a, m in self.pertes
                       if annee - a <= self.r.report_pertes_ans]
        if net < 0:
            self.pertes.append([annee, -net])
            return 0.0
        for perte in self.pertes:
            imputee = min(perte[1], net)
            perte[1] -= imputee
            net -= imputee
        self.pertes = [p for p in self.pertes if p[1] > 1e-9]
        return net * self.r.taux_impot

    def payer_impot(self, annee):
        """L'impôt de l'année, payé l'année suivante depuis ton compte courant."""
        self.impots += self.impot_de_l_annee(annee)

    def liquider(self, prix_usd, eurusd, quand):
        """
        Vend tout le dernier jour et règle l'impôt de l'année.

        Renvoie ce qui te reste vraiment, et cet impôt final.
        """
        for j in list(self.parts):
            self.vendre(j, prix_usd[j], eurusd, quand)
        impot_final = self.impot_de_l_annee(quand[0])
        return self.cash - impot_final, impot_final


# ══════════════════════════════════════════════════════════════════
#  LES DONNÉES
# ══════════════════════════════════════════════════════════════════

@dataclass
class Donnees:
    dates: pd.DatetimeIndex
    annee: np.ndarray       # année de chaque séance
    mois: np.ndarray        # mois de chaque séance
    tickers: list           # les 49 valeurs, puis l'ETF en dernier
    prix: np.ndarray        # séances × colonnes, en dollars (cours ajustés)
    eurusd: np.ndarray      # dollars pour 1 €, chaque séance
    reco: np.ndarray        # séances × 49 : codes du moteur, -1 avant le premier calcul
    score_achat: np.ndarray # séances × 49
    momentum: np.ndarray    # séances × 49, NaN tant qu'il manque un an d'historique
    etf: int                # colonne de l'ETF
    premier: int            # première séance où toutes les décisions sont possibles

    @property
    def n_actions(self):
        return len(self.tickers) - 1


def _jours(index):
    """Des dates sans fuseau ni heure, pour que tout s'aligne."""
    index = pd.DatetimeIndex(index)
    if index.tz is not None:
        index = index.tz_convert(None)
    return index.normalize()


def _serie(valeurs):
    s = pd.Series(valeurs)
    s.index = _jours(s.index)
    return s


def charger(etf="SPY"):
    """
    Les cours en cache (data/market), rejoués par le moteur séance par
    séance, plus l'ETF et le cours euro/dollar, alignés sur les mêmes jours.

    Seules les séances où les 49 valeurs ET l'ETF sont cotés sont gardées,
    comme dans stock_eval.build_matrices.
    """
    tickers = list(engine.UNIVERSE_TICKERS)
    closes, recos, scores, moms = {}, {}, {}, {}
    with console.status("Le moteur rejoue les 49 valeurs sur dix ans…"):
        for t in tickers:
            df = stock_eval.load_prices(t, "10y")
            if df is None or len(df) < 300:
                raise SystemExit(f"{t} : historique insuffisant dans data/market")
            rows = engine.analyze_series(df)
            closes[t] = _serie(engine.normalize(df)["close"])
            recos[t] = _serie({x["date"]: CODES[x["recommendation"]] for x in rows})
            scores[t] = _serie({x["date"]: x["buy_score"] for x in rows})
            moms[t] = _serie({x["date"]: (np.nan if x.get("mom_12_1") is None
                                          else x["mom_12_1"]) for x in rows})
        closes[etf] = _serie(engine.normalize(stock_eval.load_prices(etf, "10y"))["close"])
        fx = _serie(engine.normalize(stock_eval.load_prices("EURUSD=X", "10y"))["close"])

    prix = pd.DataFrame(closes).dropna()
    dates = prix.index
    reco = pd.DataFrame(recos).reindex(dates).fillna(-1).astype(int)
    score = pd.DataFrame(scores).reindex(dates)
    mom = pd.DataFrame(moms).reindex(dates)
    # Le change ne s'échange pas les mêmes jours que Wall Street : on prend
    # le dernier cours connu.
    fx = fx[~fx.index.duplicated()].reindex(dates).ffill().bfill()

    ok = np.isfinite(mom.to_numpy()).all(axis=1)
    if not ok.any():
        raise SystemExit("aucune séance où le momentum des 49 valeurs est calculable")
    premier = int(np.argmax(ok)) + 2   # décisions sur la veille, et l'avant-veille existe

    return Donnees(
        dates=dates,
        annee=dates.year.to_numpy(),
        mois=dates.month.to_numpy(),
        tickers=tickers + [etf],
        prix=prix.to_numpy(dtype=float),
        eurusd=fx.to_numpy(dtype=float),
        reco=reco.to_numpy(),
        score_achat=score.to_numpy(dtype=float),
        momentum=mom.to_numpy(dtype=float),
        etf=len(tickers),
        premier=premier,
    )


# ══════════════════════════════════════════════════════════════════
#  LES FAÇONS DE JOUER
#  Chacune reçoit le jour t et ne lit que les signaux de la veille (t-1),
#  calculés sur la clôture de la veille ; elle exécute à la clôture de t.
# ══════════════════════════════════════════════════════════════════

def _quand(d, t):
    return (int(d.annee[t]), int(d.mois[t]))


def _repartir(d, pf, t, candidates):
    """
    Partage l'argent en attente à parts égales entre les candidates, dans
    leur ordre, sans descendre sous l'ordre minimum.
    """
    k = min(len(candidates), int(pf.cash // pf.r.ordre_minimum))
    if k == 0:
        return
    part = pf.cash / k
    for j in candidates[:k]:
        pf.acheter(j, part, d.prix[t, j], d.eurusd[t], _quand(d, t))


def jouer_signaux(d, pf, t, ctx):
    """A. Acheter ce qui est « favorable », vendre ce qui devient « défavorable »."""
    veille = d.reco[t - 1]
    for j in list(pf.parts):
        if veille[j] == CODES["SELL"]:
            pf.vendre(j, d.prix[t, j], d.eurusd[t], _quand(d, t))
    if pf.cash < pf.r.ordre_minimum:
        return
    candidates = [j for j in range(d.n_actions)
                  if veille[j] == CODES["BUY"] and j not in pf.parts]
    candidates.sort(key=lambda j: -d.score_achat[t - 1, j])
    _repartir(d, pf, t, candidates)


def jouer_momentum(d, pf, t, ctx, top_n=12):
    """
    B. Garder les `top_n` mieux classées au momentum, revues toutes les
    10 séances.

    Version économe en ordres, pour donner sa meilleure chance à la
    stratégie : on ne vend que ce qui sort du classement, l'argent va aux
    nouvelles venues, et s'il n'y en a pas, il renforce la plus petite
    ligne en un seul ordre. Les versements attendent la revue suivante.
    """
    if (t - ctx["debut"]) % pf.r.seances_reequilibrage:
        return
    mom = d.momentum[t - 1]
    classees = [j for j in np.argsort(-mom) if np.isfinite(mom[j])]
    cible = classees[:top_n]
    for j in list(pf.parts):
        if j not in cible:
            pf.vendre(j, d.prix[t, j], d.eurusd[t], _quand(d, t))
    entrantes = [j for j in cible if j not in pf.parts]
    if entrantes:
        _repartir(d, pf, t, entrantes)
    elif pf.cash >= pf.r.ordre_minimum:
        j = min(cible, key=lambda k: pf.parts.get(k, 0.0) * d.prix[t, k])
        pf.acheter(j, pf.cash, d.prix[t, j], d.eurusd[t], _quand(d, t))


def jouer_panier(d, pf, t, ctx):
    """C. Chaque versement achète, par tranches de 50 €, les valeurs les moins représentées."""
    if not ctx["versement"]:
        return
    tranche = max(pf.r.versement_mensuel, pf.r.ordre_minimum)
    while pf.cash >= pf.r.ordre_minimum:
        j = min(range(d.n_actions),
                key=lambda k: (pf.parts.get(k, 0.0) * d.prix[t, k], k))
        if not pf.acheter(j, min(tranche, pf.cash), d.prix[t, j], d.eurusd[t], _quand(d, t)):
            break


def jouer_etf(d, pf, t, ctx, plan=False):
    """D et E. Chaque versement achète l'ETF, et on garde tout."""
    if ctx["versement"]:
        pf.acheter(d.etf, pf.cash, d.prix[t, d.etf], d.eurusd[t], _quand(d, t), plan)


STRATEGIES = [
    ("A. Signaux techniques", jouer_signaux),
    ("B. Top 12 momentum", partial(jouer_momentum, top_n=12)),
    ("B. Top 3 momentum", partial(jouer_momentum, top_n=3)),
    ("C. Panier des 49", jouer_panier),
    ("D. ETF S&P 500, 1 €/ordre", jouer_etf),
    ("E. ETF S&P 500, plan programmé", partial(jouer_etf, plan=True)),
]
REFERENCE = "D. ETF S&P 500, 1 €/ordre"


# ══════════════════════════════════════════════════════════════════
#  LA SIMULATION
# ══════════════════════════════════════════════════════════════════

def simuler(d, jouer, debut, fin, r):
    """Rejoue une façon de jouer de la séance `debut` à la séance `fin`, comprises."""
    pf = Portefeuille(r, en_euros=[d.etf])
    ctx = {"debut": debut, "versement": False}
    creux = 0.0
    for t in range(debut, fin + 1):
        if t > debut and d.annee[t] != d.annee[t - 1]:
            pf.payer_impot(int(d.annee[t - 1]))
        ctx["versement"] = t == debut or d.mois[t] != d.mois[t - 1]
        if ctx["versement"]:
            pf.verser(r.versement_initial if t == debut else r.versement_mensuel)
        jouer(d, pf, t, ctx)
        creux = min(creux, pf.valeur(d.prix[t], d.eurusd[t]) - pf.verse)

    il_reste, impot_final = pf.liquider(d.prix[fin], d.eurusd[fin], _quand(d, fin))
    annees = (d.dates[fin] - d.dates[debut]).days / 365.25
    return {
        "verse": pf.verse,
        "il_reste": il_reste,
        "impots": pf.impots + impot_final,
        "gain_net": il_reste - pf.verse - pf.impots,
        "frais": pf.frais,
        "frais_change": pf.frais_change,
        "ordres_par_an": pf.ordres / annees,
        "creux": creux,
    }


def departs_possibles(d, annees):
    """
    Chaque premier jour de bourse d'un mois dont la fenêtre tient dans les
    données.

    Une fenêtre couvre exactement `annees` × 12 mois : elle s'arrête la
    veille du mois anniversaire. Sinon, un dernier versement tombait
    parfois le dernier jour, acheté puis revendu aussitôt, frais compris.
    """
    departs = []
    for t in range(d.premier, len(d.dates)):
        if d.mois[t] == d.mois[t - 1]:
            continue
        anniversaire = (d.dates[t] + pd.DateOffset(years=annees)).replace(day=1)
        if anniversaire > d.dates[-1]:
            break
        departs.append((t, int(d.dates.searchsorted(anniversaire)) - 1))
    return departs


# ══════════════════════════════════════════════════════════════════
#  LE RAPPORT
# ══════════════════════════════════════════════════════════════════

def eur(x, signe=False):
    """1234.5 → « 1 235 € » ; avec signe → « +1 235 € »."""
    corps = f"{abs(x):,.0f}".replace(",", " ") + " €"
    if signe:
        return ("+" if x >= 0 else "−") + corps
    return ("−" if x < 0 else "") + corps


def mois_annee(date):
    noms = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.",
            "août", "sept.", "oct.", "nov.", "déc."]
    return f"{noms[date.month - 1]} {date.year}"


def rapport(d, r, horizons=(3, 5), fichier=RAPPORT):
    sortie = {
        "date": datetime.now().isoformat(timespec="seconds"),
        "reglages": r.__dict__,
        "donnees": [str(d.dates[0].date()), str(d.dates[-1].date())],
        "horizons": {},
    }
    for h in horizons:
        departs = departs_possibles(d, h)
        if not departs:
            console.print(f"[yellow]Pas assez de données pour {h} ans.[/yellow]")
            continue
        with console.status(f"{h} ans : {len(departs)} départs × {len(STRATEGIES)} façons de jouer…"):
            res = {nom: [simuler(d, jouer, s, f, r) for s, f in departs]
                   for nom, jouer in STRATEGIES}

        verse = res[REFERENCE][0]["verse"]
        table = Table(
            title=(f"{h} ans · {len(departs)} départs, de {mois_annee(d.dates[departs[0][0]])} "
                   f"à {mois_annee(d.dates[departs[-1][0]])} · versé : {eur(verse)}"),
            header_style="bold magenta")
        for col, just in [("Façon de jouer", "left"), ("Gain net", "right"),
                          ("Pire départ", "right"), ("Meilleur", "right"),
                          ("Frais", "right"), ("Impôts", "right"),
                          ("Ordres/an", "right"), ("Creux", "right"),
                          ("Bat l'ETF", "right")]:
            table.add_column(col, justify=just)

        ref = [x["gain_net"] for x in res[REFERENCE]]
        stats = {}
        for nom, runs in res.items():
            gains = np.array([x["gain_net"] for x in runs])
            med = lambda cle: float(np.median([x[cle] for x in runs]))
            bat = float(np.mean(gains > np.array(ref))) if nom != REFERENCE else None
            stats[nom] = {
                "gain_net_median": float(np.median(gains)),
                "pire": float(gains.min()), "meilleur": float(gains.max()),
                "frais_median": med("frais"), "dont_change_median": med("frais_change"),
                "impots_median": med("impots"),
                "ordres_par_an_median": med("ordres_par_an"),
                "creux_median": med("creux"), "bat_etf": bat,
            }
            s = stats[nom]
            couleur = "green" if s["gain_net_median"] >= 0 else "red"
            table.add_row(
                nom,
                f"[{couleur}]{eur(s['gain_net_median'], signe=True)}[/{couleur}]",
                eur(s["pire"], signe=True), eur(s["meilleur"], signe=True),
                eur(s["frais_median"]), eur(s["impots_median"]),
                f"{s['ordres_par_an_median']:.0f}", eur(s["creux_median"]),
                "—" if bat is None else f"{bat:.0%}".replace("%", " %"),
            )
        console.print()
        console.print(table)
        change = [f"{nom.split('.')[0]}{' ' + nom.split()[2] if nom.startswith('B.') else ''} "
                  f"{eur(s['dont_change_median'])}"
                  for nom, s in stats.items() if s["dont_change_median"] >= 0.5]
        if change:
            console.print(f"[dim]Dont frais de change (médiane) : {' · '.join(change)}[/dim]")
        sortie["horizons"][str(h)] = {"departs": len(departs), "verse": verse, "strategies": stats}

    console.print(
        "\n[bold]Comment lire[/bold]\n"
        "• Gain net : ce que tu récupères en vendant tout à la fin, frais et impôt payés,\n"
        "  moins ce que tu as versé, moins les impôts payés en cours de route. Médiane des départs.\n"
        "• Pire départ / Meilleur : le gain net du plus mauvais et du meilleur mois pour commencer.\n"
        "• Creux : au pire moment, combien ton compte valait de moins que ce que tu avais versé.\n"
        "• Bat l'ETF : la part des départs où cette façon de jouer finit devant la ligne D.\n")
    console.print(
        "[bold]Ce que ces chiffres ne disent pas[/bold]\n"
        "• A, B et C choisissent parmi 49 entreprises connues en 2026 : elles ont toutes\n"
        "  survécu. C'est trop beau (biais du survivant). D et E n'ont pas ce biais.\n"
        "• Les cours sont ajustés des dividendes : ils sont réinvestis d'office et imposés\n"
        "  seulement à la vente. En vrai, ils sont imposés chaque année.\n"
        "• Les frais de Revolut et l'impôt de 2026 sont appliqués aux années passées.\n"
        "• Le passé ne dit pas l'avenir. Ce n'est pas un conseil d'investissement.\n")

    os.makedirs("data", exist_ok=True)
    with open(fichier, "w") as f:
        json.dump(sortie, f, indent=2, ensure_ascii=False, default=str)
    console.print(f"[dim]Résultats enregistrés dans {fichier}[/dim]")


# ══════════════════════════════════════════════════════════════════
#  LES VÉRIFICATIONS
#  Des cas assez simples pour être calculés à la main. Si l'un d'eux
#  échoue, aucun chiffre du rapport ne mérite confiance.
# ══════════════════════════════════════════════════════════════════

def verifier():
    r = REGLAGES
    proche = lambda a, b: abs(a - b) < 1e-9
    cas = []

    def ok(nom, condition):
        cas.append(condition)
        console.print(("[green]✓[/green] " if condition else "[red]✗[/red] ") + nom)

    # 1. Les frais d'un ordre
    ok("un ordre de 50 € coûte 1 €", proche(frais_ordre(50, r), 1.0))
    ok("un ordre de 400 € coûte encore 1 €", proche(frais_ordre(400, r), 1.0))
    ok("un ordre de 1 000 € coûte 2,50 € (0,25 %)", proche(frais_ordre(1000, r), 2.5))
    ok("un plan programmé de 50 € coûte 12,5 centimes", proche(frais_ordre(50, r, plan=True), 0.125))

    # 2. Un aller-retour à prix constant ne perd que les deux frais
    pf = Portefeuille(r)
    pf.verser(200)
    pf.acheter(0, 200, 100.0, 1.0, (2020, 1))
    ok("200 € à 100 $ (1 € = 1 $) donnent 1,99 part", proche(pf.parts[0], 1.99))
    pf.vendre(0, 100.0, 1.0, (2020, 1))
    ok("revendu au même prix : il reste 198 €", proche(pf.cash, 198.0))
    ok("la perte de 2 € ne coûte pas d'impôt", proche(pf.impot_de_l_annee(2020), 0.0))
    ok("et elle se reporte", pf.pertes == [[2020, 2.0]])

    # 3. Un gain est imposé à 31,4 %
    pf = Portefeuille(r)
    pf.verser(100)
    pf.acheter(0, 100, 10.0, 1.0, (2021, 3))
    pf.vendre(0, 20.0, 1.0, (2021, 9))
    ok("le cours double : 9,9 parts revendues 198 €, moins 1 € = 197 €", proche(pf.cash, 197.0))
    ok("gain de 97 €, impôt de 30,458 €", proche(pf.impot_de_l_annee(2021), 0.314 * 97))

    # 4. Le report des pertes, et sa limite de dix ans
    pf = Portefeuille(r)
    pf.gains = {2020: -50.0}
    pf.impot_de_l_annee(2020)
    pf.gains[2021] = 80.0
    ok("50 € de perte en 2020, 80 € de gain en 2021 : impôt sur 30 €",
       proche(pf.impot_de_l_annee(2021), 0.314 * 30))
    pf = Portefeuille(r)
    pf.gains = {2020: -50.0}
    pf.impot_de_l_annee(2020)
    pf.gains[2031] = 80.0
    ok("la même perte ne sert plus en 2031 : impôt sur 80 €",
       proche(pf.impot_de_l_annee(2031), 0.314 * 80))

    # 5. Le change
    pf = Portefeuille(r)
    pf.verser(100)
    pf.acheter(0, 100, 123.75, 1.25, (2022, 1))
    ok("à 1,25 $ pour 1 €, 99 € achètent 123,75 $, soit 1 part", proche(pf.parts[0], 1.0))
    pf = Portefeuille(r)
    pf.verser(1200)
    pf.acheter(0, 1200, 1.0, 1.0, (2022, 5))
    ok("1 200 € dans le mois : 3 € d'ordre + 1 % sur les 197 € hors quota = 4,97 €",
       proche(pf.frais, 3.0 + 1.97))
    pf = Portefeuille(r, en_euros=[0])
    pf.verser(1200)
    pf.acheter(0, 1200, 1.0, 1.0, (2022, 5))
    ok("la même chose sur une ligne cotée en euros : 3 € d'ordre, pas de change",
       proche(pf.frais, 3.0))

    # 6. Sur les vrais cours : sans frais ni impôt, la ligne D suit l'ETF en euros
    d = charger()
    r0 = replace(r, frais_minimum=0.0, frais_taux=0.0, change_taux=0.0,
                 taux_impot=0.0, versement_mensuel=0.0)
    debut, fin = departs_possibles(d, 3)[0]
    res = simuler(d, jouer_etf, debut, fin, r0)
    attendu = (200 * d.prix[fin, d.etf] / d.prix[debut, d.etf]
               * d.eurusd[debut] / d.eurusd[fin])
    ok(f"sans frais ni impôt, 200 € dans l'ETF valent {eur(attendu)} au bout de 3 ans, "
       f"comme le cours de SPY converti en euros", abs(res["il_reste"] - attendu) < 1e-6)
    verses = {simuler(d, jouer_etf, s, f, r)["verse"] for s, f in departs_possibles(d, 3)}
    ok("chaque fenêtre de 3 ans reçoit exactement 200 € + 35 × 50 € = 1 950 €",
       verses == {1950.0})

    # 7. Aucune décision ne lit le jour même ni le futur
    d2 = Donnees(**{**d.__dict__})
    d2.reco = d.reco.copy()
    d2.momentum = d.momentum.copy()
    t = debut + 37
    d2.reco[t:] = CODES["SELL"]
    d2.momentum[t:] = np.nan
    pf1, pf2 = Portefeuille(r), Portefeuille(r)
    for pf, dd in ((pf1, d), (pf2, d2)):
        ctx = {"debut": debut, "versement": False}
        for u in range(debut, t + 1):
            ctx["versement"] = u == debut or dd.mois[u] != dd.mois[u - 1]
            if ctx["versement"]:
                pf.verser(200 if u == debut else 50)
            jouer_signaux(dd, pf, u, ctx)
    ok("brouiller les signaux à partir du jour t ne change rien aux décisions de t",
       pf1.parts == pf2.parts and proche(pf1.cash, pf2.cash))

    console.print(f"\n{sum(cas)}/{len(cas)} vérifications réussies")
    return all(cas)


def main():
    parser = argparse.ArgumentParser(description="Ce qu'il te serait resté, frais et impôts payés.")
    parser.add_argument("--initial", type=float, default=REGLAGES.versement_initial,
                        help="versement du premier jour, en € (200 par défaut)")
    parser.add_argument("--mensuel", type=float, default=REGLAGES.versement_mensuel,
                        help="versement de chaque mois, en € (50 par défaut)")
    parser.add_argument("--sans-change", action="store_true",
                        help="aucun frais de change, comme si Revolut gardait tes dollars")
    parser.add_argument("--verifier", action="store_true",
                        help="lancer les cas calculés à la main")
    args = parser.parse_args()

    if args.verifier:
        sys.exit(0 if verifier() else 1)

    r = replace(REGLAGES, versement_initial=args.initial, versement_mensuel=args.mensuel)
    if args.sans_change:
        r = replace(r, change_taux=0.0)
    d = charger()
    console.print(f"[dim]Cours du {d.dates[0].date()} au {d.dates[-1].date()} · "
                  f"{d.n_actions} valeurs + {d.tickers[d.etf]} · "
                  f"{eur(r.versement_initial)} puis {eur(r.versement_mensuel)} par mois · "
                  f"1 € par ordre · impôt {r.taux_impot:.1%}".replace("%", " %")
                  + (" · sans frais de change" if args.sans_change else "") + "[/dim]")
    rapport(d, r, fichier=RAPPORT_SANS_CHANGE if args.sans_change else RAPPORT)


if __name__ == "__main__":
    main()
