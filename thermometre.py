"""
Le thermomètre du marché — le risque d'une forte chute, mesuré honnêtement.

CE QUE ÇA FAIT
    Trois aiguilles sur le S&P 500 et ses secteurs. Chacune est lue
    SEULEMENT avec le passé connu à la date de la lecture : aucun regard
    vers le futur.
      1. La tendance : le S&P 500 est sous sa moyenne des 200 dernières
         séances.
      2. L'agitation : la volatilité du dernier mois dépasse 80 % de tout
         ce qu'on avait vu jusque-là.
      3. L'envolée : un secteur a pris au moins 100 % de plus que le
         marché en deux ans. C'est le seuil de l'étude de Harvard
         (Greenwood, Shleifer et You, « Bubbles for Fama », 2019). Mais
         cette étude parle de la chute du SECTEUR lui-même, pas du
         marché : pour une chute du S&P 500, elle s'est allumée trop
         rarement pour conclure (voir la mesure). Les secteurs (dont le
         Nasdaq et les semi-conducteurs) ont été choisis après coup.
    Le niveau du thermomètre = le nombre d'aiguilles allumées (0 à 3).

    Puis la mesure : chaque fin de mois, le niveau, et ce qui s'est passé
    ensuite. La chute mesurée (choix de Mathis, 15/11/2026) : le S&P 500
    perd au moins 20 % dans les 12 mois qui suivent la lecture.

CE QUE ÇA NE FAIT PAS
    Aucune date de krach, aucun ordre, aucun conseil. Une forte chute est
    rare (sept en cinquante ans) : la mesure est donc fragile, et elle
    le dit (marge d'incertitude). Une alarme n'annonce pas une baisse :
    elle dit que les mouvements peuvent être grands, dans les deux sens.

LANCER
    python3 thermometre.py              # la lecture d'aujourd'hui
    python3 thermometre.py --mesurer    # la mesure sur le passé → data/thermometre.json
    python3 thermometre.py --verifier   # les cas calculés à la main

RELU deux fois le 15/11/2026 par des agents indépendants. Première
relecture : aucun regard vers le futur dans les aiguilles ; un léger dans
l'ancienne règle (corrigé : elle agit à la séance suivante), une alarme
mal datée (corrigée), et des chiffres qui pouvaient tromper (ajoutés :
incertitude, ce que l'alarme ne dit pas). Seconde relecture : les fonds
sectoriels étaient comptés AVEC leurs dividendes face à un indice SANS
(corrigé : prix seul partout) ; un secteur refusé par Yahoo disparaissait
en silence (corrigé : signalé, et la mesure refuse de s'écrire) ; la
règle comptait les intérêts à l'abri sans les dividendes investi
(corrigé : deux comparaisons équitables) ; des tests qui laissaient passer
des erreurs (complétés).
"""

import argparse
import json
import math
import os
import sys
import warnings
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
warnings.filterwarnings("ignore")

RAPPORT = os.path.join(ROOT, "data", "thermometre.json")

MARCHE = "^GSPC"                 # le S&P 500 (cours seul, sans dividendes)
ABRI = "^IRX"                    # les bons du Trésor à 3 mois : ce que rapporte l'argent à l'abri
TOTAL = "^SP500TR"               # le S&P 500 dividendes réinvestis (depuis 1988)
# Les « secteurs » : le Nasdaq (seul témoin de la tech avant 1999), les
# onze secteurs du S&P 500 (fonds SPDR, depuis fin 1998), et les
# semi-conducteurs (le secteur de Nvidia, depuis 2000).
SECTEURS = {
    "^IXIC": "Nasdaq",
    "XLK": "Technologie", "XLF": "Finance", "XLE": "Énergie", "XLV": "Santé",
    "XLY": "Consommation discrétionnaire", "XLP": "Consommation de base",
    "XLI": "Industrie", "XLB": "Matériaux", "XLU": "Services publics",
    "XLRE": "Immobilier", "XLC": "Communication", "SMH": "Semi-conducteurs",
}

MOYENNE_JOURS = 200              # la tendance
VOL_JOURS = 21                   # l'agitation : un mois de séances
VOL_SEUIL = 0.80                 # … au-dessus de 80 % du passé connu
VOL_HISTOIRE_MIN = 1260          # 5 ans de passé avant de juger l'agitation
ENVOLEE_JOURS = 504              # deux ans de séances
ENVOLEE_SEUIL = 1.00             # +100 % de plus que le marché
CHUTE_JOURS = 252                # douze mois de séances
CHUTE_SEUIL = 0.20               # −20 %
ALARME = 2                       # le niveau à partir duquel on parle d'alarme
DEBUT_MESURE = "1952-01-01"      # les aiguilles 1 et 2 ont déjà 20 ans de passé
TROP_RARE = 12                   # moins de 12 mois à un niveau : rien à en tirer
TROP_PEU = 24                    # une aiguille allumée moins de 24 mois : trop peu pour conclure

AVERTISSEMENT = ("Un thermomètre, pas une boule de cristal : il dit si le marché est tendu, "
                 "jamais quand il chutera. Les fortes chutes sont rares, la mesure est donc "
                 "fragile. Aucun conseil d'achat ou de vente.")


# ══════════════════════════════════════════════════════════════════
#  LES AIGUILLES (pures : aucun réseau, vérifiées à la main par --verifier)
# ══════════════════════════════════════════════════════════════════

def tendance(cours, jours=MOYENNE_JOURS):
    """Écart du cours à sa moyenne des `jours` dernières séances (−0,03 = 3 % dessous)."""
    moyenne = cours.rolling(jours, min_periods=jours).mean()
    return cours / moyenne - 1


def volatilite(cours, jours=VOL_JOURS):
    """La volatilité du dernier mois, sur un an (écart-type des variations × √252)."""
    return cours.pct_change().rolling(jours, min_periods=jours).std() * math.sqrt(252)


