"""Déclinaison d'un visuel en 101 versions départementales (texte incrusté, charte LFH).

Un jeu de déclinaisons = un visuel de base + un texte (avec {departement}, {code}, {region})
+ un style. Les fichiers sont écrits sous static/media/variants/set_<id>/<slug>-<code>.jpg
et zippés. Dans un post, l'adresse « variant:<id> » est remplacée, au moment de l'envoi,
par l'image du département visé.
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .departments import DEPARTMENTS, render_caption

BASE = Path(__file__).parent.parent
FONTS = BASE / "static" / "fonts"
VARIANTS_DIR = BASE / "static" / "media" / "variants"
MAX_DIM = 1440

COULEURS = {"bleu": (40, 40, 234), "noir": (0, 0, 0), "blanc": (255, 255, 255), "rouge": (232, 63, 19),
            "vert": (28, 119, 104), "rose": (247, 183, 196)}
STYLES = {
    # bandeau plein + texte contrasté (lisible sur n'importe quel fond)
    "bandeau_bleu": {"fond": "bleu", "texte": "blanc"},
    "bandeau_noir": {"fond": "noir", "texte": "blanc"},
    "bandeau_blanc": {"fond": "blanc", "texte": "noir"},
    # texte seul, avec une ombre légère
    "texte_blanc": {"fond": None, "texte": "blanc"},
    "texte_noir": {"fond": None, "texte": "noir"},
    "texte_bleu": {"fond": None, "texte": "bleu"},
    "texte_vert": {"fond": None, "texte": "vert"},   # le vert des visuels « Objectif 2027 »
    "bandeau_vert": {"fond": "vert", "texte": "noir"},
    "texte_rose": {"fond": None, "texte": "rose"},   # rose de la charte, sur fond vert
}
POSITIONS = ("bas", "haut", "centre")


def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    name = "Poppins-ExtraBold.ttf" if bold else "Poppins-SemiBold.ttf"
    try:
        return ImageFont.truetype(str(FONTS / name), size)
    except OSError:
        return ImageFont.load_default(size=size)


TRACKING = -0.025   # interlettrage de la charte (-25 pour mille)


def _mesure(font: ImageFont.FreeTypeFont, texte: str) -> tuple[int, int, int]:
    """Largeur totale avec interlettrage, hauteur, décalage haut (bbox)."""
    tr = TRACKING * font.size
    largeur = sum(font.getlength(ch) for ch in texte) + tr * max(0, len(texte) - 1)
    bbox = font.getbbox(texte)
    return int(largeur), bbox[3] - bbox[1], bbox[1]


def _dessiner(draw: ImageDraw.ImageDraw, x: float, y: float, texte: str, font: ImageFont.FreeTypeFont, fill) -> None:
    tr = TRACKING * font.size
    for ch in texte:
        draw.text((x, y), ch, font=font, fill=fill)
        x += font.getlength(ch) + tr


def _slug(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "-", s.lower()).strip("-")
    return s[:40] or "visuel"


def charger_base(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > MAX_DIM:
        r = MAX_DIM / max(w, h)
        img = img.resize((int(w * r), int(h * r)), Image.LANCZOS)
    return img


def incruster(base: Image.Image, texte: str, position: str = "bas", style: str = "bandeau_bleu",
              taille: float = 1.0, minuscules: bool = True, align: str = "centre") -> Image.Image:
    """Renvoie une copie de l'image avec le texte incrusté (texte déjà rendu, sans variables)."""
    img = base.copy()
    w, h = img.size
    if minuscules:
        texte = texte.lower()
    st = STYLES.get(style, STYLES["bandeau_bleu"])
    fsize = int(w / 12 * taille)
    marge = int(w * 0.05)
    font = _font(max(18, fsize))
    draw = ImageDraw.Draw(img)
    # réduction automatique si le texte dépasse la largeur utile
    while True:
        tw, th, top = _mesure(font, texte)
        if tw <= w - 2 * marge or font.size <= 18:
            break
        font = _font(font.size - 2)
    pad_v = int(th * 0.55)
    band_h = th + 2 * pad_v
    marge_v = 0 if st["fond"] else int(h * 0.035)
    if position == "haut":
        y0 = marge_v
    elif position == "centre":
        y0 = (h - band_h) // 2
    else:
        y0 = h - band_h - marge_v
    marge_h = int(w * 0.09)   # marge des maquettes (alignée sur les textes en bord)
    if align == "gauche":
        tx = marge_h
    elif align == "droite":
        tx = w - marge_h - tw
    else:
        tx = (w - tw) // 2
    ty = y0 + pad_v - top
    if st["fond"]:
        draw.rectangle([0, y0, w, y0 + band_h], fill=COULEURS[st["fond"]])
    else:
        # ombre douce pour la lisibilité du texte seul
        if st["texte"] in ("blanc", "bleu"):
            ombre = (0, 0, 0)
            for dx, dy in ((2, 2), (-2, 2), (2, -2), (-2, -2), (0, 3)):
                _dessiner(draw, tx + dx, ty + dy, texte, font, ombre)
    _dessiner(draw, tx, ty, texte, font, COULEURS[st["texte"]])
    return img


def rendre_texte(template: str, code: str) -> str:
    return render_caption(template, code, None).replace("@", "").strip()


def apercu(data: bytes, template: str, code: str = "75", **opts) -> bytes:
    img = incruster(charger_base(data), rendre_texte(template, code), **opts)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=88)
    return out.getvalue()


def generer(set_id: int, data: bytes, template: str, slug: str, **opts) -> list[dict]:
    """Écrit les 101 images + un zip ; renvoie [{code, name, path (relatif static), size}]."""
    base = charger_base(data)
    dossier = VARIANTS_DIR / f"set_{set_id}"
    dossier.mkdir(parents=True, exist_ok=True)
    slug = _slug(slug)
    out = []
    for code, _name in DEPARTMENTS:
        img = incruster(base, rendre_texte(template, code), **opts)
        fname = f"{slug}-{code}.jpg"
        img.save(dossier / fname, "JPEG", quality=88, optimize=True)
        out.append({"code": code, "name": fname, "path": f"media/variants/set_{set_id}/{fname}",
                    "size": (dossier / fname).stat().st_size})
    with zipfile.ZipFile(dossier / f"{slug}-101.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for v in out:
            z.write(dossier / v["name"], v["name"])
    return out


def zip_path(set_id: int, slug: str) -> str:
    return f"media/variants/set_{set_id}/{_slug(slug)}-101.zip"
