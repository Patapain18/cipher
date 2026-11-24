"""
Les erreurs volontaires du direct (plan du 24/11/2026) : cours_direct.py, les
morceaux nouveaux de ma_position.py et de web/server.py.

CE QUE ÇA FAIT
    Chaque erreur est glissée dans une COPIE du fichier, dans un dossier
    jetable (les autres modules du projet y sont liés, pas copiés), puis
    les trois vérifications du direct y sont lancées :
        python3 cours_direct.py --verifier
        python3 ma_position.py --verifier
        python3 outils/verif_serveur_direct.py
    Au moins une doit échouer. Une erreur qui passe montre un trou dans les
    vérifications, sauf si elle ne change rien de visible (dit à côté). Une
    copie qui ne se compile pas est dite INVALIDE : une erreur mal écrite ne
    teste rien. Les fichiers du projet ne sont jamais modifiés, et rien n'est
    lu chez Yahoo (le flux est un faux Yahoo local).

LANCER
    python3 outils/mutations_direct.py           # toutes (quelques minutes)
    python3 outils/mutations_direct.py 3 17      # seulement les erreurs n° 3 et 17
"""

import concurrent.futures
import os
import shutil
import subprocess
import sys
import tempfile
import time

PROJET = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

C, M, S = "cours_direct.py", "ma_position.py", os.path.join("web", "server.py")

