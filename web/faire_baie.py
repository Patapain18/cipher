"""
Fabrique le bandeau de baie vitrée de la page : LA MÊME VILLE que la
vue 3D, photographiée par la même caméra.

    python3 web/faire_baie.py

POURQUOI PROJETER LA VILLE 3D
    La première version tirait une silhouette 2D au hasard : deux villes
    différentes, l'une dans le bandeau, l'autre derrière la baie — et
    rien ne disait « c'est la baie devant laquelle j'étais assis ». Ici
    le générateur de vue3d_ville.js est exécuté sous node, et chaque
    boîte est projetée avec l'œil, la cible et les montants de vue3d.js.
    Le bandeau est la vue depuis la chaise ; la vue baie, debout. La
    cohérence n'est plus une discipline, c'est une conséquence.

POURQUOI UNE FOCALE PLUS COURTE
    Le bandeau fait 210 px de haut ; à la focale de la vue (911 px) la
    ville en occuperait 360. On garde le même œil et la même visée, avec
    un objectif plus large — comme un appareil photo qui change de
    focale sans bouger de place. Les montants tombent aux x projetés,
    tous les 1,5 m comme en 3D.

POURQUOI TROIS GRIS ET DES SILHOUETTES
    Le bandeau reste une image vectorielle sans script et sans coût :
    des faces pleines, classées par distance sur les trois gris du
    bandeau d'origine (perspective aérienne), toits un peu plus clairs.
    Pas de fenêtres — à cette taille ce serait une trame.

Le script écrit DIRECTEMENT dans web/static/index.html, entre deux
balises repères, de façon idempotente.
"""

import json
import math
import os
import subprocess

L = 1440          # largeur du viewBox
H = 210           # hauteur du bandeau
CX, CY = 720, 104 # le centre de l'image et la ligne d'horizon
FOCALE = 531      # focale du bandeau, en px (la vue : 911)

# La caméra de vue3d.js — les mêmes chiffres, recopiés ici parce que ce
# script tourne sans navigateur. Si l'un bouge, l'autre doit bouger.
OEIL = (0.0, 0.0, -2.4)
CIBLE = (0.0, -0.05, -60.0)
VITRAGE_Z = -7.4
MODULE = 1.5
ALLEGE = -0.95


def base_camera():
    f = [c - o for c, o in zip(CIBLE, OEIL)]
    n = math.sqrt(sum(v * v for v in f)); f = [v / n for v in f]
    up = (0.0, 1.0, 0.0)
    s = [f[1] * up[2] - f[2] * up[1], f[2] * up[0] - f[0] * up[2], f[0] * up[1] - f[1] * up[0]]
    n = math.sqrt(sum(v * v for v in s)); s = [v / n for v in s]
    u = [s[1] * f[2] - s[2] * f[1], s[2] * f[0] - s[0] * f[2], s[0] * f[1] - s[1] * f[0]]
    return f, s, u


F, S, U = base_camera()


def projeter(p):
    d = [p[i] - OEIL[i] for i in range(3)]
    xv = sum(d[i] * S[i] for i in range(3))
    yv = sum(d[i] * U[i] for i in range(3))
    zv = sum(d[i] * F[i] for i in range(3))
    if zv < 0.5:
        return None
    return (CX + FOCALE * xv / zv, CY - FOCALE * yv / zv)


def faces_visibles(b):
    """Les faces d'une boîte tournées vers l'œil : le toit et deux côtés."""
    x, y, z, hx, hy, hz, c, s, genre = b
    def coin(lx, ly, lz):
        return (x + lx * c - lz * s, y + ly, z + lx * s + lz * c)
    faces = []
    # Le toit, si l'œil est au-dessus.
    if OEIL[1] > y + hy:
        faces.append(("toit", [coin(-hx, hy, -hz), coin(hx, hy, -hz), coin(hx, hy, hz), coin(-hx, hy, hz)]))
    # Les quatre faces verticales : normale locale (nx, nz) tournée.
    for nx, nz, pts in (
        (1, 0, [coin(hx, -hy, -hz), coin(hx, -hy, hz), coin(hx, hy, hz), coin(hx, hy, -hz)]),
        (-1, 0, [coin(-hx, -hy, hz), coin(-hx, -hy, -hz), coin(-hx, hy, -hz), coin(-hx, hy, hz)]),
        (0, 1, [coin(hx, -hy, hz), coin(-hx, -hy, hz), coin(-hx, hy, hz), coin(hx, hy, hz)]),
        (0, -1, [coin(-hx, -hy, -hz), coin(hx, -hy, -hz), coin(hx, hy, -hz), coin(-hx, hy, -hz)]),
    ):
        n = (nx * c - nz * s, nx * s + nz * c)               # normale monde (x, z)
        centre = coin(nx * hx, 0, nz * hz)
        vers = (OEIL[0] - centre[0], OEIL[2] - centre[2])
        if n[0] * vers[0] + n[1] * vers[1] > 0:
            faces.append(("face", pts))
    return faces


