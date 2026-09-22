> **Notes techniques, en français.** Ce document était le README du projet :
> le mode d'emploi au quotidien, les mesures du signal et leur historique.
> La présentation du projet est dans le [README](../README.md).

# Bots de trading

Projet centré sur les **actions**. Les bots crypto restent là et se lancent à
la main, mais ils ne sont plus branchés au tableau de bord.

## Lancer le tableau de bord

**Double-clique « Signaux actions » sur le Bureau.** C'est une vraie
application macOS : icône dans le Dock, fenêtre à elle, Cmd+Q pour quitter.
Plus aucun navigateur dans l'histoire. Elle est aussi lançable depuis le Hub.

En ligne de commande, si tu veux voir les logs du serveur :

```bash
python3 web/server.py
```

### L'application

`web/dashboard_app.py` est une **fenêtre**, pas une copie du dashboard : elle
démarre `web/server.py` dans ce dossier, attend que le port réponde, ouvre une
WKWebView dessus, et arrête le serveur quand on la ferme.

Ce choix a deux conséquences qui valent la peine d'être comprises :

- **Le bundle pèse 13 Mo au lieu de 300.** pandas, numpy et yfinance restent
  dans `/usr/bin/python3` — l'interpréteur qui fait déjà tourner le bot sous
  launchd. Les empaqueter aurait triplé le poids pour une application qui, de
  toute façon, ne peut tourner que sur cette machine.
- **Il n'existe qu'une seule version du code.** Retoucher une page du
  dashboard se voit à la prochaine ouverture, sans reconstruire quoi que ce
  soit. Une copie embarquée aurait fini par diverger — et surtout elle aurait
  cherché `data/` à l'intérieur du bundle, où il n'y a rien : un tableau vide,
  sans la moindre erreur.

**Elle n'est donc pas distribuable, et c'est voulu.** Elle lit `data/`, que le
bot alimente ici. Envoyée à quelqu'un d'autre, elle ouvrirait une fenêtre sur
un dossier qui n'existe pas. C'est le Hub qui se transmet ; ce dashboard reste
une vue sur cette machine.

Fermer la fenêtre ferme une vue, **pas la collecte** : le bot tourne sous
launchd et continue de scanner toutes les cinq minutes.

Pour la reconstruire après une modification de `dashboard_app.py` ou de
`fenetre_native.py` (inutile après une simple retouche du dashboard) :

```bash
./web/build.sh
```

Il lit la version dans `server.py`, régénère l'icône si besoin
(`web/faire_icone.py`, aucune image à versionner) et réinstalle
`~/Desktop/Signaux actions.app`.

Le dashboard **lit** : momentum et rang du jour, cours, état technique,
historique enregistré, et la fiabilité mesurée du signal. Il ne passe aucun
ordre et n'utilise aucune clé d'API.

**Comprendre les signaux** (`explications.html`, lien depuis le dashboard) :
d'où vient une notification d'achat, comment le score est construit, et
surtout ce que valent ces signaux — chiffres à l'appui. Tous les nombres de
cette page sont lus depuis `data/eval_report.json` et
`data/ranking_report.json` et portent leur date de mesure : aucun n'est écrit
en dur, pour qu'ils ne puissent pas devenir faux en silence.

La page dit ce que les mesures disent, y compris quand c'est défavorable. En
l'état : le tableau du rendement par niveau de score ne monte pas, un 0/5
rapporte autant qu'un 5/5, et tous les niveaux qui déclenchent restent sous
la référence « ne rien faire ». Le verdict s'ajuste tout seul si une mesure
future devient concluante.

**Colonne « Résultats ».** Date de la prochaine publication de résultats, mise
en évidence à moins de 7 jours. Elle ne dit pas dans quel sens le cours ira —
seulement qu'il bougera probablement fort : acheter la veille d'une publication
revient à jouer à pile ou face sur un écart d'ouverture.

Pourquoi cette donnée et pas l'actualité en général ? Une dépêche est intégrée
au cours en quelques secondes ; quand le bot la scanne, elle est déjà dans le
prix. Et on ne pourrait même pas le vérifier : les archives de presse sont
horodatées à la dernière modification de l'article, donc un backtest « verrait »
l'information après le mouvement qu'elle est censée expliquer — magnifique et
entièrement faux. Une date de publication, elle, est connue des semaines à
l'avance et ne triche pas.

Le calendrier est mis en cache 24 h (`data/earnings_cache.json`) et rafraîchi
**par le bot**, jamais par le serveur web : interroger 49 valeurs prend ~35 s,
et une page ne doit pas attendre le réseau. Seules les dates sont stockées, le
nombre de jours se recalcule à l'affichage.