# (fichier, [(avant, après), …], ce que l'erreur casse). « avant » doit être unique dans le fichier.
ERREURS = [
    # ── cours_direct.py : les messages
    (C, [("    if symbole not in abonnes:\n        return _ignorer(\"symbole\")", "    if False:\n        return _ignorer(\"symbole\")")],
     "un symbole pas demandé est gardé"),
    (C, [("    if not math.isfinite(prix) or prix <= 0:", "    if prix <= 0:")], "un prix NaN ou infini est gardé"),
    (C, [("    if not math.isfinite(prix) or prix <= 0:", "    if not math.isfinite(prix) or prix < 0:")], "un prix nul est gardé"),
    (C, [("    if not heure_s > 0 or heure_s > _maintenant() + FUTUR_S:", "    if not heure_s > 0:")],
     "une heure dans le futur est gardée"),
    (C, [("    if not heure_s > 0 or heure_s > _maintenant() + FUTUR_S:", "    if heure_s > _maintenant() + FUTUR_S:")],
     "une heure absente (0) est gardée"),
    (C, [("        if avant is not None and heure < avant.heure:", "        if avant is not None and heure <= avant.heure:")],
     "à la même seconde, le message suivant est refusé"),
    (C, [("        if avant is not None and heure < avant.heure:", "        if False:")], "une heure qui recule est gardée"),
    (C, [("    heure_s = heure_ms / 1000.0", "    heure_s = heure_ms / 1.0")], "l'heure lue en secondes au lieu de millisecondes"),
    (C, [("        d.ParseFromString(base64.b64decode(code, validate=True))", "        d.ParseFromString(code.encode())")],
     "le message pas décodé du base64"),
    # ── cours_direct.py : la vie du fil
    (C, [("        if 0 <= maintenant - instant < DEMANDE_S:", "        if 0 <= maintenant - instant:")],
     "une demande n'expire jamais"),
    (C, [('                    fin = _arret.is_set() or not voulus or _bourse["etat"] == "fermee"',
          '                    fin = _arret.is_set() or not voulus')], "la Bourse fermée ne ferme pas la connexion"),
    (C, [('        if not _voulus() or _bourse["etat"] == "fermee":\n            return\n        _fil["vivant"] = True',
          '        if not _voulus():\n            return\n        _fil["vivant"] = True')], "la Bourse fermée n'empêche pas d'ouvrir"),
    (C, [('        if _fil["vivant"] or _etat["etat"] == "indisponible":', '        if _fil["vivant"]:')],
     "une bibliothèque absente : il réessaie à chaque demande"),
    (C, [("                elif maintenant - battement >= BATTEMENT_S:", "                elif False:")], "plus de battement"),
    (C, [('                        ws.send(json.dumps({"unsubscribe": sorted(partis)}))', "                        pass")],
     "un symbole plus voulu n'est pas désabonné"),
    (C, [("                if _maintenant() - dernier > MUET_S:", "                if False:")], "un flux muet n'est jamais refait"),
    (C, [("                    a_garde, dernier = True, _maintenant()", "                    a_garde = True")],
     "un flux qui parle est refait comme s'il était muet"),
    (C, [("            attente = 0.0 if issue == \"fin\" else (REPRISE_S if a_garde else ATTENTE_S)",
          "            attente = 0.0")], "il réessaie sans attendre (il s'acharne)"),
    (C, [("            attente = 0.0 if issue == \"fin\" else (REPRISE_S if a_garde else ATTENTE_S)",
          "            attente = 0.0 if issue == \"fin\" else ATTENTE_S")], "pas de reprise rapide après une coupure"),
    (C, [("    except Exception as e:\n        with _verrou:\n            _changer(\"attente\", _en_francais(e))\n        return \"panne\", a_garde",
          "    except OSError as e:\n        with _verrou:\n            _changer(\"attente\", _en_francais(e))\n        return \"panne\", a_garde")],
     "une erreur imprévue tue le fil"),
    (C, [('            return f"Yahoo refuse la connexion (code {e.response.status_code})"', '            return str(e)')],
     "un refus de Yahoo dit en anglais"),
    (C, [('        _changer("connexion", None)\n        _etat["premier_essai"] = True\n', '        _etat["premier_essai"] = True\n')],
     "l'état ne dit pas « connexion » au démarrage"),
    (C, [('    premiere_connexion = e.get("etat") == "connexion" and bool(e.get("premier_essai"))',
          '    premiere_connexion = e.get("etat") == "connexion"')], "chaque nouvel essai après une panne fait relire vite"),
    (C, [('            with _verrou:\n                _etat["premier_essai"] = False\n', '')], "le premier essai ne finit jamais"),
    (C, [('        if seance == SEANCE_NORMALE:\n            _derniers_normale[symbole]', '        if True:\n            _derniers_normale[symbole]')],
     "l'après-séance remplace le cours de la séance normale"),
    (C, [('    vient_d_ouvrir = e.get("etat") == "ouvert" and e.get("depuis_s") is not None and e["depuis_s"] < 10',
          '    vient_d_ouvrir = e.get("etat") == "ouvert"')], "toujours rapide quand le flux est ouvert"),
    (C, [('        e["depuis_s"] = round(_maintenant() - e["depuis"], 1)', '        e["depuis_s"] = 0.0')],
     "depuis_s toujours à 0"),
    # Les trois protections du journal de websockets (le gestionnaire nul, propagate, le niveau) suffisent
    # chacune : on retire les trois.
    (C, [("_MUET.addHandler(logging.NullHandler())\n_MUET.propagate = False\n_MUET.setLevel(logging.CRITICAL + 1)",
          "_MUET.propagate = True")], "les messages de websockets remontent sur la sortie d'erreur"),
    # (Retirée : un message binaire non décodé n'a aucun effet, json.loads lit aussi les octets.)
    # ── ma_position.py
    (M, [("    if direct is not None and (_maintenant_utc() - direct.heure).total_seconds() <= cours_direct.FRAIS_S:",
          "    if direct is not None and (_maintenant_utc() - direct.heure).total_seconds() < cours_direct.FRAIS_S:")],
     "à 30 s pile, le cours du flux n'est plus frais"),
    (M, [("    if direct is not None and (_maintenant_utc() - direct.heure).total_seconds() <= cours_direct.FRAIS_S:",
          "    if direct is not None:")], "un cours du flux de n'importe quel âge, sans lire les barres"),
    (M, [("    direct = cours_direct.cours(symbole) if symbole != CHANGE else None", "    direct = cours_direct.cours(symbole)")],
     "le dollar pris au flux"),
    (M, [("    if direct is not None and direct.heure >= heure:", "    if direct is not None and direct.heure > heure:")],
     "à égalité avec la barre, la barre gagne"),
    (M, [("    if direct is not None and direct.heure >= heure:", "    if direct is not None:")], "le flux gagne même plus vieux que la barre"),
    (M, [("            if direct is not None:\n                return _cours(direct.prix, direct.heure, \"direct\")\n            raise",
          "            raise")], "Yahoo sans barres : une panne même avec un cours du flux"),
    (M, [("    if deja and 0 <= age < MEMO_ACTUEL_S:", "    if deja and 0 <= age < 100 * MEMO_ACTUEL_S:")],
     "les barres gardées bien plus de 30 s"),
    (M, [("    elif deja and 0 <= age < MEMO_PERIME_S and cours_direct.actif():", "    elif False:")],
     "dans le serveur, un Yahoo lent retient la réponse"),
    (M, [("    elif deja and 0 <= age < MEMO_PERIME_S and cours_direct.actif():", "    elif deja and 0 <= age < MEMO_PERIME_S:")],
     "hors du serveur aussi, des barres de 5 min servies sans être relues"),
    (M, [("        if fil is not None and fil.is_alive():\n            return", "        if False:\n            return")],
     "plusieurs relectures des mêmes barres à la fois"),
    (M, [("MEMO_PERIME_S = 45", "MEMO_PERIME_S = 300")], "au rythme de 60 s, le cours de la minute d'avant"),
    (M, [('                       "source_du_cours": source_du_cours(lu),', '                       "source_du_cours": "direct",')],
     "chaque ligne se dit « direct »"),
    (M, [('        if l.get("source_du_cours") != "direct":\n            continue', '        if False:\n            continue')],
     "une ligne des barres compte pour « en direct »"),
    (M, [("        if age <= cours_direct.FRAIS_S:", "        if True:")], "une ligne du flux de n'importe quel âge fait « en direct »"),
    (M, [('    return {"en_direct": en_direct, "rapide": cours_direct.rythme_rapide(en_direct),',
          '    return {"en_direct": en_direct, "rapide": True,')], "toujours rapide"),
    (M, [('    cours_direct.suivre("position", [l["ticker"] for l in detenues] + [INDICE])',
          '    cours_direct.suivre("position", [l["ticker"] for l in detenues])')], "SPY pas suivi"),
    (M, [('    cours_direct.suivre("position", [l["ticker"] for l in detenues] + [INDICE])',
          '    cours_direct.suivre("position", [l["ticker"] for l in detenues] + [INDICE, CHANGE])')], "le dollar suivi"),
    (M, [("    if recent and 0 <= time.time() - recent[0] < MEMO_ACTUEL_S:", "    if False:")],
     "un cours passé récent relu à chaque fois"),
    (M, [("    else:\n        _memo_recent[cle] = (time.time(), v)", "    else:\n        _memo_passe[cle] = v")],
     "un cours passé récent gardé pour toujours"),
    # ── web/server.py
    (S, [('    return {"date_seance": close.index[-1].date().isoformat(), "references": refs}',
          '    return {"date_seance": close.index[-2].date().isoformat(), "references": refs}')], "la date de la veille"),
    (S, [('    for cle, n in (("change_1d", 1), ("change_1w", 5), ("change_1m", 22)):',
          '    for cle, n in (("change_1d", 0), ("change_1w", 5), ("change_1m", 22)):')], "le repère d'un jour est le cours lui-même"),
    (S, [('    for cle, n in (("change_1d", 1), ("change_1w", 5), ("change_1m", 22)):',
          '    for cle, n in (("change_1d", 1), ("change_1w", 22), ("change_1m", 5)):')], "les repères d'une semaine et d'un mois échangés"),
    (S, [("        refs[cle] = v if v is not None and math.isfinite(v) and v > 0 else None", "        refs[cle] = v")],
     "un repère nul gardé"),
    (S, [('        c = cours_direct.cours_normal(r["ticker"])', '        c = cours_direct.cours(r["ticker"])')],
     "l'après-séance dans le tableau"),
    (S, [('        if c is None or r.get("date_seance") != aujourd_hui:', '        if c is None:')],
     "un scan d'hier avec le cours d'aujourd'hui"),
    (S, [("        if not frais and (scan_utc is None or c.heure <= scan_utc):", "        if False:")],
     "un cours plus ancien que le scan dans le tableau"),
    (S, [("        if not frais and (scan_utc is None or c.heure <= scan_utc):", "        if not frais:")],
     "la case recule dès que le cours n'est plus frais"),
    (S, [("        frais = (maintenant - c.heure).total_seconds() <= cours_direct.FRAIS_S", "        frais = True")],
     "tout cours du tableau se dit frais"),
    (S, [('    en_direct = any(v["frais"] for v in valeurs.values())', "    en_direct = bool(valeurs)")],
     "« en direct » sans aucun cours frais"),
    (S, [('    return {**pos, "direct": ma_position.etat_du_direct(pos["lignes"])}', "    return pos")],
     "l'état du direct servi depuis le cache"),
    (S, [("    pos = position_servie()\n", "    pos = ma_position_en_direct()\n")],
     "le journal calcule sa propre position (deux photos des cours)"),
    (S, [("            v[cle] = (c.prix - ref) / ref * 100 if ref else None", "            v[cle] = (c.prix - ref) / c.prix * 100 if ref else None")],
     "la variation divisée par le cours au lieu du repère"),
    (S, [("    rangs = [r for r in (scan or {}).get(\"rows\", []) if not r.get(\"error\") and r.get(\"ticker\")]",
          "    rangs = [r for r in (scan or {}).get(\"rows\", []) if r.get(\"ticker\")]")], "une valeur en erreur suivie"),
    (S, [('        entree = _cache.get("/api/stocks") or _cache.get("/api/stocks:classement")\n    scan = entree["value"] if entree else None\n    rangs',
          '        entree = _cache.get("/api/stocks")\n    scan = entree["value"] if entree else None\n    rangs')],
     "le classement gardé ignoré"),
    (S, [('    if bourse in ("ouverte", None):\n        cours_direct.suivre("tableau"', '    if True:\n        cours_direct.suivre("tableau"')],
     "les 49 demandés au flux hors de la séance normale"),
    (S, [('    if bourse in ("ouverte", None):\n        cours_direct.suivre("tableau"', '    if bourse == "ouverte":\n        cours_direct.suivre("tableau"')],
     "les 49 pas demandés quand l'état de la Bourse est inconnu"),
    (S, [('    return {**ma_position.journal_compare(prix, pos["eurusd"]), "position": pos}',
          '    return ma_position.journal_compare(prix, pos["eurusd"])')], "le journal ne rend pas sa position"),
    (S, [('    return 1 if cours_direct.etat().get("etat") in ("connexion", "ouvert") else 60',
          '    return 60')], "la position gardée 60 s pendant le direct"),
    (S, [('    return 1 if cours_direct.etat().get("etat") in ("connexion", "ouvert") else 60',
          '    return 1')], "la position jamais gardée hors du direct"),
    (S, [("    bourse = etat_de_la_bourse()\n    cours_direct.bourse(bourse)\n    p = ma_position.position(rang_de)",
          "    p = ma_position.position(rang_de)\n    bourse = etat_de_la_bourse()\n    cours_direct.bourse(bourse)")],
     "l'état de la Bourse donné au flux APRÈS le calcul"),
    (S, [('    a_relire = s["periode"] is None or not 0 <= age < 3600 or (etat == "fermee" and age >= 300)',
          '    a_relire = s["periode"] is None or not 0 <= age < 3600')], "fermée : pas relue avant une heure"),
    (S, [('    a_relire = s["periode"] is None or not 0 <= age < 3600 or (etat == "fermee" and age >= 300)',
          '    a_relire = True')], "les séances relues à chaque demande"),
    (S, [('    if a_relire and time.time() - s["essai"] >= 60 and _seances_verrou.acquire(blocking=False):',
          '    if a_relire and _seances_verrou.acquire(blocking=False):')], "Yahoo en panne : un essai à chaque demande"),
    (S, [('    if force and os.environ.get("FLUX_URL"):', '    if force:')], "FLUX_BOURSE marche sans faux flux"),
    (S, [('        fd = os.open(os.path.join(dossier, "serveur.log"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)',
          '        fd = os.open(os.path.join(dossier, "serveur.log"), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)')],
     "le fichier d'erreurs jamais vidé"),
    (S, [('    if not os.environ.get("DASHBOARD_PARENT_PID"):\n        return\n    dossier', '    dossier')],
     "la sortie d'erreur détournée même au Terminal"),
    (S, [("    os.dup2(fd, 2)\n", "")], "la sortie d'erreur jamais détournée"),
]

