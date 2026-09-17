"""Moteur de conseils pour la planification : règles explicites, jamais d'action automatique.

Chaque règle produit des alertes de niveau « bloquant » (l'envoi échouera), « attention »
(risque réel) ou « conseil » (bonne pratique des algorithmes Instagram). L'équipe garde la main :
une suggestion est une proposition avec une action applicable, pas une décision.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta

# ---------- Connaissances (résumé des bonnes pratiques Instagram, sept. 2026) ----------

REGLES = [
    {"code": "fenetres", "titre": "Créneaux à forte audience",
     "texte": "En semaine, l'audience française est la plus disponible entre 7 h 30 et 9 h, entre 12 h et 13 h 30, "
              "et entre 18 h et 21 h. Le week-end, plutôt 10 h à 12 h et 19 h à 21 h. Publier dans ces fenêtres "
              "maximise les interactions de la première heure, qui pèsent lourd dans la diffusion."},
    {"code": "nuit", "titre": "Pas d'envoi de nuit",
     "texte": "Entre 23 h et 7 h, un post démarre sans interactions et l'algorithme le classe comme peu intéressant."},
    {"code": "espacement", "titre": "Espacer les publications d'un même compte",
     "texte": "Deux posts de fil à moins de deux heures d'écart se cannibalisent : le second coupe la diffusion du "
              "premier. Les stories peuvent être plus rapprochées, 30 minutes minimum."},
    {"code": "volume", "titre": "Volume par compte et par jour",
     "texte": "Un à deux posts de fil par jour et par compte suffisent. Au-delà, la portée par post baisse. "
              "Meta impose de toute façon 25 publications maximum par compte et par 24 h, stories comprises."},
    {"code": "rafale", "titre": "Éviter la rafale identique",
     "texte": "Cent comptes qui publient le même visuel à la même minute ressemblent à un réseau automatisé. "
              "Échelonner de quelques minutes et personnaliser la légende ({departement}, {compte}) rend la "
              "campagne organique et répartit les appels à l'API."},
    {"code": "formats", "titre": "Mélanger les formats",
     "texte": "Le carrousel obtient en moyenne plus de portée et d'enregistrements qu'une image seule. Les reels "
              "touchent des personnes qui ne suivent pas le compte. La story crée l'urgence et l'échange direct. "
              "Une campagne efficace combine les trois."},
    {"code": "legende", "titre": "Légende : accroche, appel à l'action, hashtags",
     "texte": "Seuls les 125 premiers caractères sont visibles avant « plus » : y placer le message clé. "
              "Un appel à l'action explicite (rejoignez, inscrivez-vous, partagez, lien en bio) augmente les "
              "interactions. Trois à cinq hashtags précis valent mieux que trente."},
    {"code": "visuels", "titre": "Visuels conformes",
     "texte": "URL publiques en https uniquement. Carrousel : 2 à 10 images. Story : format vertical 9:16. "
              "Une légende n'est pas affichée sur une story."},
    {"code": "rythme", "titre": "Rythme de campagne",
     "texte": "Une séquence tient mieux avec un teaser la veille, un temps fort, puis un rappel ou un bilan. "
              "Un trou de plus de trois jours fait retomber l'attention."},
    {"code": "doublons", "titre": "Pas de doublon sur un compte",
     "texte": "Le même visuel publié deux fois sur le même compte est signalé comme du contenu répétitif."},
]

FENETRES_SEMAINE = [((7, 30), (9, 0)), ((12, 0), (13, 30)), ((18, 0), (21, 0))]
FENETRES_WEEKEND = [((10, 0), (12, 0)), ((19, 0), (21, 0))]
CTA = re.compile(r"rejoignez|inscrivez|inscription|partagez|lien en bio|commentez|découvrez|decouvrez|"
                 r"votez|signez|participez|venez|rendez-vous|rdv|adhérez|adherez", re.I)
PLACEHOLDER = re.compile(r"\{([a-zà-ü_]+)\}", re.I)
PLACEHOLDERS_CONNUS = {"departement", "département", "code", "compte", "region", "région"}


def _fenetres(d: datetime):
    return FENETRES_WEEKEND if d.weekday() >= 5 else FENETRES_SEMAINE


def dans_fenetre(d: datetime) -> bool:
    t = (d.hour, d.minute)
    return any(a <= t <= b for a, b in _fenetres(d))


def prochaine_fenetre(d: datetime) -> datetime:
    """Prochain créneau conseillé à partir de d (même jour si possible, sinon lendemain matin)."""
    for _ in range(3):
        for (h1, m1), (h2, m2) in _fenetres(d):
            debut = d.replace(hour=h1, minute=m1, second=0, microsecond=0)
            fin = d.replace(hour=h2, minute=m2, second=0, microsecond=0)
            if d <= fin:
                return max(d, debut) if d <= fin else debut
        d = (d + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return d


def _fmt(d: datetime | None) -> str:
    return d.strftime("%d/%m %H:%M") if d else "immédiat"


def analyser(rows: list[dict], existants: list[dict] | None = None,
             maintenant: datetime | None = None) -> dict:
    """Analyse un plan.

    rows : envois à créer, chacun : {i, when (datetime Paris ou None), targets [codes], media_type,
           caption, media_url, asset_id (opt), stagger (opt bool)}
    existants : envois déjà programmés (même forme, avec 'id'), pour détecter les collisions.
    """
    now = maintenant or datetime.now()
    alertes: list[dict] = []
    existants = existants or []

    def add(niveau, code, message, lignes=None, suggestion="", action=None):
        alertes.append({"niveau": niveau, "code": code, "message": message,
                        "lignes": lignes or [], "suggestion": suggestion, "action": action})

    # occupation par compte : (code) -> liste de (when, media_type, origine)
    occupation: dict[str, list] = defaultdict(list)
    for e in existants:
        for c in e.get("targets", []):
            occupation[c].append((e.get("when"), e.get("media_type", "IMAGE"), f"existant #{e.get('id')}"))
    for r in rows:
        w = r.get("when") or now
        for c in r.get("targets", []):
            occupation[c].append((w, r.get("media_type", "IMAGE"), f"ligne {r['i']}"))

    n_lignes = len(rows)
    n_cibles = sum(len(r.get("targets", [])) for r in rows)
    formats = defaultdict(int)
    jours = set()
    par_asset_compte: dict[tuple, list] = defaultdict(list)

    for r in rows:
        i = r["i"]
        w = r.get("when")
        mt = r.get("media_type", "IMAGE")
        cap = r.get("caption") or ""
        urls = [u.strip() for u in (r.get("media_url") or "").replace("|", "\n").splitlines() if u.strip()]
        formats[mt] += 1
        if w:
            jours.add(w.date())
        # --- horaires
        if w and w < now:
            add("bloquant", "passe", f"ligne {i} : l'heure {_fmt(w)} est déjà passée.", [i],
                "Choisissez une heure future, ou videz l'heure pour un envoi immédiat.",
                {"type": "deplacer", "ligne": i, "quand": prochaine_fenetre(now + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M")})
        elif w and (w.hour >= 23 or w.hour < 7):
            pf = prochaine_fenetre(w)
            add("attention", "nuit", f"ligne {i} : envoi de nuit ({_fmt(w)}), démarrage sans interactions.", [i],
                f"Décaler au prochain créneau à forte audience : {_fmt(pf)}.",
                {"type": "deplacer", "ligne": i, "quand": pf.strftime("%Y-%m-%dT%H:%M")})
        elif w and mt != "STORIES" and not dans_fenetre(w):
            pf = prochaine_fenetre(w)
            if pf != w:
                add("conseil", "fenetres", f"ligne {i} : {_fmt(w)} est hors des créneaux à forte audience.", [i],
                    f"Créneau conseillé le plus proche : {_fmt(pf)}.",
                    {"type": "deplacer", "ligne": i, "quand": pf.strftime("%Y-%m-%dT%H:%M")})
        # --- rafale
        if len(r.get("targets", [])) >= 30 and not r.get("stagger"):
            add("conseil", "rafale",
                f"ligne {i} : {len(r['targets'])} comptes publient le même contenu à la même minute.", [i],
                "Échelonner par lots (ex. 10 comptes toutes les 2 minutes) et personnaliser la légende avec {departement}.",
                {"type": "echelonner", "ligne": i, "taille_lot": 10, "intervalle": 2})
        # --- légende
        if mt != "STORIES":
            if not cap.strip():
                add("attention", "legende", f"ligne {i} : légende vide sur un post de fil.", [i],
                    "Ajoutez au moins une phrase d'accroche et un appel à l'action.")
            else:
                if len(cap) > 2200:
                    add("bloquant", "legende", f"ligne {i} : légende trop longue ({len(cap)} caractères, maximum 2200).", [i])
                if "#" not in cap:
                    add("conseil", "legende", f"ligne {i} : aucun hashtag.", [i],
                        "Trois à cinq hashtags précis (#LaFranceHumaniste, #{departement}…).")
                elif cap.count("#") > 10:
                    add("conseil", "legende", f"ligne {i} : {cap.count('#')} hashtags, c'est beaucoup.", [i],
                        "Gardez les cinq plus précis.")
                if not CTA.search(cap):
                    add("conseil", "legende", f"ligne {i} : pas d'appel à l'action repéré.", [i],
                        "Une invitation claire (rejoignez, inscrivez-vous, partagez, lien en bio) augmente les interactions.")
                inconnus = {p for p in PLACEHOLDER.findall(cap) if p.lower() not in PLACEHOLDERS_CONNUS}
                if inconnus:
                    add("attention", "legende", f"ligne {i} : variable inconnue {{{', '.join(sorted(inconnus))}}}.", [i],
                        "Variables disponibles : {departement}, {code}, {compte}, {region}.")
        elif cap.strip():
            add("conseil", "visuels", f"ligne {i} : une story n'affiche pas de légende, le texte sera ignoré.", [i])
        # --- visuels
        bad = [u for u in urls if not (u.lower().startswith("https://") or u.startswith("variant:"))]
        if bad:
            add("bloquant", "visuels", f"ligne {i} : visuel non https ({bad[0][:60]}).", [i],
                "Meta n'accepte que des URL publiques en https.")
        if mt == "CAROUSEL" and not 2 <= len(urls) <= 10:
            add("bloquant", "visuels", f"ligne {i} : un carrousel demande 2 à 10 images ({len(urls)} fournies).", [i])
        # --- doublons asset/compte
        if r.get("asset_id"):
            for c in r.get("targets", []):
                par_asset_compte[(r["asset_id"], c)].append(i)

    # --- espacement et volume par compte
    conflits, volumes = 0, 0
    lignes_conflit: set[int] = set()
    for code, occ in occupation.items():
        occ_t = sorted([(w or now, mt, src) for w, mt, src in occ], key=lambda x: x[0])
        for (w1, m1, s1), (w2, m2, s2) in zip(occ_t, occ_t[1:]):
            mini = 30 if (m1 == "STORIES" and m2 == "STORIES") else 120
            if (w2 - w1) < timedelta(minutes=mini):
                conflits += 1
                for s in (s1, s2):
                    if s.startswith("ligne "):
                        lignes_conflit.add(int(s.split()[1]))
                if conflits <= 8:
                    add("attention", "espacement",
                        f"compte {code} : {s1} ({_fmt(w1)}) et {s2} ({_fmt(w2)}) à moins de {mini} min.",
                        sorted(lignes_conflit), f"Décaler le second d'au moins {mini} minutes.")
        par_jour = defaultdict(int)
        for w, mt, src in occ_t:
            if mt != "STORIES":
                par_jour[w.date()] += 1
        for jour, n in par_jour.items():
            if n > 2 and volumes < 8:
                volumes += 1
                add("conseil", "volume", f"compte {code} : {n} posts de fil le {jour.strftime('%d/%m')}.", [],
                    "Un à deux posts de fil par jour et par compte ; le reste en story.")
        if len(occ_t) > 25:
            add("bloquant", "volume", f"compte {code} : {len(occ_t)} publications, Meta limite à 25 par 24 h.", [])
    if conflits > 8:
        add("attention", "espacement", f"… et {conflits - 8} autres collisions d'horaires.", [])

    # --- doublons
    for (asset_id, code), lignes in par_asset_compte.items():
        if len(lignes) > 1:
            add("attention", "doublons", f"compte {code} : le même post de la banque est prévu {len(lignes)} fois (lignes {', '.join(map(str, lignes))}).", lignes)

    # --- formats et rythme (vue campagne)
    if n_lignes >= 3:
        if len(formats) == 1:
            seul = next(iter(formats))
            noms = {"IMAGE": "images seules", "CAROUSEL": "carrousels", "REELS": "reels", "STORIES": "stories"}
            add("conseil", "formats", f"la campagne n'utilise que des {noms.get(seul, seul)}.", [],
                "Mélangez : carrousel pour la portée, reel pour la découverte, story pour l'urgence.")
    if len(jours) >= 2:
        js = sorted(jours)
        for a, b in zip(js, js[1:]):
            if (b - a).days > 3:
                add("conseil", "rythme", f"trou de {(b - a).days} jours entre le {a.strftime('%d/%m')} et le {b.strftime('%d/%m')}.", [],
                    "Un rappel ou une story intermédiaire maintient l'attention.")
    if n_lignes >= 2 and len(jours) == 1:
        add("conseil", "rythme", "tous les envois tombent le même jour.", [],
            "Un teaser la veille et un bilan le lendemain prolongent la séquence.")

    # --- score
    nb = {"bloquant": 0, "attention": 0, "conseil": 0}
    for a in alertes:
        nb[a["niveau"]] += 1
    score = max(0, 100 - 25 * nb["bloquant"] - 8 * nb["attention"] - 2 * nb["conseil"])
    ordre = {"bloquant": 0, "attention": 1, "conseil": 2}
    alertes.sort(key=lambda a: ordre[a["niveau"]])
    return {"score": score, "compte": nb, "alertes": alertes,
            "stats": {"envois": n_lignes, "cibles": n_cibles, "jours": len(jours),
                      "formats": dict(formats), "comptes": len(occupation)},
            "resume": ("aucun point bloquant" if not nb["bloquant"] else f"{nb['bloquant']} point(s) bloquant(s)")
                      + f", {nb['attention']} point(s) d'attention, {nb['conseil']} conseil(s)."}