**Mes positions.** La seule partie de la page qui écrit. Elle note les achats
que tu as déjà passés chez ton courtier — quantité et prix d'entrée — et en
affiche le P&L latent, valeur par valeur et au total. Le bot relit ce carnet
à chaque cycle, ce qui lui permet de n'émettre un signal de vente que sur une
valeur que tu détiens réellement.

Les écritures se limitent à `data/stock_positions.json` : aucun ordre, aucune
clé d'API. Elles exigent un en-tête `X-Dashboard` qu'une page tierce ne peut
pas ajouter sans un préalable CORS auquel le serveur ne répond pas — sans
quoi n'importe quel site ouvert dans ton navigateur pourrait écrire dans ton
carnet, puisque le serveur écoute sur localhost. Le ticker doit appartenir à
l'univers suivi, quantité et prix doivent être positifs, et le fichier est
remplacé de façon atomique pour que le bot ne lise jamais un JSON à moitié
écrit.

**Il est organisé autour du momentum**, le moins mauvais des critères
testés (voir les réserves plus bas) : c'est la colonne mise en avant, le tri par défaut du tableau, la
vue ouverte au chargement et le sujet des cartes de synthèse. Le score du
moteur reste consultable sous le nom de « score technique », en retrait —
mesuré sans pouvoir prédictif, il décrit une valeur mais n'annonce rien, et
lui donner l'allure d'un signal serait mentir sur ce qu'il vaut.

Quatre filtres au-dessus du tableau (Top momentum / Signaux techniques /
Détenues / Tout) — avec 49 lignes, tout afficher en permanence noierait le
peu qui demande une décision.

## La vue baie — c'est le site

L'application s'ouvre **dans la pièce** : un plateau vitré sur quatre côtés au
onzième étage, une ville tout autour, un soleil qui suit l'heure réelle, et le
dashboard réparti sur **cinq panneaux de verre** suspendus devant les vitrages.
On passe de l'un à l'autre en tournant la tête — flèches à droite et à gauche,
touches ← →, ou en tirant le fond à la souris. **Échap** ramène à la vue plan,
qui reste derrière, et **V** y retourne.

Les cinq panneaux, de gauche à droite : **mes positions** (le carnet, le seul
endroit où l'on écrit) · **fiabilité du signal** · **synthèse** (on arrive ici :
la tête de classement, les quatre chiffres, six lignes) · **le tableau
complet** (49 lignes, qui défilent dans le panneau) · **historique et
bascules**. Ce sont les sections RÉELLES de la page, déplacées dans la pièce
— pas des copies : le formulaire du carnet reste un formulaire, les filtres
des filtres, et tout se rafraîchit en place. À la fermeture, tout revient à sa
place dans la page.

### Pourquoi une vue, et pas un décor

Le tableau des 49 valeurs mesure 1 373 px de large dans une fenêtre qui en
fait 1 440 : il ne reste pas un pixel à donner à un décor autour des
chiffres. Et surtout, tout ce qui rend une scène belle — la profondeur, le
mouvement, la translucidité — coûte exactement ce qui rend un tableau lisible :
des pixels, de l'immobilité, de l'opacité. Le dashboard n'est donc pas posé
*sur* la ville : il est *dans* la pièce, sur du verre à 98 %, et chaque panneau
ne montre qu'une chose.

C'est aussi une question d'honnêteté. Ce README passe deux cents lignes à
démonter ses propres résultats flatteurs — « autrement dit un pile ou face ».
Un contenant est une affirmation : l'étage vitré ne dit rien des chiffres, il
les tient à distance de lecture. Rien dans la ville ne commente un cours.

### Ce que Mathis a décidé (8 septembre 2026)

Le soleil suit l'heure réelle mais **jamais la nuit** ; **cinq panneaux** ;
on passe de l'un à l'autre en **tournant la caméra** ; **ciel bleu, nuages,
vraies ombres**. Conséquence : l'immeuble est vitré tout autour et la ville
l'entoure sur 360° — c'était la demande d'origine. La règle « lumière du nord,
aucune couleur nouvelle » de la version précédente est abandonnée à cette
date, en connaissance de cause : le seul bleu de la scène reste un mélange
d'indigo (--accent) et de papier, et la couleur du soleil est dérivée du blanc
chaud du papier par une loi physique — aucun token n'a été ajouté.

### Le soleil, le ciel, les ombres

- **Position** : formule de la NOAA (déclinaison, équation du temps, angle
  horaire) pour Paris, validée contre l'algorithme de Meeus à 0,3° près.
  Référence : le 8 septembre 2026 à 13 h 20 CEST, élévation 46,6°, azimut
  169,7° (midi solaire à 13 h 48). Recalculé toutes les cinq minutes.
