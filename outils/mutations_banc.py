"""
Les erreurs volontaires du banc des prévisions (banc.py et banc_methodes.py).

CE QUE ÇA FAIT
    Chaque erreur est glissée dans une COPIE des deux fichiers, dans un
    dossier jetable (stock_engine.py y est lié, pas copié), puis
    `python3 banc.py --verifier` y est lancé : il doit échouer. Une erreur
    qui passe montre un trou dans les vérifications. Les fichiers du projet
    ne sont jamais modifiés, et rien n'est écrit dans data/.

LANCER
    python3 outils/mutations_banc.py           # toutes (environ 10 minutes)
    python3 outils/mutations_banc.py 3 17      # seulement les erreurs n° 3 et 17
"""

import concurrent.futures
import os
import shutil
import subprocess
import sys
import tempfile
import time

PROJET = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COPIES = ("banc.py", "banc_methodes.py", "banc_argent.py")
LIENS = ("stock_engine.py", "simulateur_200.py", "stock_eval.py")   # le simulateur : pour comparer l'impôt du critère 2
M, B, A = "banc_methodes.py", "banc.py", "banc_argent.py"

# (fichier, [(avant, après), …], ce que l'erreur casse). « avant » doit être unique dans le fichier.
ERREURS = [
    # Les mesures : un regard vers le futur, ou un niveau à la place d'un rapport.
    (M, [('m["r1"] = c / c.shift(1) - 1', 'm["r1"] = c.shift(-1) / c - 1')], "r1 lit le lendemain"),
    (M, [('m["r5"] = c / c.shift(5) - 1', 'm["r5"] = c / c.shift(4) - 1')], "r5 sur 4 séances"),
    (M, [('m["r21"] = c / c.shift(21) - 1', 'm["r21"] = c.shift(-21) / c - 1')], "r21 lit le futur"),
    (M, [('m["vol20"] = ind["daily_vol"]', 'm["vol20"] = ind["daily_vol"].shift(-1)')], "volatilité du lendemain"),
    (M, [('m["rsi14"] = ind["rsi"]', 'm["rsi14"] = ind["rsi"].shift(-1)')], "RSI du lendemain"),
    (M, [('m["macd_atr"] = (ind["ema12"] - ind["ema26"]) / ind["atr"]', 'm["macd_atr"] = (ind["ema12"] - ind["ema26"])')],
     "MACD en niveau (trahit les fractionnements)"),
    (M, [('m["ecart20_atr"] = (c - ind["sma20"]) / ind["atr"]', 'm["ecart20_atr"] = (c - ind["sma20"])')],
     "écart à la moyenne en niveau"),
    (M, [('m["sma20_50"] = ind["sma20"] / ind["sma50"] - 1', 'm["sma20_50"] = ind["sma20"] - ind["sma50"]')],
     "moyennes en niveau"),
    (M, [('m["volume20"] = ind["volume"] / ind["vol_sma20"]', 'm["volume20"] = ind["volume"]')], "volume en niveau"),
    (M, [('m["mom12_1"] = ind["mom_12_1"]', 'm["mom12_1"] = ind["mom_12_1"].shift(-1)')], "momentum du lendemain"),
    (M, [('m["force"] = force.reindex(d.index)', 'm["force"] = force.reindex(d.index).shift(-1)')],
     "force du lendemain"),
    (M, [('m["spy1"] = (spy / spy.shift(1) - 1).reindex(d.index)', 'm["spy1"] = (spy.shift(-1) / spy - 1).reindex(d.index)')],
     "SPY du lendemain"),
    (M, [('m["spy5"] = (spy / spy.shift(5) - 1).reindex(d.index)', 'm["spy5"] = (spy / spy.shift(-5) - 1).reindex(d.index)')],
     "SPY sur 5 séances futures"),
    (M, [('m["eurusd1"] = connu_le_soir(fx1, d.index)', 'm["eurusd1"] = connu_le_soir(fx1.shift(-1), d.index)')],
     "l'euro/dollar de la ligne suivante"),
    (M, [('return fx1.reindex(dates, method="ffill", tolerance', 'return fx1.reindex(dates, method="bfill", tolerance')],
     "l'euro/dollar d'une ligne d'après le soir"),
    (M, [('method="ffill", tolerance=pd.Timedelta(days=banc.ECART_CHANGE_MAX))', 'method="ffill")')],
     "un euro/dollar périmé de plus de 5 jours"),
    (M, [('    return change / change.shift(1) - 1', '    return change.shift(-1) / change - 1')],
     "euro/dollar vers l'ouverture suivante"),
    (M, [('fx1 = change_sur_un_jour(change)\n', 'fx1 = change_sur_un_jour(change).shift(-1)\n')],
     "la variation du change décalée d'une ligne"),
    (M, [('r = banc.rendements_du_lendemain(banc.en_euros(clotures, change))[tickers]',
          'r = banc.rendements_du_lendemain(clotures)[tickers]')], "la réponse en dollars, pas en euros"),
    (M, [('X[:, j, :] = mesures_d_une_valeur(cours[t], spy, fx1)', 'X[:, j, :] = mesures_d_une_valeur(cours[tickers[0]], spy, fx1)')],
     "les mesures de la première valeur pour toutes"),
    (M, [('m["r1"] = c / c.shift(1) - 1', 'm["r1"] = c / c.shift(2) - 1')], "« comme hier » sur deux séances"),
    # L'apprentissage.
    (M, [('return np.arange(max(premiere - purge, 0))', 'return np.arange(max(premiere, 0))')], "sans purge"),
    (M, [('return np.arange(max(premiere - purge, 0))', 'return np.arange(max(premiere + 1, 0))')],
     "la première séance notée apprise"),
    (M, [('dates.searchsorted(b, side="right"))', 'dates.searchsorted(b, side="left"))')],
     "la dernière séance de l'année sans prévision"),
    (M, [('ok = np.isfinite(Xa).all(axis=1) & np.isfinite(ra)', 'ok = np.isfinite(Xa).all(axis=1)')],
     "apprendre sur des réponses vides"),
    (M, [('return Xa[ok], reponses(ra[ok]).astype(int), ok', 'return Xa[ok], (ra[ok] >= 0).astype(int), ok')],
     "un rendement nul appris comme une hausse"),
    (M, [('    echelle = StandardScaler().fit(Xa)\n    with _sans_faux_avertissements():\n        modele = LogisticRegression',
          '    echelle = StandardScaler().fit(np.vstack([Xa, Xt]))\n    with _sans_faux_avertissements():\n        modele = LogisticRegression')],
     "le centrage voit l'année notée"),
    (M, [('p = expit((echelle.transform(Xt) * modele.coef_[0])', 'p = expit((echelle.transform(Xt) * -modele.coef_[0])')],
     "le signe de c inversé"),
    (M, [('p = expit((echelle.transform(Xt) * modele.coef_[0]).sum(axis=1) + modele.intercept_[0])',
          'p = expit((echelle.transform(Xt) * modele.coef_[0]).sum(axis=1))')], "c sans sa constante"),
    (M, [('p = modele.predict_proba(X)[:, list(modele.classes_).index(1)]',
          'p = modele.predict_proba(X)[:, list(modele.classes_).index(0)]')], "d donne la probabilité de baisse"),
    (M, [('modele = HistGradientBoostingClassifier(**reglages).fit(Xa, ya)',
          'modele = HistGradientBoostingClassifier().fit(Xa, ya)')], "d avec les réglages par défaut"),
    (M, [('REGLAGES_C = {"C": 1.0, "max_iter": 1000}', 'REGLAGES_C = {"C": 0.5, "max_iter": 1000}')], "c : C = 0,5"),
    (M, [('REGLAGES_D = {"max_iter": 200,', 'REGLAGES_D = {"max_iter": 100,')], "d : 100 itérations"),
    (M, [('pc[test], pd_[test] = c_t.reshape(len(test), v), d_t.reshape(len(test), v)',
          'pc[test], pd_[test] = d_t.reshape(len(test), v), c_t.reshape(len(test), v)')], "c et d échangées"),
    (M, [('s["c"], s["d"], appris = apprendre_et_prevoir', 's["d"], s["c"], appris = apprendre_et_prevoir')],
     "c et d échangées dans les sorties"),
    (M, [('(dates[appr][lignes_ok][0], dates[appr][lignes_ok][-1], int(ok.sum()))',
          '(dates[appr][lignes_ok][0], dates[appr][lignes_ok][-1], int(len(ok)))')], "le nombre de lignes apprises"),
    # Les avis.
    (M, [('(x > SEUILS[m]).astype(float)', '(x >= SEUILS[m]).astype(float)')], "0 ou 50 % comptés « hausse »"),
    (M, [('return np.where(np.isfinite(x), (x > SEUILS[m]).astype(float), np.nan)', 'return (x > SEUILS[m]).astype(float)')],
     "pas d'avis compté comme « baisse »"),
    (M, [('SEUIL_PROBA = 0.5 ', 'SEUIL_PROBA = 0.45 ')], "c et d disent « hausse » dès 45 %"),
    (M, [('SORTIE = {"a": "force", "b": "mom12_1", "hier": "r1"}', 'SORTIE = {"a": "mom12_1", "b": "mom12_1", "hier": "r1"}')],
     "a dit le momentum"),
    (M, [('SORTIE = {"a": "force", "b": "mom12_1", "hier": "r1"}', 'SORTIE = {"a": "force", "b": "mom12_1", "hier": "r5"}')],
     "« comme hier » sur 5 séances"),
    # Le critère 1.
    (M, [('bonnes = 1 - mm - yy + 2 * my', 'bonnes = 1 - mm - yy + my')], "bonnes réponses fausses"),
    (M, [('tirage = mm * yy + (1 - mm) * (1 - yy)', 'tirage = mm * yy')], "le tirage au sort faux"),
    (M, [('"passe": face["tirage"]["vu"] and face["monte"]["vu"]', '"passe": face["tirage"]["vu"] or face["monte"]["vu"]')],
     "le « ET » du critère 1 devenu « OU »"),
    (M, [('"vu": bool(d > seuil)', '"vu": bool(d >= seuil)')], "« vu » à égalité"),
    (M, [('"barre": float(max(tirage', '"barre": float(min(tirage')], "la barre trop basse"),
    (M, [('seuil_dessous = banc.seuil_critique(-d, -t, niveau)', 'seuil_dessous = banc.seuil_critique(-d, t, niveau)')],
     "« nettement en dessous » faux"),
    (M, [('poids = banc.poids_blocs(int(p.sum()), bloc, tirages, graine + i)',
          'poids = banc.poids_blocs(int(p.sum()), bloc, tirages, graine)')], "les mêmes blocs pour toutes les périodes"),
    (M, [('sens_du_jour=(hier["face"]["tirage"]["ecart"], t_hier), niveau=niveau)[0]',
          'sens_du_jour=(hier["face"]["tirage"]["ecart"], t_hier), niveau=0.95)[0]')], "le critère à 95 %"),
    (M, [('hier, contraire = _face(d_t - d_h, t_t - t_h, niveau), _face(d_t + d_h, t_t + t_h, niveau)',
          'hier = contraire = _face(d_t - abs(d_h), t_t - np.abs(t_h), niveau)')],
     "le sens du jour choisi sur les chiffres (|d_h|, le test biaisé d'avant la relecture)"),
    (M, [('_face(d_t + d_h, t_t + t_h, niveau)', '_face(d_t + d_h, t_t + d_h, niveau)')],
     "le contraire de « comme hier » sans ses tirages (pas apparié)"),
    (M, [('hier, t_hier = critere_1(np.nan_to_num(av["hier"][p]), y[p], valide[p], poids, niveau=niveau)',
          'hier, t_hier = critere_1(np.nan_to_num(av["a"][p]), y[p], valide[p], poids, niveau=niveau)')],
     "« comme hier » remplacé par a"),
    (M, [('hier, t_hier = critere_1(np.nan_to_num(av["hier"][p]), y[p], valide[p], poids, niveau=niveau)',
          'hier, t_hier = critere_1(np.nan_to_num(av["hier"][p]), y[p], valide[p], banc.poids_blocs(int(p.sum()), bloc, tirages, graine + 99), niveau=niveau)')],
     "« comme hier » sur d'autres tirages des blocs"),
    (M, [('dans un sens comme dans l\'autre" if f["vu"]', 'dans un sens comme dans l\'autre" if f["ecart"] > 0')],
     "« nettement mieux que le sens du jour » dès que l'écart est positif"),
    (M, [('passent = [m for m in CANDIDATES if resultats_tout[m]["passe"]]',
          'passent = [m for m in METHODES if resultats_tout[m]["passe"]]')], "« comme hier » ouvre la porte A"),
    (M, [('valide = a_un_lendemain & np.logical_and.reduce([np.isfinite(av[m]) for m in METHODES])',
          'valide = a_un_lendemain')], "une valeur-jour sans avis comptée"),
    (M, [('"retirees": int((a_un_lendemain & ~valide).sum())', '"retirees": 0')], "les retirées pas comptées"),
    (M, [('lignes = np.flatnonzero(dans & np.isfinite(donnees.r).any(axis=1))',
          'lignes = np.flatnonzero(dans & np.isfinite(donnees.r).all(axis=1))')], "une séance à valeur manquante retirée"),
    (M, [('    y = reponses(r)\n', '    y = (np.where(a_un_lendemain, r, 0.0) >= 0).astype(float)\n')],
     "un rendement nul noté comme une hausse"),
    (M, [('GRAINE = 20261120 ', 'GRAINE = 20261119 ')], "une autre graine des blocs"),
    (M, [('PURGE = 1 ', 'PURGE = 0 ')], "purge à 0 séance"),
    # La mesure et son écriture.
    (M, [('    if not egal_etape0(dossier, donnees, annees):', '    if egal_etape0(dossier, donnees, annees):')],
     "garde-fou de l'étape 0 inversé"),
    (M, [('donnees.r = np.where(np.isfinite(donnees.r), rng.normal(0.0005, 0.02, donnees.r.shape), np.nan)',
          'donnees.r = donnees.r')], "à blanc avec les vraies réponses"),
    (M, [('        dire("À BLANC : les rendements', '        dire("À  blanc : les rendements')], "à blanc pas annoncé"),
    (M, [('    if not a_blanc and os.path.exists(chemin):', '    if False and os.path.exists(chemin):')],
     "la mesure se refait au lieu de réafficher"),
    (M, [('    if a_blanc:\n        print(texte)\n        return rap\n', '')], "à blanc écrit"),
    (M, [('    if os.path.exists(chemin):\n        raise FileExistsError', '    if False:\n        raise FileExistsError'),
         ('        os.link(temp, chemin)                       # refusé', '        os.replace(temp, chemin)                       # refusé')],
     "une mesure écrase la première"),
    (M, [('return float(x) if math.isfinite(x) else None', 'return float(x)')], "NaN dans le JSON"),
    (M, [('if os.path.abspath(dossier_banc).startswith(os.path.abspath(banc.MARCHE)):', 'if False:')],
     "écrire dans data/market permis"),
    (M, [('def charger_cours(dossier=banc.MARCHE, tickers=None, fin=banc.FIN_AVANT_COFFRE):',
          'def charger_cours(dossier=banc.MARCHE, tickers=None, fin=None):')], "le coffre lu par défaut"),
    # Le rapport.
    (M, [('verdict = ("ne concourt pas" if m not in CANDIDATES else "PASSE" if x["passe"] else "ne passe pas")',
          'verdict = ("ne concourt pas" if m not in CANDIDATES else "ne passe pas" if x["passe"] else "PASSE")')],
     "le verdict inversé"),
    (M, [('if x["face"]["tirage"]["vu"] and not x["face"]["monte"]["vu"]:', 'if x["face"]["tirage"]["vu"] or not x["face"]["monte"]["vu"]:')],
     "la phrase « elle trie mieux » hors de son cas"),
    (M, [('elle, l_ = ("elle", "l\'") if len(rap["passent"]) == 1 else ("elles", "les ")', 'elle, l_ = ("elle", "l\'")')],
     "« pour elle » au pluriel"),
    (M, [('    if h["vu"]:\n', '    if h["vu_dessous"]:\n'), ('    elif h["vu_dessous"]:\n', '    elif h["vu"]:\n')],
     "élan et rebond échangés"),
    (M, [('return _ecart(x, dec) + (" point" if abs(v) < 2 else " points")', 'return _ecart(x, dec) + (" point" if abs(v) <= 2 else " points")')],
     "« 2,00 point » au singulier"),
    (M, [('il lui fallait plus de {banc._pct(x[\'barre\'], 2)}', 'il lui fallait plus de {banc._pct(x[\'bonnes\'], 2)}')],
     "la barre remplacée par ses bonnes réponses"),
    # L'étape 0, ce que l'étape 1 partage avec elle.
    (B, [('if not isinstance(df.index, pd.DatetimeIndex) or df.index.tz is not None:', 'if False:')],
     "dates illisibles ou avec fuseau acceptées"),
    (B, [('if not isinstance(df.index, pd.DatetimeIndex) or df.index.tz is not None:',
          'if not isinstance(df.index, pd.DatetimeIndex):')], "dates avec fuseau acceptées"),
    (B, [('    for colonne in colonnes:\n        if colonne not in df.columns:', '    for colonne in ():\n        if colonne not in df.columns:')],
     "colonne absente pas signalée"),
    (B, [('if (np.isinf(x) | (x <= 0)).any():', 'if (np.isinf(x) | (x < 0)).any():')], "un 0 accepté"),
    (B, [('df = df.sort_index().loc[:fin]', 'df = df.sort_index()')], "la coupe du coffre retirée"),
    (B, [('pos = change.index.searchsorted(jours, side="right")', 'pos = change.index.searchsorted(jours, side="left")')],
     "l'euro/dollar du jour même"),
    (B, [('    return prix.shift(-1) / prix - 1', '    return prix / prix.shift(1) - 1')], "la réponse = le rendement du jour"),
    (B, [('    h = ecart / 2', '    h = ecart')], "l'écart compté deux fois"),
    (B, [('res = (2 * (my - mm * yy), 1 - mm - 2 * yy + 2 * my)', 'res = ((my - mm * yy), 1 - mm - 2 * yy + 2 * my)')],
     "l'écart face au tirage divisé par 2"),
    (B, [('return float(np.quantile(observe - np.asarray(tires, float), niveau))',
          'return float(np.quantile(observe - np.asarray(tires, float), 1 - niveau))')], "le seuil du mauvais côté"),
    (B, [('idx = ((debuts[:, :, None] + np.arange(bloc)) % n)', 'idx = ((debuts[:, :, None] + np.zeros(bloc, int)) % n)')],
     "des blocs d'une seule séance répétée"),
    # Ajoutées après la relecture du 20/11/2026 : celles du relecteur qui passaient (une mesure prise pour
    # une autre, une fenêtre glissante, le code pas commité, l'affichage), puis celles des corrections.
    (M, [('m["rsi14"] = ind["rsi"]', 'm["rsi14"] = ind["adx"]')], "le RSI remplacé par l'ADX"),
    (M, [('m["macd_atr"] = (ind["ema12"] - ind["ema26"]) / ind["atr"]', 'm["macd_atr"] = ind["macd_hist"] / ind["atr"]')],
     "l'histogramme MACD au lieu de la ligne MACD"),
    (M, [('m["vol20"] = ind["daily_vol"]', 'm["vol20"] = ind["ret20"]')], "la volatilité remplacée par le rendement 20 j"),
    (M, [('m["spy1"] = (spy / spy.shift(1) - 1).reindex(d.index)', 'm["spy1"] = c / c.shift(1) - 1')],
     "SPY sur 1 séance remplacé par la valeur elle-même"),
    (M, [('m["ecart20_atr"] = (c - ind["sma20"]) / ind["atr"]', 'm["ecart20_atr"] = (c - ind["sma50"]) / ind["atr"]')],
     "l'écart à la moyenne 50 au lieu de 20"),
    (M, [('change = banc._lire(dossier, banc.CHANGE, fin, "Open").dropna()',
          'change = banc._lire(dossier, banc.CHANGE, fin, "Close").dropna()')], "la « clôture » du change au lieu de l'ouverture"),
    (M, [('    spy = cours[banc.REFERENCE]["Close"]\n', '    spy = cours[banc.REFERENCE]["Open"]\n')],
     "SPY mesuré sur ses ouvertures"),
    (M, [("cellules.append(f\"{_ecart(x['face']['tirage']['ecart']) + (' vu' if x['passe'] else '   '):>14}\")",
          "cellules.append(f\"{_ecart(x['face']['tirage']['ecart']) + (' vu' if x['face']['tirage']['vu'] else '   '):>14}\")")],
     "le « vu » année par année = vu face au tirage seul"),
    (M, [('        appr = seances_d_apprentissage(dates, a)\n', '        appr = seances_d_apprentissage(dates, a)[-650:]\n')],
     "l'apprentissage sur une fenêtre de 650 séances, pas toutes les années d'avant"),
    (M, [("dire(f\"   « Ça monte toujours » : {banc._pct(tout['a']['monte'], 2)} de bonnes réponses.\")",
          "dire(f\"   « Ça monte toujours » : {banc._pct(tout['a']['tirage'], 2)} de bonnes réponses.\")")],
     "« ça monte toujours » affiche le tirage de a"),
    (M, [("dire(\"   « ça monte » : \" + \", \".join(f\"{nom} {banc._pct(rap['resultats'][nom]['a']['monte'], 1)}\"",
          "dire(\"   « ça monte » : \" + \", \".join(f\"{nom} {banc._pct(rap['resultats'][nom]['a']['bonnes'], 1)}\"")],
     "« ça monte » année par année = les bonnes réponses de a"),
    (M, [('    return {"commit": commit, "modifie": bool(etat), "md5": md5_des_fichiers(dossier)}',
          '    return {"commit": commit, "modifie": False, "md5": md5_des_fichiers(dossier)}')],
     "le code pas commité n'est jamais vu"),
    (M, [('    if not a_blanc and code["modifie"]:', '    if False and code["modifie"]:')],
     "la mesure tourne sur du code pas commité"),
    (M, [('FICHIERS_DU_CODE = ("banc.py", "banc_methodes.py", "stock_engine.py")',
          'FICHIERS_DU_CODE = ("banc.py", "banc_methodes.py")')], "stock_engine.py hors de l'empreinte"),
    (M, [('        test = np.arange(dates.searchsorted(a), dates.searchsorted(b, side="right"))',
          '        test = np.arange(dates.searchsorted(a) - 1, dates.searchsorted(b, side="right"))')],
     "le modèle de l'année prévoit aussi la veille de sa première séance"),
    (M, [('        phrase = "demain va nettement plus souvent dans le même sens',
          '        phrase = "demain va plus souvent dans le même sens')], "la phrase « élan » sans « nettement »"),
    (M, [('    if os.path.exists(chemin):\n        raise FileExistsError', '    if False:\n        raise FileExistsError'),
         ('        os.link(temp, chemin)                       # refusé', '        shutil.copy(temp, chemin)                       # refusé')],
     "une mesure écrase la première (une copie au lieu du lien, sans plantage)"),
    (M, [('"vu": hier["vu"] and contraire["vu"]', '"vu": hier["vu"] or contraire["vu"]')],
     "« nettement mieux que le sens du jour » face à UN seul de ses deux sens"),
    (M, [('"borne": min(hier["ecart"] - hier["seuil"], contraire["ecart"] - contraire["seuil"])',
          '"borne": min(hier["ecart"], contraire["ecart"])')], "la borne du sens du jour sans son seuil"),
    (M, [("banc._pts(max(h['seuil'], h['seuil_dessous']), 2)", "banc._pts(h['seuil'], 2)")],
     "« rien de net » : ce que le test voyait, d'un seul côté"),
    (M, [('source = "du tirage" if x["tirage"] + s_t >= x["monte"] + s_m else',
          'source = "du tirage" if x["tirage"] + s_t < x["monte"] + s_m else')], "d'où vient la barre, inversé"),
    (M, [('{banc._nb(banc.facteur_puissance(), 2)} fois le seuil', '{banc._nb(1.0, 2)} fois le seuil')],
     "un écart vrai égal au seuil « vu 8 fois sur 10 »"),
    (M, [('fin = ("dire « hausse » partout fait nettement mieux" if x["face"]["monte"]["vu_dessous"]',
          'fin = ("dire « hausse » partout fait nettement mieux" if not x["face"]["monte"]["vu_dessous"]')],
     "« fait nettement mieux » et « à peu près aussi bien » échangés"),
    (M, [('        elif x["face"]["tirage"]["vu_dessous"]:', '        elif not x["face"]["tirage"]["vu_dessous"]:')],
     "la phrase « nettement EN DESSOUS du tirage » hors de son cas"),
    (M, [('f"et le biais du survivant a pu {l_}aider', 'f"et rien ne dit que le hasard a pu {l_}aider')],
     "la porte A ouverte sans rappeler le biais du survivant"),
    (M, [('    if a_blanc:\n        print(texte)\n        return rap\n', '    print(texte)\n    if a_blanc:\n        return rap\n')],
     "le rapport affiché avant d'être écrit"),
    (M, [("        os.replace(temp_prev, os.path.join(dossier_banc, NOM_PREVISIONS))      # les prévisions d'abord…\n"
          "        os.link(temp, chemin)                       # refusé si le résultat existe déjà\n",
          "        os.link(temp, chemin)                       # refusé si le résultat existe déjà\n"
          "        os.replace(temp_prev, os.path.join(dossier_banc, NOM_PREVISIONS))      # les prévisions d'abord…\n")],
     "le résultat posé avant ses prévisions"),
    (M, [('donnees=empreinte_des_donnees(dossier, lues),', 'donnees=None,')], "l'empreinte des cours lus pas gardée"),
    (M, [('bibliotheques=versions_des_bibliotheques())', 'bibliotheques=None)')], "les versions des bibliothèques pas gardées"),
    (B, [('    import banc\n    banc.main()', '    main()')],
     "en commande, une trace Python au lieu de « Rien n'est calculé » (deux exemplaires de banc)"),
    # Ajoutées après la seconde passe de la relecture (20/11/2026).
    (M, [('"md5": _md5(temp_prev), "lignes"', '"md5": "", "lignes"')], "le md5 des prévisions pas gardé"),
    (M, [("{_ecart_et_borne(f['contraire'])} : {qualif}", "{_ecart_et_borne(f['comme_hier'])} : {qualif}")],
     "face au sens du jour, « comme hier » affiché à la place de son contraire"),
    (M, [('seuils_sens += [f["comme_hier"]["seuil"], f["contraire"]["seuil"]]', 'seuils_sens += [f["comme_hier"]["seuil"]]')],
     "ce que le test du sens du jour voyait, d'un seul côté"),
    # Le critère 2 (banc_argent.py, 20/11/2026).
    (A, [('r_ouv = (o_eur.shift(-2) / o_eur.shift(-1) - 1)', 'r_ouv = (o_eur.shift(-1) / o_eur - 1)')],
     "le matin, de l'ouverture du jour même (avant la décision du soir)"),
    (A, [('np.column_stack([r_prev, r_clot[banc.REFERENCE].to_numpy(float)])', 'np.column_stack([r_prev, r_prev[:, 0]])')],
     "SPY remplacé par la première valeur"),
    (A, [('    if not np.array_equal(r_clot[tickers].to_numpy(float), r_prev, equal_nan=True):',
          '    if False:')], "des prévisions qui ne sont plus celles du cache acceptées"),
    (A, [('n_matin = int(np.flatnonzero(au_matin).max()) + 1 if', 'n_matin = int(np.flatnonzero(au_matin).max()) + 0 if')],
     "le matin s'arrête un soir trop tôt"),
    (A, [('dernier_matin = suivante[lendemain[n_matin - 1]] if', 'dernier_matin = lendemain[n_matin - 1] if')],
     "le dernier moment du matin faux"),
    (A, [('    if bm._md5(chemin) != etape1["previsions"]["md5"]:', '    if False:')],
     "des prévisions changées acceptées"),
    (A, [('    if donnees != etape1.get("donnees"):', '    if False:')], "des fichiers de cours changés acceptés"),
    (A, [('b.sorties[m][t] = b.sorties[m][t, rng.permutation(len(b.valeurs))]', 'b.sorties[m][t] = b.sorties[m][t]')],
     "à blanc sans mélanger les sorties (les vrais choix)"),
    (A, [('b.r[h] = np.where(np.isfinite(b.r[h]), rng.normal(0.0005, 0.02, b.r[h].shape), np.nan)', 'b.r[h] = b.r[h]')],
     "à blanc avec les vrais rendements"),
    (A, [('c = veille if veille in tete else int(tete[min(int(u * len(tete)), len(tete) - 1)])',
          'c = int(tete[min(int(u * len(tete)), len(tete) - 1)])')], "le départage ne garde pas la valeur de la veille"),
    (A, [('        u = rng.random()                                   # un tirage chaque soir, même quand il ne sert pas\n',
          ''), ('            c = veille if veille in tete else int(tete[min(int(u * len(tete)), len(tete) - 1)])',
                '            c = veille if veille in tete else int(tete[min(int(rng.random() * len(tete)), len(tete) - 1)])')],
     "le générateur ne tire que les soirs où il sert"),
    (A, [('        choix[t] = veille = c\n', '        choix[t] = c\n        veille = c if c >= 0 else veille\n')],
     "la veille gardée en mémoire après un soir « aucune »"),
    (A, [('ok = valide[t] & np.isfinite(x) & (x > seuil)\n        if ok.any():\n            tete',
          'ok = np.isfinite(x) & (x > seuil)\n        if ok.any():\n            tete')], "une valeur non comptée peut être choisie"),
    (A, [('ok = valide[t] & np.isfinite(x) & (x > seuil)\n        if ok.any():\n            tete',
          'ok = valide[t] & np.isfinite(x) & (x >= seuil)\n        if ok.any():\n            tete')], "« au-dessus » non strict"),
    (A, [('        change = (cible != detenue) & ~arrete', '        change = ~arrete')], "garder coûte un aller-retour"),
    (A, [('        stop = change & (dispo <= 2 * frais)', '        stop = change & (V <= 2 * frais)')],
     "l'arrêt à 2 € sur la valeur (le trou de 2 € à 2,001 €)"),
    (A, [('        stop = change & (dispo <= 2 * frais)', '        stop = change & (dispo <= 0)')], "jamais d'arrêt à 2 €"),
    (A, [('        arrete |= stop\n', '')], "des ordres après l'arrêt"),
    (A, [('        produit = V * (1 - h) - frais\n        np.add.at', '        produit = V - frais\n        np.add.at')],
     "vendre sans le demi-écart"),
    (A, [('        place = (V - frais) / (1 + h)', '        place = V - frais')], "acheter sans le demi-écart"),
    (A, [('np.add.at(gains, (np.flatnonzero(vend), col_an[t])', 'np.add.at(gains, (np.flatnonzero(vend), col_an[0])')],
     "le gain d'une vente compté dans la première année"),
    (A, [('        cout = np.where(achete, V, cout)', '        cout = np.where(achete, place, cout)')],
     "le coût de la ligne sans les frais d'achat"),
    (A, [('    R = np.nan_to_num(np.asarray(R, float), nan=0.0)', '    R = np.asarray(R, float)')],
     "une séance sans cours rend le capital vide"),
    (A, [('        pertes = [[a, m] for a, m in pertes if annee - a <= report]', '        pertes = [[a, m] for a, m in pertes if annee - a < report]')],
     "le report des pertes sur 9 ans"),
    (A, [('            pertes.append([annee, -net])\n', '')], "les pertes jamais reportées"),
    (A, [('produit = np.where(tient, np.maximum(res["valeur"] * (1 - h) - frais, 0.0), res["valeur"])',
          'produit = np.where(tient, np.maximum(res["valeur"], 0.0), res["valeur"])')], "la dernière vente sans frais"),
    (A, [('produit = np.where(tient, np.maximum(res["valeur"] * (1 - h) - frais, 0.0), res["valeur"])',
          'produit = np.where(tient, res["valeur"] * (1 - h) - frais, res["valeur"])')], "une dernière vente sous 0"),
    (A, [('    produit = max(v * (1 - ecart / 2) - frais, 0.0)', '    produit = max(v * (1 - ecart / 2) - frais * len(valeurs_finales), 0.0)')],
     "le panier vendu ligne par ligne (49 €)"),
    (A, [('                               if all(rap["resultats"][f"{h}-{e}"]["methodes"][m]["passe_ici"] for h in HEURES)]',
          '                               if any(rap["resultats"][f"{h}-{e}"]["methodes"][m]["passe_ici"] for h in HEURES)]')],
     "le critère 2 le soir OU le matin"),
    (A, [('            barre = max(spy, panier)', '            barre = min(spy, panier)')], "la barre du critère 2 trop basse"),
    (A, [('    if abs(soir["spy"] - spy0) > 0.005 or abs(soir["panier"] - panier0) > 0.005:', '    if False:')],
     "les cibles de l'étape 0 pas vérifiées avant l'affichage"),
    (A, [("        os.replace(temp_ch, os.path.join(dossier_banc, NOM_CHEMINS))      # les chemins d'abord…\n"
          "        os.link(temp, chemin)                       # refusé si le résultat existe déjà\n",
          "        os.link(temp, chemin)                       # refusé si le résultat existe déjà\n"
          "        os.replace(temp_ch, os.path.join(dossier_banc, NOM_CHEMINS))      # les chemins d'abord…\n")],
     "le résultat posé avant ses chemins"),
    (A, [('"md5": bm._md5(temp_ch), "lignes"', '"md5": "", "lignes"')], "le md5 des chemins pas gardé"),
    (A, [('    if a_blanc:\n        print(texte)\n        return rap\n    ecrire_critere2(',
          '    print(texte)\n    if a_blanc:\n        return rap\n    ecrire_critere2(')], "le rapport affiché avant d'être écrit"),
    (A, [('    if a_blanc:\n        print(texte)\n        return rap\n    ecrire_critere2(', '    ecrire_critere2(')],
     "à blanc écrit"),
    (A, [('    if not a_blanc and code["modifie"]:', '    if False and code["modifie"]:')],
     "le critère 2 tourne sur du code pas commité"),
    (A, [('FICHIERS_DU_CODE = ("banc.py", "banc_methodes.py", "banc_argent.py", "stock_engine.py")',
          'FICHIERS_DU_CODE = ("banc.py", "banc_methodes.py", "stock_engine.py")')], "banc_argent.py hors de l'empreinte"),
    (A, [('    if not a_blanc and os.path.exists(chemin):', '    if False and os.path.exists(chemin):')],
     "le critère 2 se refait au lieu de réafficher"),
    (A, [('"bas": float(np.quantile(x, 0.1))', '"bas": float(np.quantile(x, 0.2))')], "l'intervalle des tirages faux"),
    (A, [('            if x["net"] < 0:', '            if x["net"] <= 0:')], "« tu aurais perdu plus que tes 200 € » à 0 €"),
    (A, [('    if not rap["passent"]:\n        dire("   Aucune candidate', '    if True:\n        dire("   Aucune candidate')],
     "« aucune ne passe » même quand une passe"),
    (A, [('        dire("À BLANC : les sorties', '        dire("À  blanc : les sorties')], "à blanc pas annoncé"),
    (B, [('        if args.critere2:\n            import banc_argent\n            banc_argent.lancer_le_critere2(a_blanc=args.a_blanc)',
          '        if args.critere2:\n            import banc_argent\n            banc_argent.lancer_le_critere2(a_blanc=False)')],
     "en commande, --critere2 --a-blanc fait la vraie mesure"),
    (B, [('    if args.a_blanc and not (args.mesurer or args.critere2):', '    if args.a_blanc and not args.mesurer:')],
     "en commande, --critere2 --a-blanc refusé"),
    (A, [('det = strat["chemin_detenue"][i][:fin]', 'det = strat["chemin_detenue"][i]'),
         ('np.prod(1 + np.nan_to_num(R[:fin][quand, j]))', 'np.prod(1 + np.nan_to_num(R[quand, j]))')],
     "les valeurs détenues comptées après l'arrêt (la position bloquée)"),
    (A, [("{_eur(s['frais'] + s['ecart']):>15}", "{_eur(s['frais']):>15}")], "les frais sans l'écart"),
    (A, [("le panier des {len(soir['valeurs'])} ", "le panier des 49 ")], "« le panier des 49 » écrit en dur"),
    (A, [('rap["choix_md5"] = {m: hashlib.md5(choix[m].astype("<i8").tobytes()).hexdigest() for m in METHODES}',
          'rap["choix_md5"] = {m: hashlib.md5(argent.valide.tobytes()).hexdigest() for m in METHODES}')],
     "l'empreinte des choix ne dépend pas des choix"),
]