def seuil_agitation(vol, quantile=VOL_SEUIL, histoire_min=VOL_HISTOIRE_MIN):
    """
    Le seuil de l'agitation à chaque date : le quantile de TOUTES les
    volatilités connues jusqu'à cette date (fenêtre qui s'allonge), jamais
    celles d'après. Rien avant `histoire_min` séances de passé.
    """
    return vol.expanding(min_periods=histoire_min).quantile(quantile)


def envolee(secteur, marche, jours=ENVOLEE_JOURS):
    """
    Ce qu'un secteur a pris DE PLUS que le marché en `jours` séances :
    sa variation moins celle du marché (1,0 = +100 % de plus).
    """
    marche = marche.reindex(secteur.index).ffill()
    return (secteur / secteur.shift(jours) - 1) - (marche / marche.shift(jours) - 1)


def chute_a_venir(cours, jours=CHUTE_JOURS, seuil=CHUTE_SEUIL):
    """
    Pour chaque date : le cours touche-t-il au moins −`seuil` (par rapport
    à ce jour-là) dans les `jours` séances suivantes ? NaN quand le futur
    n'est pas encore connu en entier. C'est la SEULE chose qui regarde
    vers l'avant : c'est ce qu'on cherche à mesurer, jamais une aiguille.
    """
    futur = cours[::-1].rolling(jours, min_periods=jours).min()[::-1].shift(-1)
    reponse = (futur <= cours * (1 - seuil)).astype(float)
    reponse[futur.isna()] = np.nan
    return reponse


def rendement_a_venir(cours, jours=CHUTE_JOURS):
    """La variation sur les `jours` séances suivantes (pour dire ce que l'alarme NE dit PAS)."""
    return cours.shift(-jours) / cours - 1


def fins_de_mois(df):
    """La dernière séance de chaque mois."""
    return df.groupby(df.index.to_period("M")).tail(1)


def construire(marche, secteurs):
    """
    Toutes les lectures, séance par séance. `marche` : les cours du S&P 500 ;
    `secteurs` : {symbole: cours}. Chaque colonne ne dépend que du passé,
    sauf `chute` et `a_venir`, qui sont ce qu'on cherche à mesurer.
    """
    df = pd.DataFrame({"cours": marche})
    df["ecart_tendance"] = tendance(marche)
    df["tendance"] = (df["ecart_tendance"] < 0).astype(float)
    df.loc[df["ecart_tendance"].isna(), "tendance"] = np.nan

    df["vol"] = volatilite(marche)
    df["seuil_vol"] = seuil_agitation(df["vol"])
    df["agitation"] = (df["vol"] > df["seuil_vol"]).astype(float)
    df.loc[df["seuil_vol"].isna() | df["vol"].isna(), "agitation"] = np.nan

    nets = pd.DataFrame({s: envolee(c, marche).reindex(df.index) for s, c in secteurs.items()})
    df["envolee_max"] = nets.max(axis=1, skipna=True)
    df["envolee_secteur"] = nets.idxmax(axis=1, skipna=True) if not nets.empty else None
    df["envolee"] = (df["envolee_max"] >= ENVOLEE_SEUIL).astype(float)
    df.loc[df["envolee_max"].isna(), "envolee"] = np.nan

    df["niveau"] = df[["tendance", "agitation", "envolee"]].fillna(0).sum(axis=1)
    df["complet"] = df[["tendance", "agitation", "envolee"]].notna().all(axis=1)
    df["chute"] = chute_a_venir(marche)
    df["a_venir"] = rendement_a_venir(marche)
    return df


# ══════════════════════════════════════════════════════════════════
#  LA MESURE
# ══════════════════════════════════════════════════════════════════

def _taux(serie):
    n = int(serie.notna().sum())
    k = int(serie.sum()) if n else 0
    return {"mois": n, "chutes": k, "taux": (k / n) if n else None}


def _episodes(masque):
    """Les suites de mois consécutifs où `masque` est vrai : [(début, fin), …]."""
    episodes, debut, prec = [], None, None
    for t, v in masque.items():
        if v and debut is None:
            debut = t
        if not v and debut is not None:
            episodes.append((debut, prec))
            debut = None
        prec = t
    if debut is not None:
        episodes.append((debut, prec))
    return episodes


def _fusionner(episodes, trou_max=3):
    """
    Deux suites séparées par un trou de `trou_max` mois au plus forment UN
    épisode : la chute de 2000-2002, par exemple, a un mois sans « −20 %
    devant » au milieu, mais c'est la même chute.
    """
    fusion = []
    for a, b in episodes:
        if fusion and (a.to_period("M") - fusion[-1][1].to_period("M")).n <= trou_max + 1:
            fusion[-1] = (fusion[-1][0], b)
        else:
            fusion.append((a, b))
    return fusion


def _continuite(mensuel):
    """Les mois doivent se suivre sans trou : sinon un rendement couvrirait deux mois en silence."""
    if len(mensuel) < 2:
        return
    p = mensuel.index.to_period("M")
    attendu = (p[-1] - p[0]).n + 1
    if len(mensuel) != attendu:
        raise ValueError(f"il manque {attendu - len(mensuel)} fin(s) de mois entre {p[0]} et {p[-1]} : mesure arrêtée")


def _bilan(variations, debut, fin):
    """Rendement par an et pire baisse (jour par jour) d'une suite de variations quotidiennes."""
    valeur = (1 + variations).cumprod()
    annees = (fin - debut).days / 365.25
    return {"par_an": float(valeur.iloc[-1] ** (1 / annees) - 1),
            "pire_baisse": float((valeur / valeur.cummax() - 1).min())}