- **Jamais la nuit** : l'heure est bornée à 7 h–19 h puis ramenée vers midi
  tant que le soleil n'est pas à 6° au-dessus de l'horizon. Borner l'heure ne
  suffisait pas : le 8 septembre à 7 h il est à −3,3°, et d'octobre à mars les
  deux bornes sont sous l'horizon.
- **Couleur et intensité** : masse d'air de Kasten-Young
  `m = 1/(sin e + 0,50572·(e° + 6,07995)^−1,6364)`, transmission par canal
  `T = exp(−(0,130, 0,193, 0,337)·(m − 1))`, couleur = papier · T/T.r,
  intensité `I = T.r^0,6`. À 46° le soleil est #fff7e6 ; à 12° (19 h le 8/9)
  rgb(255,202,118) ; à 6°, la borne, rgb(255,154,49).
- **Ombres portées** : deux cartes d'ombre de 2048², orthographiques depuis le
  soleil, cadrées sur des DISQUES (900 m et 300 m autour de l'immeuble — le
  carré projeté faisait un texel de 1,15 m, le disque 0,88 × 0,45), profondeur
  seule, comparaison matérielle avec PCF 3 × 3. Biais : facteur de pente 2 et
  un décalage le long de la normale de 0,45 m — le second paramètre de
  `polygonOffset` est sans effet sous Metal (mesuré), et un biais constant fait
  décoller les ombres. Refaites seulement quand le soleil a bougé de plus d'un
  demi-degré. L'immeuble de l'observateur (40 × 40 × 60 m) n'existe que dans
  cette passe : il projette sur ses voisins, on ne le voit jamais. Mémoire :
  16 Mo par carte (le D24 est stocké en 32 bits flottants sur Apple).
- **Le ciel** est calculé par rayon à partir de la base caméra — fixé au
  monde, pas à l'écran, sinon il tournerait avec la tête. Blanc du papier à
  l'horizon, bleu voilé au zénith (mix(--accent, --bg-elevated, 0,45) : 0,35
  tire vers l'ardoise, 0,55 vers le gris), halo et disque, cumulus sur un plan
  à 1 500 m éclairés côté soleil. Immobiles entre deux événements ; ils
  dérivent (3 m/s) pendant qu'un événement joue.
- **Le soleil dans la pièce** : analytique. Pour un point du sol on remonte le
  rayon vers le soleil, on trouve le vitrage traversé, on vérifie qu'il passe
  entre l'allège et la traverse haute et hors d'un montant — vérifié contre une
  référence indépendante à 0 pixel près. À 19 h la lumière entre par l'ouest et
  hachure le sol jusqu'au vitrage nord ; le matin de septembre, par l'est. Un
  lambert strict (sin 8° = 0,14) effacerait la tache du soir : plancher
  perceptif 0,55 + 0,45·sin(él), décision de design, pas oubli.
  **Le soleil ajoute de la lumière, il ne retire pas de bleu** (correction du
  9 septembre, sur une question de Mathis : « c'est le soleil qui rend le sol
  jaune ? »). La première version multipliait le sol par la couleur du soleil
  et coupait à 1 : sur un sol déjà presque blanc, le rouge et le vert ne
  bougeaient pas et le bleu était divisé par deux — l'ombre d'un montant
  était plus lumineuse que le soleil (mesuré : 229·227·222 à l'ombre contre
  241·237·188 au soleil), l'œil lisait « sol jaune ». Maintenant : mélange en
  écran `1 − (1 − sol)·(1 − L)`, qui ne peut assombrir aucun canal, avec
  `L = couleurRel · s · SOL_GAIN` ; et comme l'écran ne sait pas montrer un
  soleil plus blanc que le papier, le sol à l'ombre est exposé plus bas quand
  le soleil est fort (`SOL_OMBRE` = 0,22 × intensité × facteur). Mesuré vue
  nord à 8 h 30 : soleil/ombre = 0,94 avant (le soleil était plus sombre que
  l'ombre), 1,02 en écran seul (invisible), 1,11 avec l'ombre exposée.

### La caméra qui tourne

L'œil ne bouge jamais ; seule la cible tourne (lacet ψ, positif vers l'est, la
droite de l'écran). Les panneaux sont sur un arc de 30° autour de l'œil,
chacun face à lui : le courant à 3 m, les autres à 4,5 m — deux tiers de la
taille, en retrait, à 40 % d'opacité et inertes (clavier, souris, lecteur
d'écran : leur texte fait 5 à 7 px). Le tour dure 800 ms à 30 images par
seconde, et à chaque image la couche CSS est replacée PUIS le canvas redessiné,
dans le même rappel — une image de retard vaut 20 px de glissement. Ensuite la
scène se fige à nouveau.

Les panneaux se croisent pendant l'échange (le sortant recule, l'entrant
avance : ils passent par le même rayon, à 12–90 px du centre, pendant 30 à
95 ms). Ce n'est pas un bug de rendu — Core Animation trie l'intersection
par pixel, 0 pixel faux mesuré — mais c'est visible : le sortant s'efface en
200 ms, l'entrant s'allume après 300 ms.

