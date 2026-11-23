"""
Les erreurs volontaires du mur des frais pendant la séance (seance.py).

CE QUE ÇA FAIT
    Chaque erreur est glissée dans une COPIE de seance.py, dans un dossier
    jetable (banc.py et stock_engine.py y sont liés, pas copiés), puis
    `python3 seance.py --verifier` y est lancé : il doit échouer. Une erreur
    qui passe montre un trou dans les vérifications, sauf si elle ne change
    rien de visible (dit à côté). Une copie qui ne se compile pas est dite
    INVALIDE : une erreur mal écrite ne teste rien. Les fichiers du projet ne
    sont jamais modifiés, et rien n'est lu chez Yahoo.

LANCER
    python3 outils/mutations_seance.py           # toutes (environ une minute)
    python3 outils/mutations_seance.py 3 17      # seulement les erreurs n° 3 et 17
"""

import concurrent.futures
import os
import shutil
import subprocess
import sys
import tempfile
import time

PROJET = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ([(avant, après), …], ce que l'erreur casse). « avant » doit être unique dans seance.py.
ERREURS = [
    # Ce qui paie, et le prix d'une fenêtre.
    ([("    return mouvement > seuil", "    return mouvement >= seuil")], "une hausse égale au seuil paie"),
    ([("    return mouvement > seuil", "    return abs(mouvement) > seuil")], "une baisse paie aussi"),
    ([("faits[f].append((jour, z[1] / a[0] - 1))", "faits[f].append((jour, z[0] / a[0] - 1))")],
     "la fin prise à l'Open de sa barre"),
    ([("faits[f].append((jour, z[1] / a[0] - 1))", "faits[f].append((jour, z[1] / a[1] - 1))")],
     "le début pris au Close de sa barre"),
    ([("a, z = b.get(debut), b.get(debut + duree - 5)", "a, z = b.get(debut), b.get(debut + duree)")],
     "la barre de fin, cinq minutes trop tard"),
    ([("if a is None or z is None or not _prix(a[0]) or not _prix(z[1]):", "if a is None or z is None:")],
     "un prix nul ou absent gardé"),
    ([("    return math.isfinite(x) and x > 0", "    return x > 0")], "un prix infini gardé"),
    # Les fenêtres.
    ([('"suivantes": ((OUVERTURE_NY + 15, 45),),', '"suivantes": ((OUVERTURE_NY, 45),),')],
     "les 45 minutes suivantes partent de 9 h 30"),
    ([('"quart": tuple((OUVERTURE_NY + 15 * i, 15) for i in range(26)),',
       '"quart": tuple((OUVERTURE_NY + 15 * i, 15) for i in range(25)),')], "le dernier quart d'heure oublié"),
    ([('"tranche": tuple((OUVERTURE_NY + 15 * i, 45) for i in range(24)),',
       '"tranche": tuple((OUVERTURE_NY + 45 * i, 45) for i in range(8)),')], "des tranches bout à bout"),
    ([('"seance": ((OUVERTURE_NY, FIN_NY - OUVERTURE_NY),),', '"seance": ((OUVERTURE_NY, FIN_NY - OUVERTURE_NY - 5),),')],
     "la séance s'arrête à 15 h 55"),
    ([('"quart": tuple((OUVERTURE_NY + 15 * i, 15) for i in range(26)),\n    "tranche": tuple((OUVERTURE_NY + 15 * i, 45) for i in range(24)),',
       '"quart": tuple((OUVERTURE_NY + 15 * i, 45) for i in range(24)),\n    "tranche": tuple((OUVERTURE_NY + 15 * i, 15) for i in range(26)),')],
     "deux fenêtres échangées"),
    # Les heures et les séances.
    ([("zip(index.tz_convert(NY), ouvertures, clotures)", 'zip(index.tz_convert("UTC"), ouvertures, clotures)')],
     "les heures lues en UTC"),
    ([("zip(index.tz_convert(NY), ouvertures, clotures)", "zip(index.tz_convert(PARIS), ouvertures, clotures)")],
     "les heures lues à Paris"),
    ([("    if index.duplicated().any():", "    if False:")], "deux barres à la même heure acceptées"),
    ([("for j in p if j < aujourd_hui})", "for j in p if j <= aujourd_hui})")], "la séance du jour comptée"),
    ([("    aujourd_hui = maintenant.astimezone(NY).date()", "    aujourd_hui = maintenant.astimezone(PARIS).date()")],
     "« aujourd'hui » pris à Paris"),
    ([("        if not any(j < aujourd_hui for j in p):", "        if False:")],
     "une valeur sans barre d'avant aujourd'hui gardée"),
    ([("if len(preparees) < VALEURS_MIN or len(seances) < SEANCES_MIN:", "if len(preparees) < VALEURS_MIN:")],
     "trop peu de séances accepté"),
    ([("VALEURS_MIN = 45 ", "VALEURS_MIN = 44 ")], "44 valeurs acceptées"),
    # Les comptes.
    ([('"hausses": [sum(1 for x in tous if paie(x, s)) for s in seuils],',
       '"hausses": [sum(1 for x in tous if paie(x, seuils[0])) for s in seuils],')], "le seuil de 200 € pour les deux"),
    ([('"mediane": _flottant(mediane(abs(x) for x in tous)),', '"mediane": _flottant(mediane(x for x in tous)),')],
     "la médiane des mouvements signés"),
    ([("    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2", "    return v[n // 2]")],
     "médiane paire : la valeur d'après le milieu"),
    ([("    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2", "    return sum(v) / n")],
     "la moyenne au lieu de la médiane"),
    ([('mediane(abs(x) for _, x in r["mouvements"][f])) for v, r in faits.items()}',
       'mediane(abs(x) for x in tous)) for v, r in faits.items()}')], "la médiane de chaque valeur prise sur toutes"),
    ([("                compte[f] = compte.get(f, 0) + n", "                compte[f] = compte.get(f, 0) + 1")],
     "les retirées jour par jour comptées une fois"),
    ([('"avec_une": len({j for j, x in ouverture if paie(x, seuils[0])})',
       '"avec_une": len({j for j, x in ouverture if paie(x, seuils[1])})')], "les jours avec une hausse, au seuil de 50 €"),
    ([('"ouverture_par_jour": {"jours": len({j for j, _ in ouverture}),', '"ouverture_par_jour": {"jours": len(seances),')],
     "les jours d'ouverture pris pour toutes les séances"),
    # La variation dans le cache journalier.
    ([("    bas = min(periodes, key=lambda p: p[0])", "    bas = max(periodes, key=lambda p: p[0])")],
     "la plus basse prise pour la plus haute"),
    ([("h, n = int(hausses[fin] - hausses[fin - periode]), int(comptees[fin] - comptees[fin - periode])",
       "h, n = int(hausses[fin] - hausses[fin - periode + 1]), int(comptees[fin] - comptees[fin - periode + 1])")],
     "des périodes d'une séance de moins"),
    ([("periodes.append((Fraction(h, n), h, n, df.index[fin - 1]))", "periodes.append((Fraction(h, n), h, n, df.index[fin - periode]))")],
     "la date du début de la période au lieu de sa fin"),
    ([("for x in ligne if paie(x, seuil))", "for x in ligne if x > 0)")], "toute hausse comptée dans la variation"),
    ([("    if len(df) < periode:", "    if len(df) <= periode:")], "une période pile refusée"),
    ([("rapports[valeur] = (c / o - 1).where(bons)", "rapports[valeur] = (c / o - 1)")], "une ouverture nulle gardée"),
    # Les textes.
    ([("    if unites == 0:", "    if unites < 0:")], "« 0,0 % » au lieu de « moins de 0,1 % »"),
    ([("    return math.floor(Fraction(x) * 10 ** decimales + Fraction(1, 2))",
       "    return round(Fraction(x) * 10 ** decimales)")], "l'arrondi au pair"),
    ([('return f"{_ecrire(_arrondi(Fraction(x) * 100, decimales), decimales)}{NBSP}%"',
       'return f"{_ecrire(math.floor(x * 10 ** (decimales + 2) + 0.5), decimales)}{NBSP}%"')],
     "le pourcentage calculé en flottants (x × 10 000 d'un coup)"),
    ([("    unites = _arrondi(Fraction(100 * h, n), 1)", "    unites = round(1000 * h / n)")],
     "la part arrondie par round() (au pair)"),
    ([("    return texte_pct(x, 3)", "    return texte_pct(x, 2)")], "le seuil au centième (« 1,11 % »)"),
    ([("    m = minutes_ny + DECALAGE_PARIS", "    m = minutes_ny")], "les heures de New York affichées"),
    ([('"parts": [texte_part(h, fen[f]["comptes"]) for h in fen[f]["hausses"]]}',
       '"parts": [texte_part(h, fen[f]["comptes"]) for h in reversed(fen[f]["hausses"])]}')], "deux colonnes échangées"),
    ([('rien = lambda x: texte_part(x["comptes"] - x["hausses"][0], x["comptes"])',
       'rien = lambda x: texte_part(x["comptes"] - x["hausses"][1], x["comptes"])')], "« rien rapporté » au seuil de 50 €"),
    ([("{texte_moyenne(o['hausses'][0], jours)}", "{texte_moyenne(o['hausses'][0], m['seances'])}")],
     "la moyenne par séance au lieu de par jour d'ouverture"),
    ([("donnent de {texte_part(v['min'][0], v['min'][1])} à", "donnent de {texte_part(v['max'][0], v['max'][1])} à")],
     "la variation : la plus haute deux fois"),
    ([("coûtent {texte_euros(m['couts'][0])} quand", "coûtent {texte_euros(m['couts'][1])} quand")], "le coût de 50 € pour 200 €"),
    ([('                    for val in sorted(m["valeurs"])],', '                    for val in reversed(sorted(m["valeurs"]))],')],
     "les valeurs à l'envers"),
    ([('(" :", NBSP + ":"), ', "")], "une espace ordinaire avant « : »"),
    # La relecture de la mesure écrite.
    ([("        _exige(n + retirees == seances * len(valeurs) * len(liste),", "        _exige(True or n + retirees == seances,")],
     "le total des fenêtres pas vérifié"),
    ([("        _exige(h50 <= h200 <= n,", "        _exige(h200 <= n,")], "plus de hausses à 50 € qu'à 200 € acceptées"),
    ([("    _exige(jour_ny == aujourd_hui,", "    _exige(True,")], "une date qui ne suit pas son heure acceptée"),
    ([("    _exige(debut <= fin < aujourd_hui,", "    _exige(debut <= fin <= aujourd_hui,")], "une séance du jour même acceptée"),
    ([('SYMBOLE = re.compile(r"[A-Z][A-Z.\\-]{0,5}")', 'SYMBOLE = re.compile(r".+")')], "n'importe quel nom de valeur"),
    ([(" and (avec == 0) == (h0 == 0),", ",")], "aucun jour avec une hausse malgré des hausses accepté"),
    ([("    if not os.path.isfile(chemin):      # un dossier", "    if False:      # un dossier")],
     "un dossier à la place du fichier, mal dit"),
    # Yahoo et l'écriture.
    ([("            dormir(PAUSE)", "            pass")], "pas de pause entre deux valeurs"),
    ([("            if n < ESSAIS - 1:\n                dormir(ATTENTE)", "            if True:\n                dormir(ATTENTE)")],
     "une attente après le dernier essai"),
    ([('history(period="60d", interval="5m", prepost=False', 'history(period="60d", interval="5m", prepost=True')],
     "les barres d'avant et d'après la séance demandées"),
    ([("gzip.compress(donnees, mtime=0)", "gzip.compress(donnees)")], "gzip avec la date d'écriture"),
    ([("md5_barres=hashlib.md5(donnees).hexdigest()", "md5_barres=hashlib.md5(gzip.compress(donnees)).hexdigest()")],
     "le md5 du fichier compressé"),
    ([("t.isoformat(), repr(float(o)), repr(float(c))", 't.isoformat(), f"{o:.6f}", f"{c:.6f}"')], "les prix arrondis à 6 chiffres"),
    ([('zip(d.index.tz_convert("UTC"), d["Open"]', 'zip(d.index.tz_convert(NY), d["Open"]')], "l'heure écrite à New York"),
    ([("        _effacer(temp_barres)\n        raise\n", "        raise\n")],
     "le fichier temporaire des barres laissé quand la mesure ne s'écrit pas"),
    ([("        _effacer(temp_mesure)\n        raise EcritureCoupee", "        raise EcritureCoupee")],
     "le fichier temporaire de la mesure laissé après une écriture coupée"),
    ([('        raise EcritureCoupee(f"les nouvelles barres sont en place', '        raise OSError(f"les nouvelles barres sont en place')],
     "une écriture coupée dite comme une erreur ordinaire"),
    ([('    return hashlib.md5(donnees).hexdigest() == mesure["md5_barres"]', "    return True")],
     "des barres qui ne vont pas avec la mesure, jamais vues"),
    ([('            raise MesureEnCours("une autre mesure est déjà en cours") from None', "            pass")],
     "deux mesures en même temps acceptées"),
    ([("    valider(mesure)\n    temp_barres", "    temp_barres")], "une mesure écrite sans être vérifiée"),
    ([("if t.second or t.microsecond or minute % 5 or not OUVERTURE_NY <= minute < FIN_NY:",
       "if t.second or t.microsecond or not OUVERTURE_NY <= minute <= FIN_NY:")], "des barres hors de la grille gardées"),
    ([("                if h is not None and not h.empty:", "                if h is not None:")], "une réponse vide de Yahoo acceptée"),
    ([('        except OSError as e:\n            sys.exit(f"La mesure écrite ne se lit pas',
       '        except KeyError as e:\n            sys.exit(f"La mesure écrite ne se lit pas')], "--afficher : une trace Python sur un dossier"),
    # Les refus qui manquaient (relecture du 23/11).
    ([('_exige(debut <= _jour(jour, "un jour de fenêtres retirées") <= fin,', '_exige(True or _jour(jour, "x"),')],
     "un jour retiré hors des séances accepté"),
    ([('_exige(isinstance(m.get("commit"), str) and isinstance(m.get("modifie"), bool) and isinstance(m.get("yfinance"), str),',
       "_exige(True,")], "une provenance d'un autre type acceptée"),
    ([('_exige(h <= n and d0 <= _jour(x[2], "une date de la variation") <= d1,', '_exige(_jour(x[2], "x") is not None,')],
     "une variation incohérente acceptée"),
    ([("    _exige(jours <= seances and avec <= jours", "    _exige(avec <= jours")], "plus de jours d'ouverture que de séances accepté"),
    # Les noms, les colonnes et les notes (relecture du 23/11).
    ([("\"Le mouvement médian compte les hausses et les baisses mêlées : un achat",
       "\"Le mouvement médian mêle les hausses et les baisses : un achat")], "« hausses et baisses mêlées » plus dit dans la note"),
    ([('    "quart": "Un quart d\'heure",\n    "tranche": "45 minutes",', '    "quart": "45 minutes",\n    "tranche": "Un quart d\'heure",')],
     "les noms de deux fenêtres échangés"),
    ([('"colonnes_valeurs": [{"nom": NOMS[f], "precision": precision(f)} for f in FENETRES],',
       '"colonnes_valeurs": [{"nom": NOMS[f], "precision": precision(f)} for f in reversed(FENETRES)],')],
     "les en-têtes du tableau des valeurs à l'envers"),
    ([('else texte_pct(fen[f]["par_valeur"][val], 2)) for f in FENETRES]}', 'else texte_pct(fen[f]["par_valeur"][val], 2)) for f in reversed(FENETRES)]}')],
     "les médianes d'une valeur à l'envers"),
    ([('"precision": precision(f), "mediane"', '"precision": precision("seance"), "mediane"')], "les heures de la séance sur chaque ligne"),
    # La variation calculée par mesurer().
    ([("in journalier}, seuils[0]) if journalier else None,", "in journalier}, seuils[1]) if journalier else None,")],
     "la variation au seuil de 50 €"),
    ([('        "variation": variation({v: journalier[v] for v in preparees if v in journalier}, seuils[0]) if journalier else None,',
       '        "variation": None,')], "la variation jamais calculée"),
    ([("{v: journalier[v] for v in preparees if v in journalier}", "{v: journalier[v] for v in journalier}")],
     "les valeurs pas mesurées comptées dans la variation"),
    # Le rapport du terminal.
    ([("{x['parts'][0]:>16}  {x['parts'][1]:>16}", "{x['parts'][1]:>16}  {x['parts'][0]:>16}")], "rapport : les parts échangées"),
    ([("{c['hausses'][0]} et {c['hausses'][1]}", "{c['hausses'][1]} et {c['hausses'][0]}")], "rapport : les comptes échangés"),
    ([('f"{n} « {f} »" for f, n in comptes.items()', 'f"{n} « ouverture »" for f, n in comptes.items()')],
     "rapport : toutes les retirées dites « ouverture »"),
    ([('(", ".join(f"{k} ({r})" for k, r in m["refusees"].items()) or "aucune")', '"aucune"')],
     "rapport : les valeurs pas mesurées jamais dites"),
    ([("de {texte_part(*v['min'][:2])} (période finie le", "de {texte_part(*v['max'][:2])} (période finie le")],
     "rapport : la variation, la plus haute deux fois"),
    # Les restes de la seconde relecture (23/11).
    ([("    if not os.path.isfile(chemin_barres):      # un dossier, ou un tube", "    if False:      # un dossier, ou un tube")],
     "un tube à la place des barres : la lecture bloque"),
    ([("    except Exception:       # gzip abîmé (zlib.error, EOFError), fichier illisible : il ne va pas\n        return False",
       "    except Exception:       # gzip abîmé (zlib.error, EOFError), fichier illisible : il ne va pas\n        return True")],
     "des barres abîmées dites « vont ensemble »"),
    ([("        except EcritureCoupee as e:\n            sys.exit(f\"Écriture coupée : {e}.\")\n", "")],
     "--mesurer : une écriture coupée dite « l'ancienne mesure reste »"),
    ([("            sys.exit(f\"Rien n'est remplacé : l'écriture a échoué ({raison_systeme(e)}). L'ancienne mesure reste.\")",
       "            raise")], "--mesurer : une trace Python sur un disque plein"),
    ([("        if annoncer:\n            annoncer()\n        barres, refusees", "        barres, refusees")],
     "--mesurer : l'annonce de la lecture jamais dite"),
    ([("    with verrou(f\"{chemin_mesure}.verrou\"):\n        if annoncer:\n            annoncer()\n",
       "    if annoncer:\n        annoncer()\n    with verrou(f\"{chemin_mesure}.verrou\"):\n")],
     "--mesurer : la lecture annoncée avant le verrou"),
    ([("    except (OverflowError, ValueError, OSError):        # « 0001-01-01", "    except (ValueError, OSError):        # « 0001-01-01")],
     "une date hors des dates possibles : une trace"),
    ([("    except RecursionError:\n        raise MesureIllisible(\"ce JSON est trop imbriqué pour être lu\") from None\n", "")],
     "un JSON trop imbriqué : une trace"),
    ([("        except Exception as e:      # une donnée piégée que valider()", "        except KeyError as e:      # une donnée piégée que valider()")],
     "--afficher : une trace Python sur une erreur inattendue"),
    ([('errno.ENOSPC: "disque plein"', 'errno.ENOSPC: "No space left on device"')], "un disque plein dit en anglais"),
]

def essayer(numero, changements):
    """Une erreur glissée dans une copie ; « attrapée » si les vérifications échouent."""
    with tempfile.TemporaryDirectory(prefix="mutation-seance-") as dossier:
        shutil.copy(os.path.join(PROJET, "seance.py"), dossier)
        for lien in ("banc.py", "stock_engine.py"):
            os.symlink(os.path.join(PROJET, lien), os.path.join(dossier, lien))
        chemin = os.path.join(dossier, "seance.py")
        texte = open(chemin, encoding="utf-8").read()
        for avant, apres in changements:
            if texte.count(avant) != 1:
                return numero, "INTROUVABLE", f"« {avant[:60]} » apparaît {texte.count(avant)} fois"
            texte = texte.replace(avant, apres)
        try:
            compile(texte, chemin, "exec")
        except SyntaxError as e:
            return numero, "INVALIDE", f"la copie ne se compile pas ({e.msg}, ligne {e.lineno})"
        with open(chemin, "w", encoding="utf-8") as f:
            f.write(texte)
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        try:
            r = subprocess.run([sys.executable, "seance.py", "--verifier"], cwd=dossier, capture_output=True,
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
        sys.exit(f"seance.py sans erreur ne passe pas ses vérifications ({detail}) : rien à mesurer.")
    print(f"   oui ({time.time() - debut:.0f} s).\n", flush=True)
    resultats = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futurs = {pool.submit(essayer, i, ERREURS[i - 1][0]): i for i in choix}
        for futur in concurrent.futures.as_completed(futurs):
            numero, etat, detail = futur.result()
            resultats[numero] = (etat, detail)
            print(f"{numero:3d}. {etat:11} {ERREURS[numero - 1][1]} — {detail}", flush=True)
    trous = [i for i, (e, _) in resultats.items() if e != "attrapée"]
    print(f"\n{len(choix) - len(trous)} erreurs volontaires attrapées sur {len(choix)} "
          f"({(time.time() - debut) / 60:.1f} min).")
    for i in sorted(trous):
        print(f"   {resultats[i][0]} : n° {i}, {ERREURS[i - 1][1]} {resultats[i][1]}")
    sys.exit(0 if not trous else 1)


if __name__ == "__main__":
    main()