def regle_simple(df, mensuel, seuil=ALARME, abri=None, total=None):
    """
    La règle « à l'abri quand le niveau atteint `seuil` », jugée JOUR PAR
    JOUR. La lecture de fin de mois n'est connue qu'APRÈS la clôture : la
    décision est donc exécutée à la clôture de la première séance du mois
    suivant, et tient jusqu'à la première séance du mois d'après. Aucun
    regard vers le futur.

    Deux comparaisons, chacune ÉQUITABLE (les mêmes revenus des deux côtés) :
      - « prix seul » : ni dividendes quand on est investi, ni intérêts à
        l'abri (depuis 1973) ;
      - « avec revenus » : les dividendes quand on est investi (`total`,
        le S&P 500 dividendes réinvestis, ^SP500TR, depuis 1988) ET les
        intérêts à l'abri (`abri`, les bons du Trésor à 3 mois, ^IRX).
    Compter les intérêts à l'abri sans les dividendes investi flatterait la
    règle (relu le 15/11/2026).
    """
    cours = df["cours"]
    jours = pd.Series(cours.index, index=cours.index)
    premieres = jours.groupby(cours.index.to_period("M")).first()       # mois → sa première séance

    investi = (mensuel["niveau"] < seuil).astype(float)
    mois_effet = investi.index.to_period("M") + 1                        # la décision vaut le mois SUIVANT
    dates_effet = premieres.reindex(mois_effet).to_numpy()
    effet = pd.Series(investi.to_numpy(), index=dates_effet)
    effet = effet[pd.notna(effet.index)]                                 # le mois qui suit la dernière lecture n'existe pas encore
    # Exécutée à la CLÔTURE de cette première séance : la position ne joue
    # qu'à partir de la variation de la séance suivante.
    position = effet.reindex(cours.index).ffill().shift(1)
    garde = position.notna()

    def comparer(variations, a_l_abri, masque):
        pos, var, abri_jour = position[masque], variations[masque], a_l_abri[masque]
        debut, fin = pos.index[0], pos.index[-1]
        return {"debut": f"{debut:%Y-%m-%d}", "fin": f"{fin:%Y-%m-%d}",
                "regle": _bilan(var * pos + abri_jour * (1 - pos), debut, fin),
                "toujours": _bilan(var, debut, fin)}

    zero = pd.Series(0.0, index=cours.index)
    resultat = {
        "temps_investi": float(position[garde].mean()),
        "changements": int((investi.diff().abs() > 0).sum()),
        "prix_seul": comparer(cours.pct_change(), zero, garde),
    }
    if abri is not None and total is not None:
        var_total = total.reindex(cours.index).ffill().pct_change()
        taux_jour = abri.reindex(cours.index).ffill() / 100 / 252
        masque = garde & var_total.notna() & taux_jour.notna()
        if masque.sum() > 252:
            resultat["avec_revenus"] = comparer(var_total, taux_jour, masque)
    variantes = [resultat[k] for k in ("prix_seul", "avec_revenus") if k in resultat]
    ecarts = [v["regle"]["par_an"] - v["toujours"]["par_an"] for v in variantes]
    resultat["ecart_par_an"] = [min(ecarts), max(ecarts)]
    return resultat


def intervalle_blocs(alarme, chute, bloc=24, tirages=2000, graine=1, niveau=0.90):
    """
    La marge d'incertitude du taux de chute après une alarme, par
    rééchantillonnage en blocs de `bloc` mois : des mois voisins partagent
    presque toute leur fenêtre de 12 mois, on ne peut pas les tirer un par
    un comme s'ils étaient indépendants.
    """
    rng = np.random.default_rng(graine)
    a = alarme.to_numpy(bool)
    c = chute.to_numpy(float)
    n = len(a)
    taux = []
    for _ in range(tirages):
        debuts = rng.integers(0, n, size=math.ceil(n / bloc))
        idx = np.concatenate([np.arange(d, d + bloc) % n for d in debuts])[:n]
        if a[idx].any():
            taux.append(c[idx][a[idx]].mean())
    if not taux:                       # aucune alarme dans l'histoire : pas de marge à donner
        return None
    bas = (1 - niveau) / 2
    return [float(np.quantile(taux, bas)), float(np.quantile(taux, 1 - bas))]


def _stats_a_venir(serie):
    s = serie.dropna()
    if not len(s):
        return None
    return {"mois": int(len(s)), "moyenne": float(s.mean()), "mediane": float(s.median()),
            "hausse_20": float((s >= 0.20).mean()), "ecart_type": float(s.std())}