La caméra partagée (la perspective CSS = la focale de la projection) tient au
lacet : la chaîne vaut une conjugaison exacte par diag(1, −1, 1, 1). Mesuré
par coin, sur les cinq panneaux courants : **écart ≤ 0,011 px**.

### Ce qui a été mesuré dans la vraie fenêtre

Sept prototypes dans la WKWebView avant d'écrire une ligne, et trois choses
qui ne marchent PAS comme on l'attend :

- **Le défilement natif d'un `overflow: auto` vaut zéro** dans cette chaîne 3D,
  dans tous les états, et dépend de la géométrie des AUTRES couches ; en plus,
  un tableau qui déborde est composé hors du tri de profondeur (son texte
  passait par-dessus le panneau courant, 1,27 %). Le tableau défile donc en
  JavaScript (molette, PageUp/PageDown, espace) sous `overflow: hidden`, avec
  une jauge dessinée.
- **Le reflet du panneau (`::before`) était peint par-dessus le texte** : HOLD
  rendu à 4,30:1 au cœur du glyphe, « +569 % » à 4,50 au lieu de 5,84 — un audit
  sur les couleurs nominales ne le voit pas. `isolation: isolate` et `z-index:
  −1`, HOLD à 50 % : pire contraste mesuré 4,65, HOLD 5,03. Le verre à 94 % borne
  le fond quel que soit le décor ; à 88 % il casse sur un montant.
- **Un Tab réel faisait défiler la scène de 954 px** pour « révéler » un
  panneau hors cadre. Tab cycle dans le panneau courant, les autres sont
  inertes, et le défilement de la scène est remis à zéro à chaque focus.

Et la taille du texte rendu dépend de la LARGEUR de la fenêtre (15 px → 11,1 à
1440, 7,3 à 1000) : la baie ne s'ouvre qu'à partir de 1200 × 690, et se referme
vers le plan si la fenêtre passe dessous, ou si « réduire les animations » est
activé.

### Ce que la ville fait

Neuf événements, un seul à la fois, 12 à 30 s de silence après chacun ; entre
deux, rien. Le prix est PAR IMAGE, pas par pixel — chaque image fait
recomposer la fenêtre — donc jamais plus de 15 images par seconde. **Le soleil
sort d'un nuage** (il ouvre chaque visite, cinq secondes après l'ouverture ; une
fenêtre accroche la lumière au pic) · **l'ombre d'un nuage** qui glisse ·
**la brume** qui épaissit · **un avion** · **la grue** qui tourne et reste où
elle s'arrête · **la vapeur** d'une chaufferie · **une nappe** de brume dans les
rues · **le plafond** de nuages qui se lève · **un vol d'oiseaux**. Tout est
météo ou trafic ; rien ne clignote en vert ou en rouge.

### Ce qu'elle coûte

| état (vraie fenêtre, 180 s) | WebContent | processus GPU | images |
|---|---|---|---|
| version précédente, un panneau, ville vivante | 0,69 % | 0,64 % | 756 |
| **le site : cinq panneaux, ombres, ciel, cumulus** | **1,91 %** | **1,58 %** | **1 245** |

Soit **3,5 % d'un cœur** côté WebKit quand la ville vit, ≈ 5 ms de CPU par
image (contre 3 avant : à chaque image, WebKit recompose aussi les cinq
panneaux). Mesuré le 8 septembre à 23 h 02 (`web/outils/mesure_baie.py`,
`mesure_baie.json`). La colonne WindowServer de cette mesure n'est PAS
attribuable : 27 % pendant la phase « plan » et 28 % vue refermée — le bureau
n'était pas calme (une autre application occupait l'écran). La séquence
d'événements après l'ouverture (trouée 30 s, puis nappe, plafond…) explique
1 245 images en 180 s : 6,9 par seconde en moyenne, contre 4,2 avant.
À refaire bureau calme : `/usr/bin/python3 web/outils/mesure_baie.py`. La
version précédente avait mesuré +2,5 % de WindowServer pour 4,2 images/s ;
l'ordre de grandeur attendu ici est +4 à +6 %.

Mesures GPU par image (prototypes, WKWebView, 2880 × 1722, MSAA 4×, N images
puis `readPixels` — `gl.finish()` ne synchronise pas dans le processus GPU de
WebKit, et `performance.now()` a 1 ms de résolution) : ville en disque 8 199
instances ≈ 1,57 ms, PCF 3 × 3 +0,10 à 0,15 ms, ciel à cumulus +0,015 ms
(0,445 plein écran, mais il ne remplit que les pixels vides), soleil dans la
pièce +0,02 ms. Passe d'ombre : 0,35 ms pour les deux cartes, à chaque
mouvement du soleil ; la première fois 2 à 3 fois plus (compilation Metal),
payée à l'ouverture. Génération de la ville : 2,7 ms.