def essayer(numero, fichier, changements, verbose=False):
    """Une erreur glissée dans une copie ; « attrapée » si les vérifications échouent."""
    with tempfile.TemporaryDirectory(prefix="mutation-banc-") as dossier:
        for nom in COPIES:
            shutil.copy(os.path.join(PROJET, nom), dossier)
        for nom in LIENS:
            os.symlink(os.path.join(PROJET, nom), os.path.join(dossier, nom))
        if fichier is not None:
            chemin = os.path.join(dossier, fichier)
            texte = open(chemin, encoding="utf-8").read()
            for avant, apres in changements:
                if texte.count(avant) != 1:
                    return numero, "INTROUVABLE", f"« {avant[:60]} » apparaît {texte.count(avant)} fois"
                texte = texte.replace(avant, apres)
            with open(chemin, "w", encoding="utf-8") as f:
                f.write(texte)
        env = {**os.environ, "OMP_NUM_THREADS": "2", "PYTHONDONTWRITEBYTECODE": "1"}
        try:
            r = subprocess.run([sys.executable, "banc.py", "--verifier"], cwd=dossier, capture_output=True,
                               text=True, timeout=1200, env=env)
        except subprocess.TimeoutExpired:
            return numero, "attrapée", "trop long (plus de 20 min)"
        rates = [l[2:] for l in r.stdout.splitlines() if l.startswith("✗")]
        if r.returncode == 0:
            return numero, "PASSE", ""
        if rates:
            return numero, "attrapée", f"{len(rates)} échec(s), dont : {rates[0][:110]}"
        derniere = (r.stderr.strip().splitlines() or ["?"])[-1]
        return numero, "attrapée", f"plantage : {derniere[:110]}"