def mesurer(df, debut=DEBUT_MESURE, abri=None, total=None):
    """La mesure honnête, sur les fins de mois où le futur à 12 mois est connu."""
    mensuel = fins_de_mois(df.loc[debut:])
    connu = mensuel[mensuel["chute"].notna()]
    complet = connu[connu["complet"]]
    _continuite(connu)
    _continuite(complet)

    rapport = {
        "maj": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "chute": f"le S&P 500 perd au moins {CHUTE_SEUIL:.0%} dans les 12 mois qui suivent".replace("%", " %"),
        "periode": {"debut": f"{connu.index[0]:%Y-%m}", "fin": f"{connu.index[-1]:%Y-%m}",
                    "debut_complet": f"{complet.index[0]:%Y-%m}" if len(complet) else None},
        "base": _taux(connu["chute"]),
        "base_complet": _taux(complet["chute"]),
        "aiguilles": {},
        "niveaux": [],
    }
    for nom in ("tendance", "agitation", "envolee"):
        dispo = connu[connu[nom].notna()]
        allumee = _taux(dispo.loc[dispo[nom] == 1, "chute"])
        rapport["aiguilles"][nom] = {
            "depuis": f"{dispo.index[0]:%Y-%m}" if len(dispo) else None,
            "allumee": allumee,
            "eteinte": _taux(dispo.loc[dispo[nom] == 0, "chute"]),
            "trop_peu": allumee["mois"] < TROP_PEU,
        }
    for niveau in range(4):
        mois = complet.loc[complet["niveau"] == niveau]
        ligne = {"niveau": niveau, **_taux(mois["chute"]), "trop_rare": len(mois) < TROP_RARE}
        if len(mois) < TROP_RARE:
            ligne["annees"] = sorted({int(t.year) for t in mois.index})
        rapport["niveaux"].append(ligne)

    # Les épisodes, comptés une fois chacun : des mois qui se suivent ne
    # sont pas des cas indépendants, et une même chute ne compte qu'une fois.
    alarme = complet["niveau"] >= ALARME
    devant = complet["chute"] == 1
    chutes = _fusionner(_episodes(devant))
    cours = df["cours"]
    plus_haut_1an = cours.rolling(CHUTE_JOURS, min_periods=1).max()
    detail = []
    for a, b in chutes:
        # Une alarme ne compte que sur un mois où les −20 % sont VRAIMENT
        # devant (pas sur un mois de trou ajouté par la fusion).
        sonnee = (alarme & devant).loc[a:b]
        premiere = sonnee[sonnee].index[0] if sonnee.any() else None
        # Le creux de la chute : le plus bas cours jusqu'à 12 mois après le dernier mois de l'épisode.
        fenetre = cours.loc[a:].iloc[:cours.loc[a:b].shape[0] + CHUTE_JOURS]
        ligne = {"debut": f"{a:%Y-%m}", "fin": f"{b:%Y-%m}", "creux": f"{fenetre.idxmin():%Y-%m}",
                 "vue": premiere is not None}
        if premiere is not None:
            apres = cours.loc[premiere:].iloc[1:CHUTE_JOURS + 1]
            ligne.update({
                "alarme": f"{premiere:%Y-%m}",
                # Ce que le S&P avait déjà perdu depuis son plus haut d'un an,
                # au moment de l'alarme : une alarme tardive le dit ici.
                "deja_perdu": float(cours.loc[premiere] / plus_haut_1an.loc[premiere] - 1),
                # Et ce qu'il a encore perdu dans les 12 mois après l'alarme.
                "encore_perdu": float(apres.min() / cours.loc[premiere] - 1),
            })
        detail.append(ligne)
    fausses = [e for e in _fusionner(_episodes(alarme)) if not devant.loc[e[0]:e[1]].any()]
    deja_fausses = [float(cours.loc[a] / plus_haut_1an.loc[a] - 1) for a, _ in fausses]
    rapport["alarme"] = {
        "seuil": ALARME,
        "mois": int(alarme.sum()),
        "chute_apres": _taux(complet.loc[alarme, "chute"]),
        "intervalle": intervalle_blocs(alarme, complet["chute"]),
        "episodes_chute": len(chutes),
        "episodes_vus": sum(1 for d in detail if d["vue"]),
        "fausses_alarmes": len(fausses),
        "fausses": [{"debut": f"{a:%Y-%m}", "fin": f"{b:%Y-%m}"} for a, b in fausses],
        "fausses_deja_perdu": [min(deja_fausses), max(deja_fausses)] if deja_fausses else None,
        "chutes": detail,
    }
    # Ce que l'alarme ne dit PAS : la suite sur 12 mois, en moyenne.
    rapport["a_venir"] = {"apres_alarme": _stats_a_venir(complet.loc[alarme, "a_venir"]),
                          "tous": _stats_a_venir(complet["a_venir"])}
    tous_complets = mensuel[mensuel["complet"]]
    _continuite(tous_complets)
    rapport["regle"] = regle_simple(df, tous_complets, abri=abri, total=total)
    rapport["avertissement"] = AVERTISSEMENT
    return rapport


def lecture(df, manquants=(), maintenant=None):
    """
    Les aiguilles à la dernière séance connue.
    `manquants` : les secteurs que Yahoo n'a pas donnés. Sans eux, une
    envolée « éteinte » n'est pas sûre (un seul secteur suffit à
    l'allumer) : elle devient « inconnue », et le niveau un minimum.
    `provisoire` : la dernière séance est celle d'aujourd'hui, et la
    Bourse de New York n'a pas encore clôturé (cours du moment).
    """
    d = df.iloc[-1]
    secteur = d["envolee_secteur"] if isinstance(d["envolee_secteur"], str) else None
    allumee = bool(d["envolee"] == 1)
    inconnue = bool(manquants) and not allumee
    ny = (maintenant or datetime.now(timezone.utc)).astimezone(ZoneInfo("America/New_York"))
    provisoire = df.index[-1].date() == ny.date() and (ny.hour, ny.minute) < (16, 0)
    return {
        "date": f"{df.index[-1]:%Y-%m-%d}",
        "provisoire": bool(provisoire),
        "niveau": int(d["niveau"]),
        "niveau_minimum": inconnue,
        "tendance": {"allumee": bool(d["tendance"] == 1), "ecart": _nombre(d["ecart_tendance"])},
        "agitation": {"allumee": bool(d["agitation"] == 1), "vol": _nombre(d["vol"]),
                      "seuil": _nombre(d["seuil_vol"])},
        "envolee": {"allumee": allumee, "inconnue": inconnue, "net": _nombre(d["envolee_max"]),
                    "secteur": SECTEURS.get(secteur, secteur), "symbole": secteur,
                    "manquants": [SECTEURS.get(m, m) for m in manquants]},
    }


def _nombre(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)


# ══════════════════════════════════════════════════════════════════
#  LES COURS (Yahoo, comme le reste du bot)
# ══════════════════════════════════════════════════════════════════

def _nettoyer(c):
    """Index sans fuseau, à minuit, trié, sans date en double (Yahoo en renvoie parfois)."""
    c = c.dropna()
    c.index = pd.DatetimeIndex(c.index).tz_localize(None).normalize()
    c = c[~c.index.duplicated(keep="last")]
    return c.sort_index()


def _cours(symbole, periode, essais=3):
    """
    Les cours de clôture d'un symbole, PRIX SEUL (sans les dividendes
    réinvestis) : les fonds sectoriels se comparent ainsi au S&P 500, qui
    est lui aussi un prix seul. Yahoo refuse parfois une demande quand on
    en fait beaucoup d'un coup : on réessaie après une pause (2 s, puis
    6 s) avant d'abandonner.
    """
    import time
    import yfinance as yf
    erreur = None
    for n in range(essais):
        try:
            h = yf.Ticker(symbole).history(period=periode, interval="1d", auto_adjust=False)
            if h is not None and not h.empty:
                return _nettoyer(h["Close"])
            erreur = "réponse vide"
        except Exception as e:          # yfinance lève des erreurs variées quand Yahoo refuse
            erreur = str(e)
        if n < essais - 1:
            time.sleep(2 + 4 * n)
    raise ValueError(f"aucun cours reçu pour {symbole} ({erreur})")


