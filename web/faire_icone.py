"""
Fabrique dashboard.icns à partir de rien — aucun fichier image à
versionner, l'icône se régénère.

Le dessin reprend exactement la favicon de la page (index.html) : fond
crème, ligne indigo qui monte. Une application et son onglet qui portent
deux marques différentes, c'est deux produits dans la tête de celui qui
regarde le Dock.

    python3 web/faire_icone.py
"""

import os
import subprocess
import tempfile

from PIL import Image, ImageDraw

CREME = (244, 241, 234)
INDIGO = (36, 71, 110)
NOIR = (20, 18, 15)

# macOS attend une marge autour du dessin : une icône qui touche les
# bords paraît plus grosse que ses voisines et casse l'alignement du Dock.
MARGE = 0.10


def dessiner(taille):
    img = Image.new("RGBA", (taille, taille), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    m = taille * MARGE
    boite = [m, m, taille - m, taille - m]
    d.rounded_rectangle(boite, radius=taille * 0.20, fill=CREME)

    # La courbe, en coordonnées relatives à la zone crème : le dessin
    # reste identique à toutes les tailles, du 16 px de la barre de menus
    # au 1024 px du Finder.
    cote = taille - 2 * m
    pts = [(0.16, 0.72), (0.40, 0.44), (0.57, 0.58), (0.84, 0.28)]
    ligne = [(m + x * cote, m + y * cote) for x, y in pts]
    d.line(ligne, fill=INDIGO, width=max(2, int(taille * 0.058)),
           joint="curve")

    # Les extrémités arrondies : PIL ne les fait pas sur une polyligne,
    # on pose un disque à chaque sommet.
    r = max(1, int(taille * 0.029))
    for x, y in ligne:
        d.ellipse([x - r, y - r, x + r, y + r], fill=INDIGO)

    # Le point d'arrivée en noir chaud : c'est le signal du jour, la
    # seule chose que l'icône a besoin de dire.
    r2 = max(2, int(taille * 0.052))
    x, y = ligne[-1]
    d.ellipse([x - r2, y - r2, x + r2, y + r2], fill=NOIR)
    return img


def main():
    ici = os.path.dirname(os.path.abspath(__file__))
    sortie = os.path.join(ici, "dashboard.icns")

    with tempfile.TemporaryDirectory() as tmp:
        jeu = os.path.join(tmp, "icon.iconset")
        os.makedirs(jeu)
        # Les tailles exigées par iconutil. @2x n'est pas un doublon :
        # c'est la version écran Retina de la taille juste au-dessus.
        for base in (16, 32, 128, 256, 512):
            dessiner(base).save(os.path.join(jeu, f"icon_{base}x{base}.png"))
            dessiner(base * 2).save(
                os.path.join(jeu, f"icon_{base}x{base}@2x.png"))
        subprocess.run(["iconutil", "-c", "icns", jeu, "-o", sortie],
                       check=True)
    print(f"▸ {sortie}")


if __name__ == "__main__":
    main()