### Vérifier chez soi

- `?debug=1` : `__vueBaie.aller(i)`, `lacet(ψ)`, `soleil('2026-09-08T19:00:00+02:00')`,
  `forcer('avion', 0.5)`, `images`, `ecart()` par panneau et par coin,
  `fantome(true)`.
- `?plan` : ouvre sur la vue plan.
- `web/outils/capture_baie.py SORTIE.png --debug --js "…"`, `profil.py`,
  `mesure_baie.py`, `node web/outils/test_ville.js`.
- Le bandeau de la page plan est toujours la même ville, projetée par
  `web/faire_baie.py`.

### Ce qu'elle ne fait pas

Pas de nuit (décision), pas de pluie, pas de fleuve (80 % derrière un panneau).
Les panneaux ne portent pas d'ombre sur le sol : la tache passe sous eux, c'est
du verre imprimé, on ne l'explique pas autrement. Le côté sud du plateau n'est
pas dessiné (jamais dans le champ) ; il ne sert qu'à laisser entrer le soleil.
Elle **ne se souvient pas** du panneau où l'on était : on arrive toujours sur
la synthèse. Si le système reprend la carte graphique, elle se referme sur le
plan en le disant, et repart d'un canvas neuf.

## L'univers suivi

**49 valeurs sur 8 secteurs**, définies dans `stock_engine.UNIVERSE` (un seul
endroit : le bot, le serveur et l'évaluateur y puisent tous les trois).
Remplaçable via `STOCK_TICKERS` dans `.env`.

L'univers d'origine — 9 valeurs, presque toutes des semi-conducteurs — a été
élargi parce que la mesure a montré qu'un classement n'y avait rien à
départager : des titres qui montent et descendent ensemble ne se distinguent
pas. Ce sont les écarts **entre secteurs** qui font vivre un classement.
Effet immédiat : le top 12 par momentum couvre maintenant 6 secteurs au lieu
d'un seul.

Le téléchargement est groupé (`fetch_many`) : une requête pour les 49
valeurs, scan complet en ~4 s. Une par une, il aurait fallu des minutes.
Pour rejouer les mesures sur l'ancien univers : `--legacy-universe`.

## Le moteur de signal (v2)

`stock_engine.py` remplace le score à poids fixes de la v1. Ce qu'il ajoute :

- **Détection de régime** (ADX). En tendance, il écoute surtout le suivi
  (EMA, MACD, structure, momentum) ; en range, surtout le retour à la moyenne
  (RSI, Bollinger, écart à la moyenne). La v1 additionnait les deux familles,
  qui se contredisent et s'annulaient — d'où des scores bloqués à 3/5.
- **Distances en ATR**, donc comparables d'une valeur à l'autre.
- **Confirmation par le volume**, absente de la v1.
- **Calibration glissante** : le score situe la note du jour par rapport aux
  250 séances précédentes de la même valeur, sur une fenêtre qui ne regarde
  que le passé.
- **Une fourchette de prix**, pas une prédiction ponctuelle : cours ± un
  écart-type à l'horizon choisi.

Un `SELL` n'est émis que sur une valeur effectivement détenue — ce bot ne
pratique pas la vente à découvert, et « vendre » une action qu'on n'a jamais
achetée n'a pas de sens. Le signal baissier reste lisible dans `sell_score`.

Repasser à l'ancien score : `python3 stock_bot.py --legacy`.

## Mesurer le signal

```bash
python3 stock_eval.py --period 5y --horizon 10 --save
```

Rejoue chaque algorithme séance par séance et confronte les scores au
rendement qui a suivi : rendement par niveau de score, Information
Coefficient, et surtout l'écart avec « acheter et ne rien faire ».
`--save` écrit le rapport que le dashboard affiche en permanence.

**Ce que la mesure dit aujourd'hui** (49 valeurs, 10 ans, horizon 10 j) :
IC −0,0237, écart vs achat-conservation −0,01 pt. **Le score n'a aucun
pouvoir prédictif décelable sur cet univers.** Il décrit l'état technique
d'une valeur ; il n'annonce pas la suite.

Sur les 9 valeurs tech d'origine il obtenait +0,0094 sur 5 ans — des titres
volatils et favorables aux signaux techniques. Sur des mégacaps
diversifiées, cet avantage disparaît. Le classement par momentum 12-1
ci-dessous fait mieux, mais bien moins que ce qui était annoncé — lis les
réserves, elles sont importantes.

## Le PEAD : testé, écarté

```bash
python3 pead_test.py
```

Après une bonne surprise de résultats, un titre est censé continuer de dériver
à la hausse pendant plusieurs semaines — anomalie documentée depuis 1968. Testé
avec le même protocole que le momentum (49 valeurs, 1 503 séances, 2020-2026,
signal actif de J+1 à J+60) :

| | IC transversal | Percentile vs null | Sharpe |
|---|---|---|---|
| PEAD top 12 | **−0,0048** | 80ᵉ — indistinct | 1,044 |
| PEAD top 5 | — | 51ᵉ — indistinct | 0,693 |
| Momentum 12-1 top 12 *(même période)* | +0,0183 | **99ᵉ** | 0,988 |
| Panier équipondéré | — | — | **1,119** |

**Résultat négatif, et il n'est pas surprenant.** L'anomalie est exploitée par
les fonds quantitatifs depuis des décennies ; elle survit surtout sur les
petites capitalisations peu suivies. Sur les 49 plus grosses sociétés
américaines, l'information est intégrée en séance. S'y ajoute une rotation de
39-41 % contre 14-19 % pour le momentum : des frais que rien ne compense.

Non intégré au dashboard. Le test est conservé pour ne pas avoir à le refaire.

## Le classement relatif

```bash
python3 stock_eval.py --ranking --period 5y --horizon 10
```

Compare les valeurs **entre elles** chaque séance au lieu de juger chacune
contre son propre passé, puis simule un portefeuille des mieux classées,
rééquilibré tous les N jours, contre le panier équipondéré des neuf.

Deux garde-fous méthodologiques y sont intégrés :

- **Entrée décalée d'une séance.** Le score se calcule sur une clôture, on ne
  peut donc pas acheter à cette même clôture.
- **Confrontation au hasard.** 300 portefeuilles tirés au sort donnent la
  distribution de ce que la chance produit sur ce panier. Sans ça, une
  performance flatteuse ne prouve rien : sur un univers où une valeur a fait
  ×10, toute sélection concentrée a de bonnes chances de battre la moyenne.

Trois critères de classement sont mis en concurrence, **sur les mêmes dates**
(chacun a besoin d'un historique différent avant de produire sa première
valeur — 50 séances pour le moteur, 273 pour le momentum ; sans alignement la
comparaison ne veut rien dire, la référence elle-même changeant d'un tableau
à l'autre) :

| Critère | IC transversal | Percentiles vs hasard | Verdict |
|---|---|---|---|
| Score calibré | −0,0002 | 18ᵉ, 26ᵉ, 64ᵉ | sous le hasard |
| Note brute du moteur | +0,0046 | 83ᵉ, 84ᵉ, 72ᵉ | indistinct |
| **Momentum 12-1** | **+0,0227** | **100ᵉ, 100ᵉ, 100ᵉ** | **au-dessus** |

*(univers élargi, 9 ans — 2017-2026, Covid et krach 2022 inclus)*

**Le momentum 12-1 est le moins mauvais critère — pas un critère validé.**
Un audit méthodologique (2026-09-07) a démonté trois des quatre affirmations
qui figuraient ici :

Les mesures ont été **corrigées le 2026-09-07** après audit. Ce qui change :

- *Sharpe* — il se lit désormais sur une courbe marquée **chaque séance**, et non
  tous les 10 jours. Résultat : **0,93 pour la stratégie contre 0,935 pour le
  panier** — elle est très légèrement *en dessous*. L'écart est accompagné de son
  intervalle de confiance (bootstrap apparié par blocs) : **IC95 [−0,38 ; +0,30],
  P(écart > 0) = 47 %**. Autrement dit, un pile ou face. Le « 1,23 contre 1,22 »
  publié auparavant était un artefact d'échantillonnage. Ces Sharpe sont **bruts** :
  aucun taux sans risque n'est déduit.
- *Drawdown* — même correction, même cause : **−29,4 % contre −32,6 %** en
  quotidien, au lieu des −19,7 % annoncés. Les creux qui se formaient et se
  refermaient entre deux rééquilibrages étaient tout simplement invisibles.
  L'ancien chiffre reste dans le rapport sous `drawdown_reequilibrage`, comme
  minorant assumé.
- *Le témoin* — l'ancien tirage au sort renouvelait **tout** le portefeuille à
  chaque rééquilibrage : sa rotation valait 100 % contre 15 % pour la stratégie,
  et il payait donc cinq à six fois plus de frais que ce qu'il servait à juger.
  Il est remplacé par un **null par permutation des étiquettes** : les séries de
  score sont redistribuées entre les valeurs, ce qui conserve la persistance du
  signal — donc la rotation, donc les frais — et ne casse que le lien entre le
  signal et la valeur qu'il désigne. On percentile désormais l'**excès sur le
  panier**, si bien que le 50ᵉ percentile signifie enfin « aucune compétence ».
  Le momentum 12-1 **reste au 100ᵉ percentile** après cette correction ; le score
  du moteur, lui, tombe **sous le hasard** en top 12.
- *« +783 % contre +424 % »* — le seul plan qui résiste, mais non significatif au
  seuil usuel : excès +5,8 pts/an, IC95 [−2,2 ; +14,3], P(>0) = 92 %. La stratégie
  ne bat le panier que sur 56 % des périodes.

**Face à ce qu'on peut vraiment acheter** (mesuré le 2026-09-07, 2 262 séances) :

| Référence | Perf. | Annualisé | Sharpe | Drawdown |
|---|---|---|---|---|
| Momentum 12-1 top 12 | +783 % | +27,5 % | **0,932** | −29,4 % |
| Panier maison (49, équipondéré) | +424 % | +20,3 % | **0,935** | −32,6 % |
| SPY — S&P 500 | +259 % | +15,3 % | **0,759** | −33,7 % |
| RSP — S&P 500 équipondéré | +175 % | +11,9 % | **0,590** | −39,0 % |

Ce tableau se lit en deux temps, et le second compte plus que le premier.

**Oui, la stratégie écrase les ETF** — +27,5 % par an contre +15,3 % pour SPY,
avec un meilleur Sharpe et un moindre drawdown. Pris seul, c'est spectaculaire.

**Mais le panier maison les écrase aussi**, avec un Sharpe de 0,935 — soit
exactement celui de la stratégie (0,932). Autrement dit : **tout l'avantage
risque-ajusté vient du choix du panier, pas du momentum.** Le classement ajoute
de la performance brute (+27,5 % contre +20,3 %) en prenant proportionnellement
plus de volatilité ; c'est du levier implicite, pas de la sélection.

Et ce panier, tu ne pouvais pas le constituer en 2016 — il est fait des 49
mégacaps telles qu'on les connaît en 2026. L'écart de Sharpe entre le panier
maison (0,935) et SPY (0,759) est la mesure directe de ce que vaut le fait de
connaître la réponse d'avance.

**Conclusion pratique :** un investisseur partant aujourd'hui devrait choisir
ses 49 valeurs sans savoir lesquelles survivront. Rien dans ces mesures ne dit
que le momentum 12-1 battrait alors un simple ETF.

**Et le panier lui-même est biaisé.** `stock_engine.UNIVERSE` est une liste de
49 mégacaps écrite en 2026, rejouée depuis 2016 : les 49 fichiers de cache
contiennent tous exactement 2 515 lignes — zéro faillite, zéro radiation en dix
ans, ce qui est impossible pour un panier réellement constitué en 2016. Le
momentum a un mode d'échec précis — acheter une valeur en tendance qui
s'effondre puis disparaît — et cet univers l'a supprimé par construction.
Rejoué sur un univers daté de 2016, à concentration égale, **l'avantage est
divisé par six** (+57 pts au lieu de +354). Le test des 300 portefeuilles au
hasard ne peut pas détecter ce biais : il tire dans le même vivier de
survivants.

Ce que le projet a réellement montré, en une phrase : *à l'intérieur d'un
panier de 49 mégacaps qui ont toutes survécu jusqu'en 2026, classer par
momentum 12-1 et détenir les 12 premières aurait rapporté environ 5 à 6 points
par an de plus que tout détenir, avec une incertitude qui touche zéro et aucun
avantage mesurable en risque.* Le moteur v2, avec ses sept composantes, est battu par cette seule
ligne de code. C'était le test : il ne le passe pas.

**Profondeur du momentum.** Les trois versions ont été départagées sur les
mêmes données (10 ans, 49 valeurs) plutôt que de retenir 12 mois par
tradition :

| Horizon | IC | Séances positives | Percentiles | Rotation |
|---|---|---|---|---|
| **12-1** | **+0,0227** | **56 %** | **100ᵉ, 100ᵉ, 100ᵉ** | **15-18 %** |
| 6-1 | +0,0148 | 54 % | 100ᵉ, 95ᵉ, 100ᵉ | 22-31 % |
| 3-1 | +0,0030 | 51 % | 66ᵉ, 88ᵉ, 88ᵉ | 36-47 % |

Le 12-1 l'emporte sur tous les plans, et la décroissance est monotone — ce
qui plaide pour un effet réel plutôt que du bruit. Le 3-1 est indistinct du
hasard : un momentum court capte surtout le retournement à court terme,
celui-là même que le décalage d'un mois sert à écarter.

**Réserve importante.** Cette démonstration vaut sur un univers large et
diversifié. Sur les neuf valeurs suivies par le bot, presque toutes du même
secteur, aucun critère ne bat le panier en risque-ajusté : il n'y a
quasiment rien à départager entre des titres qui montent et descendent
ensemble.

Le dashboard affiche donc le momentum 12-1 et le rang qui en découle, comme
information de départage entre valeurs — jamais comme un signal d'achat.

## Lancer les bots

**Démarrage automatique.** Le bot se lance à l'ouverture de session via
`~/Library/LaunchAgents/com.mathis.stockbot.plist`. Pour le désactiver :

```bash
launchctl unload ~/Library/LaunchAgents/com.mathis.stockbot.plist
```

Le service n'a délibérément pas de `KeepAlive` : sinon launchd relancerait
le bot juste après chaque `manage_bot.sh stop`, et il paraîtrait impossible
à éteindre. Le bot écrit lui-même son PID, donc `manage_bot.sh status` dit
la vérité qu'il ait été lancé à la main ou par le service — et un second
lancement est refusé plutôt que de faire tourner deux bots en parallèle.

**Notifications — fondées sur le momentum, pas sur le score technique.**
Bulles macOS natives via `osascript` : aucune dépendance, rien ne sort de la
machine. Deux événements seulement déclenchent une alerte :

- **Recomposition du top 12** (`STOCK_TOP_N`), au rythme de
  `STOCK_REBALANCE_DAYS` (14 jours calendaires ≈ 10 séances). Le message
  nomme les entrantes, les sortantes, et signale celles que tu détiens.
- **Décrochage d'une valeur détenue** au-delà du rang
  `STOCK_HELD_ALERT_RANK` (25ᵉ) — envoyée immédiatement, sans attendre le
  rééquilibrage : c'est de l'argent réellement engagé. Une même valeur n'est
  signalée qu'une fois, et l'alerte se réarme si elle remonte.

Pourquoi ce rythme plutôt qu'une alerte à chaque changement du classement ?
Parce que la stratégie validée rééquilibre tous les dix jours avec 15 % de
rotation. Réagir à chaque mouvement produirait beaucoup plus de
transactions — donc plus de frais, et un comportement qui n'est plus celui
qui a été mesuré. Au premier lancement, la composition est enregistrée en
silence : c'est un point de départ, pas un mouvement.

**Le score technique ne notifie plus.** Il reste calculé et historisé — il
décrit l'état d'une valeur et alimente le graphique — mais sonner pour un
signal dont on a mesuré qu'il n'annonce rien n'apprend qu'à ignorer ses
propres alertes. L'état vit dans `data/rebalance_state.json`.

Le bot **n'empêche pas le Mac de dormir** (plus de `caffeinate`) : un Mac
éveillé écran éteint consomme ~5-10 W contre ~0,3 W en veille, soit près
d'un cycle de batterie par nuit pour rien. Quand le Mac dort, le processus
est suspendu et repart au réveil — les scans manqués laissent juste des
trous dans l'historique, sans conséquence pour un bot qui ne passe aucun
ordre.

```bash
./manage_bot.sh start      # bot de signaux actions (start/stop/status/logs/history/restart)
python3 bot.py             # bot crypto — respecte DRY_RUN dans .env
python3 grid_bot.py        # grid bot — ajouter --live pour sortir de la simulation
```

## Les trois bots

| Fichier | Rôle |
|---|---|
| `stock_bot.py` | Signaux sur 9 actions (yfinance), moteur v2. N'exécute rien : le trading auto d'actions n'est pas ouvert aux particuliers en France, tu passes les ordres chez ton courtier. |
| `bot.py` | Bot crypto Binance (ccxt). Stratégie SMA + RSI + MACD + Bollinger, score de confiance sur 5. |
| `grid_bot.py` | Grid trading. Config retenue après 68 268 backtests : DOGE/USDT, 4h, 3 niveaux, 0,8 % d'espacement. |

Modules partagés : `exchange.py`, `strategy.py`, `risk_manager.py`,
`portfolio.py`, `notifier.py`, `logger.py`, `config.py`.
Backtesting : `backtester.py`, `ml_backtester.py`, `ultra_test.py`,
`mega_test.py`, `compare_strategies.py`.

## Garde-fous

Tout est dans `.env`, et le dashboard les affiche en permanence en haut de page :

- `DRY_RUN=true` → aucun ordre réel, même avec des clés valides. **C'est le seul
  réglage qui te sépare de l'argent réel** puisque `TESTNET=false`.
- `STOP_LOSS_PCT`, `TAKE_PROFIT_PCT`, `MAX_DRAWDOWN_PCT`, `MAX_TRADES_PER_DAY`.

`.env` contient une vraie clé API Binance, inutilisée depuis mai 2026 — si tu
reprends, révoque-la et régénères-en une.