def charger(periode_marche="max", periode_secteurs="max", essais_secteurs=3):
    """
    Le S&P 500, les secteurs, et la liste des secteurs que Yahoo n'a pas
    donnés (jamais oubliés en silence). L'agitation compare au passé
    ENTIER du S&P 500 ; l'envolée ne regarde que deux ans en arrière :
    pour la lecture du jour, trois ans de secteurs suffisent.
    """
    marche = _cours(MARCHE, periode_marche)
    secteurs, manquants = {}, []
    for s in SECTEURS:
        try:
            secteurs[s] = _cours(s, periode_secteurs, essais_secteurs)
        except ValueError:
            manquants.append(s)
    return marche, secteurs, manquants + en_retard(marche, secteurs)


def en_retard(marche, secteurs):
    """
    Les secteurs qui n'ont pas de cours à la dernière séance du S&P 500
    (Yahoo en retard, ou une clôture vide) : ils sortiraient du maximum
    de l'envolée sans rien dire. On les compte comme manquants.
    """
    derniere = marche.index[-1]
    return [s for s, c in secteurs.items() if derniere not in c.index]


def lire_rapport():
    try:
        with open(RAPPORT) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def thermometre():
    """Ce que lit le tableau de bord : la lecture du jour, et la mesure sur le passé."""
    marche, secteurs, manquants = charger("max", "3y", essais_secteurs=2)
    df = construire(marche, secteurs)
    return {"lecture": lecture(df, manquants), "mesure": lire_rapport(), "avertissement": AVERTISSEMENT}


# ══════════════════════════════════════════════════════════════════
#  LES VÉRIFICATIONS (des séries inventées, calculées à la main)
# ══════════════════════════════════════════════════════════════════

