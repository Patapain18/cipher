"""
Lit une capture de la vue baie et en sort des chiffres — la routine
avant/après de chaque retouche.

    /usr/bin/python3 web/outils/profil.py colonnes IMG [x ...]
        les bandes de valeur (début, fin, luminance) le long de colonnes
        de pixels ; par défaut x = 200, 1440, 2680 (capture 2x)
    /usr/bin/python3 web/outils/profil.py zone IMG x0 y0 x1 y1
        histogramme (min, p10, médiane, p90, max, écart-type) d'une zone
    /usr/bin/python3 web/outils/profil.py diff A B [seuil]
        pourcentage de pixels qui diffèrent de plus de `seuil` niveaux,
        et le rectangle qui les contient
    /usr/bin/python3 web/outils/profil.py pixel IMG x y

Les valeurs sont celles du fichier (Display P3 sur cet écran) : on
compare toujours des pixels entre eux, jamais un pixel à un hex de token.
"""
import sys
import numpy as np
from PIL import Image


def charger(p):
    return np.asarray(Image.open(p).convert("RGB")).astype(np.int32)


def luminance(a):
    return (0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2])


def colonnes(img, xs):
    a = charger(img)
    L = luminance(a)
    h = a.shape[0]
    for x in xs:
        col = L[:, x]
        print(f"— colonne x={x} ({h} px)")
        debut, somme, n = 0, 0.0, 0
        for y in range(1, h + 1):
            fin = y == h or abs(col[y] - col[y - 1]) > 4
            somme += col[y - 1]; n += 1
            if fin:
                if n >= 6:
                    print(f"   y {debut:4d} → {y - 1:4d}  ({n:4d} px)  L≈{somme / n:6.1f}")
                debut, somme, n = y, 0.0, 0


def zone(img, x0, y0, x1, y1):
    a = luminance(charger(img)[y0:y1, x0:x1]).ravel()
    print(f"zone {x0},{y0} → {x1},{y1} : {a.size} px")
    print(f"   min {a.min():.0f}  p10 {np.percentile(a, 10):.0f}  médiane {np.median(a):.0f}"
          f"  p90 {np.percentile(a, 90):.0f}  max {a.max():.0f}  écart-type {a.std():.1f}")


def diff(pa, pb, seuil=3):
    a, b = charger(pa), charger(pb)
    if a.shape != b.shape:
        print("tailles différentes :", a.shape, b.shape); return
    d = np.abs(a - b).max(axis=2) > seuil
    pct = 100.0 * d.mean()
    print(f"pixels différents (> {seuil} niveaux) : {pct:.2f} %")
    if d.any():
        ys, xs = np.where(d)
        print(f"   rectangle : x {xs.min()} → {xs.max()}, y {ys.min()} → {ys.max()}")


def pixel(img, x, y):
    a = charger(img)
    print(f"({x},{y}) = {tuple(int(v) for v in a[y, x])}  L≈{luminance(a[y, x]):.1f}")


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "colonnes":
        colonnes(args[0], [int(v) for v in args[1:]] or [200, 1440, 2680])
    elif cmd == "zone":
        zone(args[0], *[int(v) for v in args[1:5]])
    elif cmd == "diff":
        diff(args[0], args[1], int(args[2]) if len(args) > 2 else 3)
    elif cmd == "pixel":
        pixel(args[0], int(args[1]), int(args[2]))
    else:
        print(__doc__)