def main():
    choix = [int(x) for x in sys.argv[1:]] or list(range(1, len(ERREURS) + 1))
    debut = time.time()
    print("Témoin (aucune erreur) : les vérifications doivent passer…", flush=True)
    _, etat, detail = essayer(0, None, [])
    if etat != "PASSE":
        sys.exit(f"Le banc sans erreur ne passe pas ses vérifications ({detail}) : rien à mesurer.")
    print(f"   oui ({time.time() - debut:.0f} s).\n", flush=True)
    resultats = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futurs = {pool.submit(essayer, i, *ERREURS[i - 1][:2]): i for i in choix}
        for futur in concurrent.futures.as_completed(futurs):
            numero, etat, detail = futur.result()
            resultats[numero] = (etat, detail)
            print(f"{numero:3d}. {etat:11} {ERREURS[numero - 1][2]} — {detail}", flush=True)
    passent = [i for i, (e, _) in resultats.items() if e != "attrapée"]
    print(f"\n{len(choix) - len(passent)} erreurs volontaires attrapées sur {len(choix)} "
          f"({(time.time() - debut) / 60:.1f} min).")
    for i in sorted(passent):
        print(f"   {resultats[i][0]} : n° {i}, {ERREURS[i - 1][2]} {resultats[i][1]}")
    sys.exit(0 if not passent else 1)


if __name__ == "__main__":
    main()