def verifier():
    cas = []

    def ok(nom, condition):
        cas.append(bool(condition))
        print(("✓ " if condition else "✗ ") + nom)

    jours = pd.bdate_range("2000-01-03", periods=900)

    # 1. La tendance : 300 séances à 100, puis une baisse à 90.
    s = pd.Series(100.0, index=jours[:400])
    s.iloc[300:] = 90.0
    e = tendance(s)
    ok("tendance : rien avant 200 séances de passé", e.iloc[:199].isna().all())
    ok("tendance : au niveau de sa moyenne, écart nul", abs(e.iloc[250]) < 1e-12)
    ok("tendance : le jour de la baisse à 90, l'écart est négatif (90 / 99,95 − 1)",
       abs(e.iloc[300] - (90 / ((199 * 100 + 90) / 200) - 1)) < 1e-12)

    # 2. La chute à venir : −25 % dans 100 séances → oui ; −15 % → non.
    c = pd.Series(100.0, index=jours[:600])
    c.iloc[100:120] = 75.0
    ch = chute_a_venir(c)
    ok("chute : à la séance 0, le cours tombe à 75 dans les 252 suivantes → oui", ch.iloc[0] == 1)
    ok("chute : à la séance 130, plus de baisse devant → non", ch.iloc[130] == 0)
    ok("chute : le futur pas encore connu en entier → inconnu (NaN)", ch.iloc[-1:].isna().all())
    c2 = pd.Series(100.0, index=jours[:600])
    c2.iloc[100:120] = 85.0
    ok("chute : −15 % seulement → non", chute_a_venir(c2).iloc[0] == 0)
    ok("chute : la séance elle-même ne compte pas (le lendemain d'abord)",
       chute_a_venir(pd.Series([100.0, 79.0] + [79.0] * 300, index=jours[:302])).iloc[1] == 0)

    # 3. L'envolée : le secteur grimpe pendant que le marché ne bouge pas.
    m = pd.Series(100.0, index=jours[:600])
    sec = pd.Series(np.linspace(100, 250, 600), index=jours[:600])
    env = envolee(sec, m)
    attendu = sec.iloc[550] / sec.iloc[550 - 504] - 1
    ok(f"envolée : {attendu * 100:+.0f} % de plus qu'un marché immobile", abs(env.iloc[550] - attendu) < 1e-12)
    ok("envolée : rien avant deux ans de passé", env.iloc[:504].isna().all())

    # 4. Aucun regard vers le futur, sur 2 000 séances (l'agitation a donc
    #    ses 5 ans de passé) et avec un secteur qui n'a pas le même
    #    calendrier que le marché (des séances manquantes).
    rng = np.random.default_rng(7)
    long = pd.bdate_range("1990-01-01", periods=2000)
    base = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 2000))), index=long)
    secteur = (base * np.exp(np.cumsum(rng.normal(0, 0.012, 2000)))).drop(long[::7])
    coupe = 1700
    entier = construire(base, {"X": secteur})
    tronque = construire(base.iloc[:coupe], {"X": secteur.loc[:long[coupe - 1]]})
    colonnes = ["ecart_tendance", "tendance", "vol", "seuil_vol", "agitation", "envolee_max", "envolee", "niveau"]
    ok("l'agitation a bien des valeurs dans ce test (5 ans de passé atteints)",
       entier["agitation"].iloc[1300:coupe].notna().all())
    ok("aucun regard vers le futur : couper les données à la séance 1 700 ne change AUCUNE aiguille avant",
       entier[colonnes].iloc[:coupe].fillna(-9).equals(tronque[colonnes].fillna(-9)))
    krach = base.copy()
    krach.iloc[coupe:] *= 0.5
    ok("… alors que la chute à venir, elle, change (c'est la cible)",
       not construire(krach, {"X": secteur})["chute"].iloc[:coupe].fillna(-9).equals(entier["chute"].iloc[:coupe].fillna(-9)))

    # 5. Le seuil de l'agitation ne voit que le passé.
    v = pd.Series(np.arange(1, 2001, dtype=float), index=pd.bdate_range("1990-01-01", periods=2000))
    q = seuil_agitation(v, 0.8, 1260)
    ok("agitation : à la séance 1500, le seuil est le quantile 80 % de 1 à 1501",
       abs(q.iloc[1500] - np.quantile(np.arange(1, 1502), 0.8)) < 1e-9)

    # 6. La règle simple, jour par jour, exécutée à la séance suivante.
    j = pd.bdate_range("2001-01-01", "2002-12-31")
    plat = pd.Series(100.0, index=j)
    plat.loc["2001-07-10":] = 50.0                                  # −50 % le 10 juillet 2001
    d = pd.DataFrame({"cours": plat})
    mens = fins_de_mois(d.assign(niveau=0.0))
    sans = regle_simple(d, mens)["prix_seul"]
    ok("règle : sans alarme, elle fait exactement comme « tout garder »",
       abs(sans["regle"]["par_an"] - sans["toujours"]["par_an"]) < 1e-12)
    alerte_juin = mens.copy()
    alerte_juin.loc["2001-06", "niveau"] = 3.0                     # alarme à la fin de juin
    r = regle_simple(d, alerte_juin)["prix_seul"]["regle"]
    ok("règle : une alarme fin juin évite le −50 % du 10 juillet (exécutée le 2 juillet)", r["pire_baisse"] == 0)
    alerte_juillet = mens.copy()
    alerte_juillet.loc["2001-07", "niveau"] = 3.0                  # alarme APRÈS la chute
    r = regle_simple(d, alerte_juillet)["prix_seul"]["regle"]
    ok("règle : une alarme fin juillet arrive trop tard : le −50 % est subi", abs(r["pire_baisse"] + 0.5) < 1e-12)
    premier = pd.Series(100.0, index=j)
    premier.loc["2001-07-02":] = 50.0                               # la chute TOMBE sur la première séance de juillet
    r = regle_simple(pd.DataFrame({"cours": premier}), alerte_juin)["prix_seul"]["regle"]
    ok("règle : exécutée à la CLÔTURE du 2 juillet, elle subit une chute de ce jour-là (pas de triche)",
       abs(r["pire_baisse"] + 0.5) < 1e-12)

    # 7. Les fins de mois : la dernière séance de chaque mois.
    fm = fins_de_mois(pd.DataFrame({"x": 1}, index=pd.bdate_range("2026-01-01", "2026-03-31")))
    ok("fins de mois : 30/01, 27/02 et 31/03/2026",
       [f"{t:%d/%m}" for t in fm.index] == ["30/01", "27/02", "31/03"])

    # 8. Les épisodes : des suites, fusionnées quand le trou fait 3 mois au plus.
    idx = pd.period_range("2000-01", periods=8, freq="M").to_timestamp()
    suites = _episodes(pd.Series([1, 1, 0, 0, 1, 0, 1, 1], index=idx) == 1)
    ok("épisodes : 1 1 0 0 1 0 1 1 → trois suites", len(suites) == 3)
    ok("épisodes : des trous de 1 et 2 mois ne coupent pas une chute → une seule", len(_fusionner(suites, 3)) == 1)
    idx2 = pd.period_range("2000-01", periods=10, freq="M").to_timestamp()
    ok("épisodes : un trou de 3 mois exactement ne la coupe pas non plus",
       len(_fusionner(_episodes(pd.Series([1, 0, 0, 0, 1, 0, 0, 0, 0, 0], index=idx2) == 1), 3)) == 1)
    ok("épisodes : un trou de 4 mois sépare deux chutes",
       len(_fusionner(_episodes(pd.Series([1, 0, 0, 0, 0, 1, 0, 0, 0, 0], index=idx2) == 1), 3)) == 2)

    # 9. La mesure : une alarme qui tombe seulement sur un mois de trou ne « voit » pas la chute.
    jm = pd.bdate_range("2003-01-01", "2006-12-31")
    dm = pd.DataFrame({"cours": 100.0, "tendance": 0.0, "agitation": 0.0, "envolee": 0.0,
                       "niveau": 0.0, "complet": True, "chute": 0.0, "a_venir": 0.0}, index=jm)
    fins = fins_de_mois(dm).index
    for t in fins[10:12].append(fins[13:14]):
        dm.loc[t, "chute"] = 1.0                                    # chute devant aux mois 10, 11 et 13
    dm.loc[fins[12], "niveau"] = 2.0                                # alarme au mois 12 seulement (le trou)
    rm = mesurer(dm, debut="2003-01-01")
    ok("mesure : une seule chute (le trou du mois 12 est fusionné)", rm["alarme"]["episodes_chute"] == 1)
    ok("mesure : l'alarme du mois de trou ne compte pas comme « vue »", rm["alarme"]["episodes_vus"] == 0)
    trou = dm[dm.index.to_period("M") != fins[20].to_period("M")]     # un mois ENTIER manquant
    try:
        mesurer(trou, debut="2003-01-01")
        ok("mesure : un mois manquant arrête la mesure", False)
    except ValueError:
        ok("mesure : un mois manquant arrête la mesure (au lieu de fausser la règle en silence)", True)

    # 10. Le SENS de chaque aiguille dans `construire` (une aiguille inversée doit échouer).
    c1 = construire(s, {})
    ok("aiguille tendance : allumée sous sa moyenne (séance 300), éteinte à sa moyenne (séance 250)",
       c1["tendance"].iloc[300] == 1 and c1["tendance"].iloc[250] == 0)
    calme = np.r_[np.tile([0.002, -0.002], 750), np.tile([0.03, -0.03], 20)]
    serie_a = pd.Series(100 * np.exp(np.cumsum(calme)), index=pd.bdate_range("1990-01-01", periods=1540))
    c2 = construire(serie_a, {})
    ok("aiguille agitation : allumée quand la volatilité bondit, éteinte au calme",
       c2["agitation"].iloc[1535] == 1 and c2["agitation"].iloc[1400] == 0)
    m3 = pd.Series(100.0, index=jours[:600])
    lent = pd.Series(np.linspace(100, 150, 600), index=jours[:600])          # environ +40 % : pas assez
    fort = pd.Series(np.linspace(100, 250, 600), index=jours[:600])          # plus de +100 %
    c3 = construire(m3, {"LENT": lent, "FORT": fort})
    c4 = construire(m3, {"LENT": lent})
    ok("aiguille envolée : un secteur à plus de +100 % l'allume, et c'est lui qui est nommé",
       c3["envolee"].iloc[550] == 1 and c3["envolee_secteur"].iloc[550] == "FORT")
    ok("aiguille envolée : un secteur à +40 % seulement la laisse éteinte", c4["envolee"].iloc[550] == 0)
    monte = pd.Series(np.linspace(100, 160, 600), index=jours[:600])
    ok("envolée : un secteur immobile quand le marché monte est EN RETARD (valeur négative)",
       abs(envolee(m3, monte).iloc[550] + (monte.iloc[550] / monte.iloc[46] - 1)) < 1e-12)

    # 11. Les autres calculs, un par un.
    r3 = rendement_a_venir(pd.Series([100.0, 110.0, 121.0], index=jours[:3]), 1)
    ok("rendement à venir : il regarde vers l'avant (110 / 100 − 1), pas vers l'arrière",
       abs(r3.iloc[0] - 0.10) < 1e-12 and np.isnan(r3.iloc[-1]))
    alt = pd.Series(100 * np.cumprod(np.r_[1, np.tile([1.01, 0.99], 30)]), index=jours[:61])
    ok("volatilité : l'écart-type des 21 dernières variations × √252",
       abs(volatilite(alt).iloc[60] - alt.pct_change().iloc[40:61].std() * math.sqrt(252)) < 1e-12)
    defaut = seuil_agitation(v)
    ok("agitation : par défaut, le quantile 80 % après 5 ans (1 260 séances) de passé",
       abs(defaut.iloc[1500] - np.quantile(np.arange(1, 1502), 0.8)) < 1e-9 and np.isnan(defaut.iloc[1258]))
    ok("incertitude : sans aucune alarme, pas de marge (et pas de plantage)",
       intervalle_blocs(pd.Series([False] * 30), pd.Series([0.0] * 30)) is None)
    dbl = pd.Series([1.0, 2.0, 3.0], index=pd.DatetimeIndex(["2026-01-02", "2026-01-02", "2026-01-01"]))
    net = _nettoyer(dbl)
    ok("cours : une date en double de Yahoo est retirée (la dernière gardée), et l'ordre remis",
       list(net.index.strftime("%m-%d")) == ["01-01", "01-02"] and net.iloc[-1] == 2.0)

    # 12. La règle : le seuil inclus, et les revenus des deux côtés.
    alerte_2 = mens.copy()
    alerte_2.loc["2001-06", "niveau"] = 2.0
    ok("règle : le niveau 2 tout juste suffit à se mettre à l'abri (le seuil est inclus)",
       regle_simple(d, alerte_2)["prix_seul"]["regle"]["pire_baisse"] == 0)
    tr = pd.Series(100 * np.cumprod(np.full(len(j), 1.0004)), index=j)        # le S&P dividendes compris, en hausse
    abri5 = pd.Series(5.0, index=j)                                            # 5 % l'an à l'abri
    toujours_abri = mens.copy()
    toujours_abri["niveau"] = 3.0
    rr = regle_simple(d, toujours_abri, abri=abri5, total=tr)["avec_revenus"]
    ok("règle avec revenus : toujours à l'abri → les intérêts seuls (environ 5 % l'an)",
       abs(rr["regle"]["par_an"] - 0.052) < 0.005)
    ok("règle avec revenus : « tout garder » suit le S&P dividendes compris, pas le cours seul",
       rr["toujours"]["par_an"] > 0.10)

    # 13. La mesure : allumée et éteinte dans le bon sens, et les fausses alarmes.
    dm2 = pd.DataFrame({"cours": 100.0, "tendance": 0.0, "agitation": 0.0, "envolee": 0.0,
                        "niveau": 0.0, "complet": True, "chute": 0.0, "a_venir": 0.0}, index=jm)
    for t in fins[10:12]:
        dm2.loc[t, ["chute", "tendance", "niveau"]] = [1.0, 1.0, 2.0]
    dm2.loc[fins[30], "niveau"] = 2.0                                       # une alarme sans chute devant…
    dm2.loc[fins[40], "niveau"] = 2.0                                       # … et une seconde, plus loin
    dm2.loc[dm2.index > fins[29], "cours"] = 90.0                           # −10 % sous le plus haut d'un an
    rm2 = mesurer(dm2, debut="2003-01-01")
    ok("mesure : allumée → 2 mois, 2 chutes ; éteinte → aucune chute (pas d'inversion)",
       rm2["aiguilles"]["tendance"]["allumee"]["chutes"] == 2
       and rm2["aiguilles"]["tendance"]["allumee"]["mois"] == 2
       and rm2["aiguilles"]["tendance"]["eteinte"]["chutes"] == 0)
    ok("mesure : la chute est vue, et les deux alarmes sans chute sont comptées comme fausses",
       rm2["alarme"]["episodes_vus"] == 1 and rm2["alarme"]["fausses_alarmes"] == 2)
    ok("mesure : « déjà perdu » se compte depuis le PLUS HAUT d'un an (−10 %)",
       all(abs(x + 0.10) < 1e-12 for x in rm2["alarme"]["fausses_deja_perdu"]))
    ok("mesure : 2 mois allumés seulement → « trop peu pour conclure »", rm2["aiguilles"]["tendance"]["trop_peu"])

    # 14. La lecture du jour.
    l1 = lecture(c3)
    ok("lecture : l'envolée allumée et le secteur nommé", l1["envolee"]["allumee"] and l1["envolee"]["symbole"] == "FORT")
    l2 = lecture(c4, manquants=["SMH"])
    ok("lecture : un secteur manquant rend une envolée éteinte « inconnue », et le niveau un minimum",
       l2["envolee"]["inconnue"] and l2["niveau_minimum"] and l2["envolee"]["manquants"] == ["Semi-conducteurs"])
    dfp = construire(pd.Series(100.0, index=pd.bdate_range("2026-01-01", "2026-11-16")), {})
    ny = ZoneInfo("America/New_York")
    ok("lecture : en pleine séance, la lecture du jour est provisoire",
       lecture(dfp, maintenant=datetime(2026, 11, 16, 11, 0, tzinfo=ny))["provisoire"])
    ok("lecture : après la clôture de New York, elle ne l'est plus",
       not lecture(dfp, maintenant=datetime(2026, 11, 16, 16, 30, tzinfo=ny))["provisoire"])

    # 15. La lecture : le sens de chaque aiguille, et la date de la lecture provisoire.
    l3 = lecture(c1)
    ok("lecture : la tendance allumée (sous la moyenne) se lit allumée, et compte dans le niveau",
       l3["tendance"]["allumee"] and l3["niveau"] == 1)
    calme_seul = lecture(construire(serie_a.iloc[:1400], {}))
    agite = lecture(c2)
    ok("lecture : l'agitation éteinte au calme, allumée dans la tempête",
       not calme_seul["agitation"]["allumee"] and agite["agitation"]["allumee"])
    ok("lecture : le niveau est la somme des aiguilles allumées",
       agite["niveau"] == sum(agite[k]["allumee"] for k in ("tendance", "agitation", "envolee")))
    ok("lecture : le lendemain, la lecture de la veille n'est plus provisoire",
       not lecture(dfp, maintenant=datetime(2026, 11, 17, 11, 0, tzinfo=ny))["provisoire"])

    # 16. La règle avec revenus ne compte que les jours où dividendes ET intérêts sont connus.
    tr_tard = tr.loc["2002-01-01":]
    rt = regle_simple(d, toujours_abri, abri=abri5, total=tr_tard)["avec_revenus"]
    ok("règle avec revenus : elle commence quand le S&P dividendes compris commence, sans trou",
       rt["debut"] >= "2002-01-01" and math.isfinite(rt["regle"]["par_an"]) and math.isfinite(rt["toujours"]["par_an"]))

    # 17. Les cours, avec un FAUX Yahoo : le prix seul, les refus et les retards signalés.
    import types

    class FauxTicker:
        def __init__(self, symbole):
            self.symbole = symbole

        def history(self, period, interval, auto_adjust):
            if self.symbole == "REFUSE":
                raise RuntimeError("Yahoo refuse")
            idx = pd.bdate_range("2026-01-05", periods=5, tz="America/New_York")
            brut, ajuste = [10.0, 11.0, 12.0, 13.0, 14.0], [9.0, 10.0, 11.0, 12.0, 13.0]
            h = pd.DataFrame({"Close": ajuste if auto_adjust else brut, "Adj Close": ajuste}, index=idx)
            return h.iloc[:-1] if self.symbole == "RETARD" else h

    ancien_yf = sys.modules.get("yfinance")
    anciens_secteurs = dict(SECTEURS)
    sys.modules["yfinance"] = types.SimpleNamespace(Ticker=FauxTicker)
    try:
        ok("cours : le PRIX SEUL, pas le cours ajusté des dividendes",
           list(_cours("X", "5d", essais=1).values) == [10.0, 11.0, 12.0, 13.0, 14.0])
        SECTEURS.clear()
        SECTEURS.update({"OK": "Bon", "REFUSE": "Refusé", "RETARD": "En retard"})
        _, sect, manq = charger("5d", "5d", essais_secteurs=1)
        ok("charger : un secteur refusé ET un secteur en retard d'une séance sont signalés, jamais oubliés",
           sorted(manq) == ["REFUSE", "RETARD"] and "OK" in sect)
    finally:
        SECTEURS.clear()
        SECTEURS.update(anciens_secteurs)
        if ancien_yf is None:
            sys.modules.pop("yfinance", None)
        else:
            sys.modules["yfinance"] = ancien_yf

    print(f"\n{sum(cas)}/{len(cas)} vérifications réussies")
    return all(cas)