def chemin(pts):
    return "M" + " L".join(f"{p[0]:.0f},{p[1]:.0f}" for p in pts) + "Z"


BANDES = [(0, 170, "pres"), (170, 280, "pres"), (280, 450, "milieu"),
          (450, 700, "loin"), (700, 1000, "loin")]


def main():
    ici = os.path.dirname(os.path.abspath(__file__))
    ville = json.loads(subprocess.run(
        ["node", os.path.join(ici, "outils", "exporter_ville.js")],
        capture_output=True, text=True, check=True).stdout)
    boites = ville["boites"]
    sol = CY - FOCALE * ALLEGE / (VITRAGE_Z - OEIL[2]) * -1  # y de l'allège
    SOL = round(CY + FOCALE * (-ALLEGE) / (OEIL[2] - VITRAGE_Z))

    # Du plus loin au plus près (le peintre), par bandes de distance.
    groupes = {}
    n_faces = 0
    for b in sorted(boites, key=lambda b: b[2]):          # z croissant = loin d'abord
        d = -b[2]
        if d > 1000:
            continue
        bande = next((i for i, (a, z2, _) in enumerate(BANDES) if a <= d < z2), None)
        if bande is None:
            continue
        for genre_face, pts in faces_visibles(b):
            proj = [projeter(p) for p in pts]
            if any(p is None for p in proj):
                continue
            xs = [p[0] for p in proj]; ys = [p[1] for p in proj]
            if max(xs) < -10 or min(xs) > L + 10 or max(ys) < 0 or min(ys) > SOL:
                continue
            if (max(xs) - min(xs)) * (max(ys) - min(ys)) < 4:
                continue                                     # sous-pixel : rien à peindre
            groupes.setdefault((bande, genre_face), []).append(chemin(proj))
            n_faces += 1

    silhouettes = []
    for bande, (_, _, classe) in enumerate(BANDES):
        for genre_face in ("face", "toit"):
            ch = groupes.get((bande, genre_face))
            if not ch:
                continue
            extra = ' baie-toit' if genre_face == "toit" else ''
            silhouettes.append(f'<path class="baie-{classe}{extra}" d="{"".join(ch)}"/>')
    silhouettes = "\n    ".join(silhouettes)

    # Les montants du mur-rideau, aux x projetés : tous les 1,5 m, comme
    # en 3D. Sans eux on voit une ville ; avec eux on est DEDANS.
    montants = []
    for i in range(-6, 7):
        x = CX + FOCALE * (i * MODULE) / (OEIL[2] - VITRAGE_Z)
        if -3 <= x <= L + 3:
            montants.append(f'<rect class="baie-montant" x="{x - 1.5:.1f}" y="0" width="3" height="{SOL}"/>')
    montants = "".join(montants)

    fragment = f"""<!-- BANDEAU:DEBUT — ne pas éditer à la main, voir web/faire_baie.py
     ══════════════════════════════════════════════════════
     BANDEAU — la baie vitrée du onzième étage, vue de la chaise
     ══════════════════════════════════════════════════════
     Généré par web/faire_baie.py : c'est LA MÊME ville que la vue baie
     (web/static/vue3d_ville.js), projetée par la même caméra avec un
     objectif plus large. Aucun script, aucune animation, aucun coût :
     une image vectorielle posée dans le flux du document.

     DANS LE FLUX, et non en `position: fixed` : un décor fixé derrière
     la colonne de lecture serait masqué à 97 % ET coûterait sa hauteur
     à chaque écran de lecture, tous les matins. Là, il se voit en
     entier à l'ouverture, puis il défile hors champ dès qu'on lit.

     PLEIN JOUR, et non la nuit : le fond sombre à accent lumineux est
     l'esthétique dont ce projet et le portfolio cherchaient à sortir.
     La ville est ici une silhouette dans la brume, à la couleur du
     papier — elle finit par se confondre avec la page, et il n'y a
     donc aucune couture entre le bandeau et le tableau qui suit.
-->
<div class="baie" aria-hidden="true">
  <svg viewBox="0 0 {L} {H}" preserveAspectRatio="xMidYMax slice"
       role="presentation" focusable="false">
    <defs>
      <linearGradient id="baie-ciel" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" class="baie-ciel-haut"/>
        <stop offset="1" class="baie-ciel-bas"/>
      </linearGradient>
      <linearGradient id="baie-fondu" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" class="baie-fondu-haut"/>
        <stop offset="1" class="baie-fondu-bas"/>
      </linearGradient>
      <linearGradient id="baie-brume-g" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" class="baie-brume-haut"/>
        <stop offset="1" class="baie-brume-bas"/>
      </linearGradient>
      <linearGradient id="baie-sol-g" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" class="baie-sol-haut"/>
        <stop offset="1" class="baie-sol-bas"/>
      </linearGradient>
      <linearGradient id="baie-reflet" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" class="baie-reflet-bord"/>
        <stop offset="0.45" class="baie-reflet-coeur"/>
        <stop offset="1" class="baie-reflet-bord"/>
      </linearGradient>
    </defs>

    <rect width="{L}" height="{SOL}" fill="url(#baie-ciel)"/>
    <!-- Le sol de la ville, de l'horizon à l'allège : sans lui, une rue
         vue dans l'axe laisse passer le ciel en triangle blanc. Il se
         fond dans la brume vers l'horizon. -->
    <rect x="0" y="{CY}" width="{L}" height="{SOL - CY}" fill="url(#baie-sol-g)"/>
    {silhouettes}

    <!-- Le reflet oblique sur le verre : deux traits suffisent à dire
         qu'il y a une vitre entre la ville et nous. -->
    <rect class="baie-vitre" width="{L}" height="{SOL}" fill="url(#baie-reflet)"/>

    <!-- La brume au pied de la ville : sans elle les immeubles se
         posent à plat sur l'allège, comme des découpes de papier. -->
    <rect x="0" y="{SOL - 44}" width="{L}" height="44" fill="url(#baie-brume-g)"/>

    {montants}
    <!-- La traverse haute et l'allège : elles ferment le cadre et
         donnent l'échelle de l'étage. -->
    <rect class="baie-cadre" x="0" y="0" width="{L}" height="6"/>
    <rect class="baie-cadre" x="0" y="{SOL}" width="{L}" height="7"/>

    <!-- Le sol de la pièce, qui rejoint la couleur de la page. -->
    <rect x="0" y="{SOL + 7}" width="{L}" height="{H - SOL - 7}"
          fill="url(#baie-fondu)"/>
  </svg>
</div>
<!-- BANDEAU:FIN -->
"""

    fragment = "\n".join(("  " + l if l.strip() else l)
                         for l in fragment.rstrip("\n").split("\n"))

    page = os.path.join(ici, "static", "index.html")
    with open(page) as f:
        html = f.read()

    DEBUT, FIN = "<!-- BANDEAU:DEBUT", "<!-- BANDEAU:FIN -->"
    if DEBUT in html:
        i = html.index("  " + DEBUT)
        j = html.index(FIN) + len(FIN)
        html = html[:i] + fragment + html[j:]
    else:
        ancre = '<div class="shell">\n'
        html = html.replace(ancre, ancre + "\n" + fragment + "\n", 1)

    with open(page, "w") as f:
        f.write(html)

    print(f"▸ {page}  (bandeau de {len(fragment) // 1024} Ko, {n_faces} faces, "
          f"allège à y={SOL}, {len(montants.split('<rect')) - 1} montants)")


if __name__ == "__main__":
    main()
