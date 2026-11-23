"""
Les erreurs volontaires du creux du marché (creux.py).

CE QUE ÇA FAIT
    Chaque erreur est glissée dans une COPIE de creux.py, dans un dossier
    jetable (thermometre.py y est lié, pas copié), puis
    `python3 creux.py --verifier` y est lancé : il doit échouer. Une erreur
    qui passe montre un trou dans les vérifications, sauf si elle ne change
    rien de visible (dit à côté). Les fichiers du projet ne sont jamais
    modifiés, et rien n'est lu chez Yahoo.

LANCER
    python3 outils/mutations_creux.py           # toutes (quelques secondes)
    python3 outils/mutations_creux.py 3 17      # seulement les erreurs n° 3 et 17
"""

import concurrent.futures
import os
import shutil
import subprocess
import sys
import tempfile
import time

PROJET = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ([(avant, après), …], ce que l'erreur casse). « avant » doit être unique dans creux.py.
ERREURS = [
    # Le seuil et les épisodes.
    ([("return creux <= record * (1 - seuil)", "return creux / record - 1 <= -seuil")],
     "le seuil écrit par une division (refuse 100 → 80)"),
    ([("return creux <= record * (1 - seuil)", "return creux < record * (1 - seuil)")], "−20 % exactement refusé"),
    ([("if valeurs[i] >= valeurs[rec]:", "if valeurs[i] > valeurs[rec]:")], "un record égalé ne finit pas la baisse"),
    ([("elif creux is None or valeurs[i] < valeurs[creux]:", "elif creux is None or valeurs[i] <= valeurs[creux]:")],
     "à égalité, le dernier creux compte"),
    ([("            rec, creux = i, None", "            rec = i")], "le creux n'est pas remis à zéro au record"),
    ([("    return finis, (rec, creux)",
       "    return finis + ([(rec, creux, len(valeurs) - 1)] if creux is not None else []), (rec, creux)")],
     "la baisse en cours comptée comme finie"),
    ([("for i in range(1, len(valeurs)):", "for i in range(2, len(valeurs)):")], "la deuxième clôture sautée"),
    ([('"descente": (d_creux - d_rec).days}', '"descente": (d_creux - d_rec).days + 1}')], "la descente d'un jour de trop"),
    ([("retour_record=(d_fin - d_rec).days", "retour_record=(d_fin - d_creux).days")], "retour depuis le record compté depuis le creux"),
    ([("retour_creux=(d_fin - d_creux).days", "retour_creux=(d_fin - d_rec).days")], "retour depuis le creux compté depuis le record"),
    ([("min(restees, key=lambda x: x[0])", "max(restees, key=lambda x: x[0])")], "la plus proche : la moins profonde"),
    ([("for r, c, f in finis if not sous_le_seuil(v[c], v[r], seuil)]", "for r, c, f in finis]")],
     "la plus proche choisie parmi les baisses gardées"),
    ([("range(rec + 1, len(v)) if sous_le_seuil", "range(rec + 2, len(v)) if sous_le_seuil")],
     "la première clôture à −20 % cherchée un jour trop tard"),
    ([('"au_record": creux is None,', '"au_record": creux is None or v[-1] >= v[rec] * 0.999,')],
     "« à son record » à 0,1 % près"),
    # Les clôtures seules.
    ([("(ny.hour, ny.minute) < (16, 0)", "(ny.hour, ny.minute) < (9, 30)")], "la ligne du jour gardée pendant la séance"),
    ([("(ny.hour, ny.minute) < (16, 0)", "(ny.hour, ny.minute) <= (16, 0)")], "la clôture de 16 h encore retirée"),
    ([("    ny = maintenant.astimezone(NY)", "    ny = maintenant.astimezone(PARIS)")], "l'heure de Paris prise pour celle de New York"),
    ([("        return cours.iloc[:-1]", "        return cours")], "la ligne du jour jamais retirée"),
    # Les médianes et les nombres.
    ([("Fraction(v[n // 2 - 1] + v[n // 2], 2)", "Fraction(sum(v), n)")], "médiane paire = la moyenne de tout"),
    ([("return Fraction(v[n // 2]) if n % 2", "return Fraction(v[n // 2 - 1]) if n % 2")], "médiane impaire décalée"),
    ([("return math.trunc((Fraction(creux)", "return round((Fraction(creux)")], "pourcentage arrondi au lieu de tronqué"),
    ([("(Fraction(creux) / Fraction(record) - 1) * 1000", "(creux / record - 1) * 1000")], "pourcentage en calcul ordinaire"),
    ([("{MOINS if dixiemes < 0 else ''}", "{'-' if dixiemes < 0 else ''}")], "le tiret au lieu du vrai signe moins"),
    ([("{a // 10},{a % 10}{NBSP}%", "{a // 10},{a % 10} %")], "une espace ordinaire avant « % »"),
    ([("return math.floor(x + Fraction(1, 2))", "return round(x)")], "l'arrondi au pair (12,5 → 12)"),
    ([("    if mois < 1:\n", "    if j < 15:\n")], "l'ancienne règle des durées (15 jours → « 0 mois »)"),
    ([("    if mois < 24:\n", "    if j < 730.5:\n")], "l'ancienne règle des durées (716 jours → « 24 mois »)"),
    ([("d = _arrondi(j / JOURS_PAR_AN * 10)", "d = math.floor(j / JOURS_PAR_AN * 10)")], "les années tronquées"),
    ([('JOURS_PAR_MOIS = Fraction("30.44")', 'JOURS_PAR_MOIS = Fraction("30")')], "un mois de 30 jours"),
    ([('JOURS_PAR_AN = Fraction("365.25")', 'JOURS_PAR_AN = Fraction("365")')], "une année de 365 jours"),
    # Les textes.
    ([("{'1ᵉʳ' if d.day == 1 else d.day}", "{d.day}")], "« 1 juin » au lieu de « 1ᵉʳ juin »"),
    ([('return f"{MOIS[d.month - 1]} {d.year}"', 'return f"{MOIS[d.month % 12]} {d.year}"')], "le mois décalé"),
    ([("JOURS[d.weekday()]", "JOURS[d.weekday() - 1]")], "le jour de la semaine décalé"),
    ([('.replace(",", MILLIERS)', '.replace(",", " ")')], "une espace ordinaire dans les milliers"),
    ([("p = moment.astimezone(PARIS)", "p = moment.astimezone(timezone.utc)")], "l'heure de lecture en UTC"),
    ([("La baisse commencée le {texte_date(ec['record_date'])}", "La baisse commencée le {texte_date(ec['creux_date'])}")],
     "la baisse en cours datée de son creux"),
    ([('    if ec["seuil_date"] is not None:', '    if ec["seuil_date"] is not None and False:')],
     "la phrase des −20 % touchés oubliée"),
    ([('dc = dixiemes_de_baisse(ec["creux"], ec["record"])', 'dc = dixiemes_de_baisse(ec["derniere"], ec["record"])')],
     "le creux pour l'instant pris à la dernière clôture"),
    ([("retour('retour_creux', 'depuis le creux')", "retour('retour_record', 'depuis le creux')")],
     "la médiane depuis le record dite « depuis le creux »"),
    ([('if court != long_ else "")', 'if True else "")')], "« de 3 mois à 3 mois »"),
    ([("en {texte_duree(b['retour_creux'])} depuis le creux", "en {texte_duree(b['retour_record'])} depuis le creux")],
     "une seule baisse : le retour depuis le record dit « depuis le creux »"),
    ([("    if n_proche > n:", "    if n_proche >= n:")], "« il y en aurait 13 » au lieu de « autant »"),
    ([("n_proche = len(baisses(c, SEUIL_PROCHE))", "n_proche = len(baisses(c, SEUIL))")], "la barre de −19 % jamais calculée"),
    ([('    if n == 1:\n        return _insecables(f"La seule baisse', '    if n == -1:\n        return _insecables(f"La seule baisse')],
     "« Les 1 baisses »"),
    ([('"creux": texte_date_courte(b["creux_date"])', '"creux": texte_date_courte(b["record_date"])')], "la colonne Creux datée du record"),
    ([('(" ;", NBSP + ";"), ', "")], "une espace ordinaire avant « ; »"),
    # La lecture et la panne.
    ([("if not len(v) or not np.isfinite(v).all() or (v <= 0).any():", "if not len(v):")], "un cours nul ou infini accepté"),
    ([("            _dernieres[symbole] = lecture\n", "            pass\n")], "la dernière bonne lecture jamais gardée"),
    ([('raise ValueError(f"aucune lecture du {nom} pour l\'instant ({e})") from e', "continue")],
     "un indice sans lecture oublié en silence (réponse à moitié vide)"),
    ([("        except ValueError as e:\n            ancienne", "        except KeyError as e:\n            ancienne")],
     "un refus de Yahoo fait tomber les deux indices"),
    ([("derniere = max(date.fromisoformat", "derniere = min(date.fromisoformat")], "l'en-tête prend la clôture la plus ancienne"),
    ([("history(start=DEBUT_YAHOO, interval", 'history(period="max", interval')],
     "« max » (99 ans dans yfinance 1.2.0) au lieu d'un début explicite"),
    ([("(debut - premier).days > MARGE_DEBUT", "(debut - premier).days > 3650")], "une série coupée au début acceptée"),
    ([('cours = thermometre._nettoyer(h["Close"])', 'cours = h["Close"]')], "les cours de Yahoo pas nettoyés"),
    ([('("^IXIC", "Nasdaq Composite")', '("^NDX", "Nasdaq Composite")')], "le Nasdaq-100 à la place du Composite"),
    ([("lecture.update(symbole=symbole, lu_le=maintenant.isoformat(), frais=True",
       "lecture.update(symbole=symbole, lu_le=maintenant.isoformat(), frais=False")], "une lecture fraîche dite ancienne"),
    # La première relecture (23/11/2026) : des erreurs qui passaient, puis une par correction.
    ([("def en_cours(cours, seuil=SEUIL):", "def en_cours(cours, seuil=SEUIL_PROCHE):")],
     "la baisse en cours dite à −20 % dès −19 %"),
    ([("restees = [(v[c] / v[r], r, c, f)", "restees = [(v[c] - v[r], r, c, f)")], "la plus proche mesurée en points"),
    ([('"histoire": phrase_histoire(liste, debut),', '"histoire": phrase_histoire(baisses(c, SEUIL_PROCHE), debut),')],
     "la phrase compte les baisses de 19 %"),
    ([('"detail": titre_detail(nom, len(liste), debut),', '"detail": titre_detail(nom, n_proche, debut),')],
     "le titre du détail compte les baisses de 19 %"),
    ([('"legende": LEGENDE, "notes": notes(sortie)}', '"legende": "", "notes": notes(sortie)}')], "la légende vide"),
    ([("return [_insecables(n) for n in (n1, n2, *NOTES_FIXES, sources)]",
       "return [_insecables(n) for n in (n1, n2, sources)]")], "les notes fixes perdues dans la réponse"),
    ([("    \"Le Nasdaq-100 (QQQ), souvent cité, n'est pas suivi ici.\",\n", "")], "la note du Nasdaq-100 retirée"),
    ([("un second krach avant ce record en fait partie", "un autre krach compte à part")], "la note du second krach changée"),
    ([('tete = "à son record" if ec["au_record"] else (texte_pct(d_tete) if d_tete else "à moins de 0,1 %")',
       'tete = "à son record" if ec["au_record"] or not d_tete else texte_pct(d_tete)')],
     "« à son record » quand la baisse tronquée vaut 0"),
    ([('(texte_pct(d_tete) if d_tete else "à moins de 0,1 %")',
       '(texte_pct(d_tete) if d_tete is not None else "à moins de 0,1 %")')], "« 0,0 % » en tête"),
    ([("{texte_pct(dc) if dc else 'à moins de 0,1 % du record'}", "{texte_pct(dc)}")],
     "« 0,0 % » pour le creux pour l'instant"),
    ([('"tete_num": bool(d_tete),', '"tete_num": not ec["au_record"],')], "« à moins de 0,1 % » écrit en chiffres"),
    ([('"tete_suite": None if ec["au_record"] else "de son record",', '"tete_suite": "de son record",')],
     "« à son record de son record »"),
    ([('None if d == derniere else f"(clôture du', 'None if True else f"(clôture du')],
     "une vieille lecture sans sa date en tête"),
    ([('sortie.append(dict(x, textes=dict(x["textes"], tete_date=(',
       'sortie.append(x) or x["textes"].update(tete_date=(')], "la lecture gardée modifiée par la réponse"),
    ([('en_baisse = any(not x["en_cours"]["au_record"] for x in indices)', "en_baisse = True")],
     "les notes parlent d'une baisse en cours qui n'existe pas"),
    ([("Yahoo n'a pas donné de nouveaux cours utilisables : dernière", "Yahoo ne répond pas : dernière")],
     "« Yahoo ne répond pas » quand il a répondu"),
    ([('derniere = f" ; dernière clôture : {texte_date(dates[0], True)}."', 'derniere = "."')],
     "la date de la dernière clôture absente des notes"),
    ([("    if len(set(dates)) == 1:", "    if True:")], "deux clôtures différentes dites comme une seule"),
]


def essayer(numero, changements):
    """Une erreur glissée dans une copie ; « attrapée » si les vérifications échouent."""
    with tempfile.TemporaryDirectory(prefix="mutation-creux-") as dossier:
        shutil.copy(os.path.join(PROJET, "creux.py"), dossier)
        os.symlink(os.path.join(PROJET, "thermometre.py"), os.path.join(dossier, "thermometre.py"))
        chemin = os.path.join(dossier, "creux.py")
        texte = open(chemin, encoding="utf-8").read()
        for avant, apres in changements:
            if texte.count(avant) != 1:
                return numero, "INTROUVABLE", f"« {avant[:60]} » apparaît {texte.count(avant)} fois"
            texte = texte.replace(avant, apres)
        with open(chemin, "w", encoding="utf-8") as f:
            f.write(texte)
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        try:
            r = subprocess.run([sys.executable, "creux.py", "--verifier"], cwd=dossier, capture_output=True,
                               text=True, timeout=300, env=env)
        except subprocess.TimeoutExpired:
            return numero, "attrapée", "trop long (plus de 5 min)"
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
    _, etat, detail = essayer(0, [])
    if etat != "PASSE":
        sys.exit(f"creux.py sans erreur ne passe pas ses vérifications ({detail}) : rien à mesurer.")
    print(f"   oui ({time.time() - debut:.0f} s).\n", flush=True)
    resultats = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futurs = {pool.submit(essayer, i, ERREURS[i - 1][0]): i for i in choix}
        for futur in concurrent.futures.as_completed(futurs):
            numero, etat, detail = futur.result()
            resultats[numero] = (etat, detail)
            print(f"{numero:3d}. {etat:11} {ERREURS[numero - 1][1]} — {detail}", flush=True)
    passent = [i for i, (e, _) in resultats.items() if e != "attrapée"]
    print(f"\n{len(choix) - len(passent)} erreurs volontaires attrapées sur {len(choix)} "
          f"({(time.time() - debut) / 60:.1f} min).")
    for i in sorted(passent):
        print(f"   {resultats[i][0]} : n° {i}, {ERREURS[i - 1][1]} {resultats[i][1]}")
    sys.exit(0 if not passent else 1)


if __name__ == "__main__":
    main()