# ══════════════════════════════════════════════════════════════════

def _pct(x):
    return "—" if x is None else f"{x * 100:+.1f} %".replace(".", ",")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--mesurer", action="store_true", help="mesurer sur le passé et écrire data/thermometre.json")
    p.add_argument("--verifier", action="store_true", help="les cas calculés à la main")
    args = p.parse_args()

    if args.verifier:
        sys.exit(0 if verifier() else 1)

    marche, secteurs, manquants = charger("max", "max" if args.mesurer else "3y")
    df = construire(marche, secteurs)
    l = lecture(df, manquants)
    print(f"Lecture du {l['date']} : niveau {l['niveau']} sur 3")
    print(f"  tendance  : {'allumée' if l['tendance']['allumee'] else 'éteinte'} "
          f"({_pct(l['tendance']['ecart'])} par rapport à la moyenne 200 j)")
    print(f"  agitation : {'allumée' if l['agitation']['allumee'] else 'éteinte'} "
          f"(volatilité {_pct(l['agitation']['vol'])}, seuil {_pct(l['agitation']['seuil'])})")
    etat_envolee = "allumée" if l["envolee"]["allumee"] else "inconnue" if l["envolee"]["inconnue"] else "éteinte"
    print(f"  envolée   : {etat_envolee} "
          f"({l['envolee']['secteur']} : {_pct(l['envolee']['net'])} de plus que le marché en 2 ans)"
          + (f" — sans réponse : {', '.join(l['envolee']['manquants'])}" if l["envolee"]["manquants"] else ""))

    if args.mesurer:
        # Une mesure à qui il manque un secteur fausserait l'envolée sans le
        # dire : on n'écrit rien, et on le dit.
        if manquants:
            sys.exit(f"Mesure annulée : Yahoo n'a pas donné {', '.join(manquants)}. "
                     "Rien n'est écrit ; réessaie dans quelques minutes.")
        try:
            abri, total = _cours(ABRI, "max"), _cours(TOTAL, "max")
        except ValueError as e:
            sys.exit(f"Mesure annulée : {e}. Rien n'est écrit ; réessaie dans quelques minutes.")
        rapport = mesurer(df, abri=abri, total=total)
        os.makedirs(os.path.dirname(RAPPORT), exist_ok=True)
        with open(RAPPORT + ".tmp", "w") as f:
            json.dump(rapport, f, indent=2, ensure_ascii=False)
        os.replace(RAPPORT + ".tmp", RAPPORT)
        print(json.dumps(rapport, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