VERIFS = [
    ("cours_direct.py", [sys.executable, "cours_direct.py", "--verifier"], ("  NON ",)),
    ("ma_position.py", [sys.executable, "ma_position.py", "--verifier"], ("✗",)),
    ("verif_serveur_direct.py", [sys.executable, os.path.join("outils", "verif_serveur_direct.py")], ("✗",)),
]


def essayer(numero, fichier, changements):
    """Une erreur glissée dans une copie ; « attrapée » si l'une des vérifications échoue."""
    with tempfile.TemporaryDirectory(prefix="mutation-direct-") as dossier:
        for nom in os.listdir(PROJET):
            if nom.endswith(".py"):
                os.symlink(os.path.join(PROJET, nom), os.path.join(dossier, nom))
        os.makedirs(os.path.join(dossier, "web"))
        os.makedirs(os.path.join(dossier, "outils"))
        for copie in (C, M, S, os.path.join("outils", "verif_serveur_direct.py")):
            cible = os.path.join(dossier, copie)
            if os.path.islink(cible):
                os.remove(cible)
            shutil.copy(os.path.join(PROJET, copie), cible)
        if numero:
            chemin = os.path.join(dossier, fichier)
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
        env.pop("SERVEUR_A_VERIFIER", None)
        for nom, commande, marques in VERIFS:
            try:
                r = subprocess.run(commande, cwd=dossier, capture_output=True, text=True, timeout=300, env=env)
            except subprocess.TimeoutExpired:
                return numero, "attrapée", f"{nom} : trop long (plus de 5 min)"
            rates = [l.strip() for l in r.stdout.splitlines() if l.startswith(marques)]
            if r.returncode != 0:
                if rates:
                    return numero, "attrapée", f"{nom} : {len(rates)} échec(s), dont : {rates[0][:100]}"
                derniere = (r.stderr.strip().splitlines() or ["?"])[-1]
                return numero, "attrapée", f"{nom} : plantage : {derniere[:100]}"
        return numero, "PASSE", ""


def main():
    choix = [int(x) for x in sys.argv[1:]] or list(range(1, len(ERREURS) + 1))
    debut = time.time()
    print("Témoin (aucune erreur) : les trois vérifications doivent passer…", flush=True)
    _, etat, detail = essayer(0, None, [])
    if etat != "PASSE":
        sys.exit(f"Sans erreur, les vérifications ne passent pas ({detail}) : rien à mesurer.")
    print(f"   oui ({time.time() - debut:.0f} s).\n", flush=True)
    resultats = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futurs = {pool.submit(essayer, i, ERREURS[i - 1][0], ERREURS[i - 1][1]): i for i in choix}
        for futur in concurrent.futures.as_completed(futurs):
            numero, etat, detail = futur.result()
            resultats[numero] = (etat, detail)
            print(f"{numero:3d}. {etat:11} {ERREURS[numero - 1][2]} — {detail}", flush=True)
    trous = [i for i, (e, _) in resultats.items() if e != "attrapée"]
    print(f"\n{len(choix) - len(trous)} erreurs volontaires attrapées sur {len(choix)} "
          f"({(time.time() - debut) / 60:.1f} min).")
    for i in sorted(trous):
        print(f"   {resultats[i][0]} : n° {i}, {ERREURS[i - 1][2]} {resultats[i][1]}")
    sys.exit(0 if not trous else 1)


if __name__ == "__main__":
    main()
