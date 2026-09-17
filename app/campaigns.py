"""Campagnes, banque de posts, groupes de comptes, constructeur de plan, conseils, kDrive (lecture seule)."""
from __future__ import annotations

import csv
import io
import random
import re
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel

from . import insights, kdrive
from .database import Account, AccountGroup, Asset, Campaign, Post, PostTarget, SessionLocal, User, get_db
from .departments import DEPARTMENTS, NAME_OF, REGION_OF, REGIONS
from .deps import current_user, require_admin
from .security import audit

router = APIRouter()
CODES = [c for c, _ in DEPARTMENTS]
CODESET = set(CODES)


def M():
    """Accès tardif à app.main (évite l'import circulaire)."""
    from . import main
    return main


# ====================================================================== groupes

def ensure_region_groups(db) -> None:
    existing = {g.name for g in db.query(AccountGroup).all()}
    for name, codes in REGIONS.items():
        if name not in existing:
            db.add(AccountGroup(name=name, codes=",".join(codes), kind="region"))
    db.commit()


def _group_out(g: AccountGroup) -> dict:
    codes = [c for c in (g.codes or "").split(",") if c]
    return {"id": g.id, "name": g.name, "codes": codes, "kind": g.kind, "n": len(codes)}


def expand_group_token(token: str) -> list[str] | None:
    """« groupe:Nom », un nom de groupe ou de région -> codes. None si inconnu."""
    t = (token or "").strip()
    if t.lower().startswith("groupe:"):
        t = t[7:].strip()
    if not t:
        return None
    for name, codes in REGIONS.items():
        if name.lower() == t.lower():
            return list(codes)
    db = SessionLocal()
    try:
        g = db.query(AccountGroup).filter(AccountGroup.name.ilike(t)).first()
        return [c for c in (g.codes or "").split(",") if c] if g else None
    finally:
        db.close()


def resolve_targets(value) -> list[str] | str:
    """Cibles libres -> 'all' ou liste de codes. Accepte codes, régions, groupes, mélanges."""
    if isinstance(value, list):
        tokens = [str(v) for v in value]
    else:
        s = str(value or "").strip()
        if s.lower() in ("all", "tous", "tout", "*", "national"):
            return "all"
        tokens = [x.strip() for x in re.split(r"[,;|\n]+", s) if x.strip()]
    if any(t.lower() in ("all", "tous", "tout", "*", "national") for t in tokens):
        return "all"
    out: list[str] = []
    for t in tokens:
        c = t.upper()
        if c.isdigit() and len(c) == 1:
            c = "0" + c
        if c in CODESET:
            out.append(c)
            continue
        codes = expand_group_token(t)
        if codes is None:
            # peut-être des codes séparés par des espaces
            parts = [p.upper() for p in re.split(r"[\s/]+", t) if p]
            if parts and all((("0" + p) if len(p) == 1 else p) in CODESET for p in parts):
                out.extend((("0" + p) if len(p) == 1 else p) for p in parts)
                continue
            raise ValueError(f"cible inconnue : « {t} » (code de département, région ou groupe)")
        out.extend(codes)
    seen, uniq = set(), []
    for c in out:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    if not uniq:
        raise ValueError("aucune cible")
    return uniq


@router.get("/api/groupes")
def groupes_list(user: User = Depends(current_user), db=Depends(get_db)):
    ensure_region_groups(db)
    gs = db.query(AccountGroup).order_by(AccountGroup.kind.desc(), AccountGroup.name).all()
    return [_group_out(g) for g in gs]


class GroupIn(BaseModel):
    name: str
    codes: list[str] | str


@router.post("/api/groupes")
def groupes_save(body: GroupIn, user: User = Depends(require_admin), db=Depends(get_db)):
    name = body.name.strip()[:60]
    if not name:
        raise HTTPException(400, "nom du groupe manquant")
    try:
        codes = resolve_targets(body.codes)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if codes == "all":
        codes = CODES
    g = db.query(AccountGroup).filter(AccountGroup.name.ilike(name)).first()
    if g and g.kind == "region":
        raise HTTPException(400, "les régions sont prédéfinies, choisissez un autre nom")
    if not g:
        g = AccountGroup(name=name, kind="custom")
        db.add(g)
    g.codes = ",".join(codes)
    db.commit()
    audit(db, user.email, "group_save", f"{name} ({len(codes)} comptes)")
    return _group_out(g)


@router.delete("/api/groupes/{gid}")
def groupes_delete(gid: int, user: User = Depends(require_admin), db=Depends(get_db)):
    g = db.get(AccountGroup, gid)
    if not g:
        raise HTTPException(404, "groupe inconnu")
    if g.kind == "region":
        raise HTTPException(400, "les régions ne se suppriment pas")
    db.delete(g)
    db.commit()
    audit(db, user.email, "group_delete", g.name)
    return {"ok": True}


@router.get("/api/regions")
def regions_list():
    return {"regions": REGIONS, "region_of": REGION_OF, "noms": NAME_OF}


# ====================================================================== banque de posts

def _asset_public(urls: list[str]) -> bool:
    if not urls:
        return False
    for u in urls:
        if u.startswith("variant:"):
            from .database import VariantSet
            db = SessionLocal()
            try:
                vs = db.get(VariantSet, int(u[8:])) if u[8:].isdigit() else None
                if not vs or (vs.public_ok or 0) < len(vs.variants):
                    return False
            finally:
                db.close()
        elif not u.lower().startswith("https://"):
            return False
    return True


def _asset_first_url(urls: list[str]) -> str:
    if not urls:
        return ""
    if urls[0].startswith("variant:"):
        from .main import db_first_variant
        v = db_first_variant(urls[0])
        return f"/static/{v}" if v else ""
    return urls[0]


def _asset_out(a: Asset) -> dict:
    urls = [u.strip() for u in (a.media_url or "").replace("|", "\n").splitlines() if u.strip()]
    return {"id": a.id, "title": a.title, "media_type": a.media_type,
            "type_label": {"IMAGE": "post", "CAROUSEL": "carrousel", "REELS": "reel", "STORIES": "story"}.get(a.media_type, a.media_type),
            "media_url": a.media_url or "", "urls": urls, "first_url": _asset_first_url(urls),
            "caption": a.caption or "", "tags": [t.strip() for t in (a.tags or "").split(",") if t.strip()],
            "notes": a.notes or "", "source": a.source, "kdrive_name": a.kdrive_name,
            "status": a.status, "used_count": a.used_count or 0,
            "public": _asset_public(urls),
            "created_at": a.created_at.strftime("%Y-%m-%d") if a.created_at else "",
            "updated_at": a.updated_at.strftime("%Y-%m-%d %H:%M") if a.updated_at else ""}


class AssetIn(BaseModel):
    title: str
    media_type: str = "IMAGE"
    media_url: str = ""
    caption: str = ""
    tags: str = ""
    notes: str = ""
    status: str = "pret"


def _check_asset(body: AssetIn) -> None:
    if not body.title.strip():
        raise HTTPException(400, "titre manquant")
    if body.media_type not in ("IMAGE", "CAROUSEL", "REELS", "STORIES"):
        raise HTTPException(400, "type inconnu")
    urls = [u.strip() for u in body.media_url.replace("|", "\n").splitlines() if u.strip()]
    for u in urls:
        if u.startswith("variant:"):
            continue
        if not u.lower().startswith(("http://", "https://")):
            raise HTTPException(400, f"URL non conforme : {u[:60]}")
    if body.media_type == "CAROUSEL" and urls and not 2 <= len(urls) <= 10:
        raise HTTPException(400, "un carrousel demande 2 à 10 images")
    if body.status not in ("pret", "brouillon", "archive"):
        raise HTTPException(400, "statut inconnu")


@router.get("/api/banque")
def banque_list(q: str = "", tag: str = "", type: str = "", statut: str = "",
                user: User = Depends(current_user), db=Depends(get_db)):
    items = db.query(Asset).order_by(Asset.updated_at.desc()).all()
    out = []
    for a in items:
        o = _asset_out(a)
        if statut and o["status"] != statut:
            continue
        if not statut and o["status"] == "archive":
            continue
        if type and o["media_type"] != type:
            continue
        if tag and tag.lower() not in [t.lower() for t in o["tags"]]:
            continue
        if q and q.lower() not in (o["title"] + " " + o["caption"] + " " + " ".join(o["tags"])).lower():
            continue
        out.append(o)
    tags = sorted({t for a in items for t in _asset_out(a)["tags"]}, key=str.lower)
    return {"items": out, "tags": tags, "total": len(items)}


@router.post("/api/banque")
def banque_create(body: AssetIn, user: User = Depends(require_admin), db=Depends(get_db)):
    _check_asset(body)
    a = Asset(title=body.title.strip()[:120], media_type=body.media_type,
              media_url=body.media_url.replace("|", "\n").strip(), caption=body.caption,
              tags=body.tags.strip(), notes=body.notes, status=body.status, source="url")
    db.add(a)
    db.commit()
    audit(db, user.email, "asset_create", f"#{a.id} {a.title}")
    return _asset_out(a)


@router.put("/api/banque/{aid}")
def banque_update(aid: int, body: AssetIn, user: User = Depends(require_admin), db=Depends(get_db)):
    a = db.get(Asset, aid)
    if not a:
        raise HTTPException(404, "post inconnu")
    _check_asset(body)
    a.title, a.media_type = body.title.strip()[:120], body.media_type
    a.media_url = body.media_url.replace("|", "\n").strip()
    a.caption, a.tags, a.notes, a.status = body.caption, body.tags.strip(), body.notes, body.status
    db.commit()
    audit(db, user.email, "asset_update", f"#{a.id} {a.title}")
    return _asset_out(a)


@router.post("/api/banque/{aid}/dupliquer")
def banque_duplicate(aid: int, user: User = Depends(require_admin), db=Depends(get_db)):
    a = db.get(Asset, aid)
    if not a:
        raise HTTPException(404, "post inconnu")
    b = Asset(title=f"{a.title} (copie)", media_type=a.media_type, media_url=a.media_url,
              caption=a.caption, tags=a.tags, notes=a.notes, source=a.source,
              kdrive_file_id=a.kdrive_file_id, kdrive_name=a.kdrive_name, local_path=a.local_path,
              status="brouillon")
    db.add(b)
    db.commit()
    return _asset_out(b)


@router.delete("/api/banque/{aid}")
def banque_delete(aid: int, definitif: bool = False, user: User = Depends(require_admin), db=Depends(get_db)):
    a = db.get(Asset, aid)
    if not a:
        raise HTTPException(404, "post inconnu")
    if definitif:
        db.delete(a)
        audit(db, user.email, "asset_delete", f"#{aid}")
    else:
        a.status = "archive"
        audit(db, user.email, "asset_archive", f"#{aid}")
    db.commit()
    return {"ok": True}


@router.post("/api/banque/upload")
async def banque_upload(file: UploadFile = File(...), title: str = "", caption: str = "",
                        media_type: str = "IMAGE", tags: str = "",
                        user: User = Depends(require_admin), db=Depends(get_db)):
    """Dépose un visuel depuis l'ordinateur dans static/media/ et crée un post de la banque."""
    data = await file.read()
    if len(data) > 60 * 1024 * 1024:
        raise HTTPException(400, "fichier trop lourd (60 Mo maximum)")
    try:
        rel, url = kdrive.save_media(data, file.filename or "media.jpg", prefix="up_")
    except kdrive.KDriveError as e:
        raise HTTPException(400, str(e))
    a = Asset(title=(title or file.filename or "visuel").strip()[:120], media_type=media_type,
              media_url=url, caption=caption, tags=tags, source="upload", local_path=rel)
    db.add(a)
    db.commit()
    audit(db, user.email, "asset_upload", f"#{a.id} {file.filename}")
    return _asset_out(a)


# ---------- kDrive : LECTURE SEULE (liste, vignette, téléchargement vers la banque) ----------

@router.get("/api/kdrive/etat")
def kdrive_etat(user: User = Depends(current_user)):
    return kdrive.etat()


@router.get("/api/kdrive/fichiers")
def kdrive_fichiers(dossier: str = "", user: User = Depends(require_admin)):
    try:
        return {"dossier": dossier or kdrive.settings.kdrive_folder_id, "fichiers": kdrive.list_files(dossier or None)}
    except kdrive.KDriveError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"kDrive injoignable : {e}")


@router.get("/api/kdrive/vignette/{file_id}")
def kdrive_vignette(file_id: str, user: User = Depends(require_admin)):
    try:
        content, mime = kdrive.thumbnail(file_id)
    except Exception:
        return Response(status_code=204)
    return Response(content=content, media_type=mime)


class KImportIn(BaseModel):
    title: str = ""
    caption: str = ""
    media_type: str = "IMAGE"
    tags: str = ""


@router.post("/api/kdrive/importer/{file_id}")
def kdrive_importer(file_id: str, body: KImportIn, user: User = Depends(require_admin), db=Depends(get_db)):
    """Copie un fichier kDrive dans la banque (lecture sur kDrive, écriture locale uniquement)."""
    try:
        info = kdrive.import_file(file_id)
    except kdrive.KDriveError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"kDrive injoignable : {e}")
    a = Asset(title=(body.title or info["name"]).strip()[:120], media_type=body.media_type,
              media_url=info["url"], caption=body.caption, tags=body.tags, source="kdrive",
              kdrive_file_id=info["file_id"], kdrive_name=info["name"], local_path=info["local_path"])
    db.add(a)
    db.commit()
    audit(db, user.email, "asset_kdrive", f"#{a.id} {info['name']}")
    return _asset_out(a)


# ====================================================================== campagnes

def _campaign_out(db, c: Campaign, with_posts: bool = False) -> dict:
    posts = [M()._serialize_post(p) for p in c.posts]
    counts = defaultdict(int)
    cibles = 0
    comptes: set[str] = set()
    for p in posts:
        for k, v in p["counts"].items():
            counts[k] += v
        cibles += p["total"]
        comptes.update(p["targets"])
    whens = sorted(w for w in (p["when"] for p in posts) if w)
    out = {"id": c.id, "name": c.name, "description": c.description or "", "objective": c.objective or "",
           "start_date": c.start_date or "", "end_date": c.end_date or "", "status": c.status,
           "created_at": c.created_at.strftime("%Y-%m-%d") if c.created_at else "",
           "n_posts": len(posts), "n_cibles": cibles, "n_comptes": len(comptes), "counts": dict(counts),
           "premier": whens[0] if whens else None, "dernier": whens[-1] if whens else None}
    if with_posts:
        posts.sort(key=lambda o: (o["when"] or o["created_at"] or ""))
        out["posts"] = posts
    return out


class CampaignIn(BaseModel):
    name: str
    description: str = ""
    objective: str = ""
    start_date: str = ""
    end_date: str = ""
    status: str = "brouillon"


@router.get("/api/campagnes")
def campagnes_list(user: User = Depends(current_user), db=Depends(get_db)):
    cs = db.query(Campaign).order_by(Campaign.created_at.desc()).all()
    return [_campaign_out(db, c) for c in cs]


@router.post("/api/campagnes")
def campagnes_create(body: CampaignIn, user: User = Depends(require_admin), db=Depends(get_db)):
    if not body.name.strip():
        raise HTTPException(400, "nom de campagne manquant")
    c = Campaign(name=body.name.strip()[:80], description=body.description, objective=body.objective,
                 start_date=body.start_date, end_date=body.end_date, status=body.status or "brouillon",
                 created_by=user.id)
    db.add(c)
    db.commit()
    audit(db, user.email, "campaign_create", f"#{c.id} {c.name}")
    return _campaign_out(db, c)


@router.put("/api/campagnes/{cid}")
def campagnes_update(cid: int, body: CampaignIn, user: User = Depends(require_admin), db=Depends(get_db)):
    c = db.get(Campaign, cid)
    if not c:
        raise HTTPException(404, "campagne inconnue")
    if body.status not in ("brouillon", "active", "terminee"):
        raise HTTPException(400, "statut inconnu")
    c.name, c.description, c.objective = body.name.strip()[:80] or c.name, body.description, body.objective
    c.start_date, c.end_date, c.status = body.start_date, body.end_date, body.status
    db.commit()
    audit(db, user.email, "campaign_update", f"#{c.id} {c.name} {c.status}")
    return _campaign_out(db, c)


@router.get("/api/campagnes/{cid}")
def campagnes_get(cid: int, user: User = Depends(current_user), db=Depends(get_db)):
    c = db.get(Campaign, cid)
    if not c:
        raise HTTPException(404, "campagne inconnue")
    return _campaign_out(db, c, with_posts=True)


# ---------- constructeur de plan ----------

class BuildItem(BaseModel):
    asset_id: int | None = None
    title: str = ""
    media_type: str = "IMAGE"
    media_url: str = ""
    caption: str = ""
    platform: str = "instagram"
    targets: list[str] | str = "all"
    when: str = ""                       # "AAAA-MM-JJTHH:MM" heure de Paris, vide = immédiat
    taille_lot: int = 0                  # > 0 : échelonner par lots
    intervalle: int = 2                  # minutes entre deux lots
    ordre: str = "code"                  # code | aleatoire


class BuildIn(BaseModel):
    items: list[BuildItem]
    seulement_actifs: bool = True


def _parse_when(s: str) -> datetime | None:
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    raise ValueError(f"heure illisible « {s} »")


def _existing_rows(db, exclude_campaign: int | None = None) -> list[dict]:
    """Envois déjà programmés (toutes campagnes) pour détecter les collisions."""
    out = []
    for p in db.query(Post).all():
        pend = [t for t in p.targets if t.status == "pending"]
        if not pend:
            continue
        when = M().to_paris_iso(p.scheduled_at or p.created_at)
        out.append({"id": p.id, "when": datetime.strptime(when, "%Y-%m-%dT%H:%M") if when else datetime.now(),
                    "media_type": p.media_type, "targets": [t.account.department_code for t in pend],
                    "campaign_id": p.campaign_id})
    return out


def generer_rows(db, body: BuildIn) -> tuple[list[dict], list[str], list[str]]:
    """Transforme les intentions (post × cibles × horaire × échelonnement) en envois concrets."""
    actifs = {a.department_code for a in db.query(Account).filter(Account.status == "active").all()}
    rows, erreurs, exclus = [], [], set()
    i = 0
    for k, it in enumerate(body.items, start=1):
        try:
            asset = db.get(Asset, it.asset_id) if it.asset_id else None
            media_type = it.media_type or (asset.media_type if asset else "IMAGE")
            media_url = it.media_url or (asset.media_url if asset else "")
            caption = it.caption if it.caption else (asset.caption if asset else "")
            title = it.title or (asset.title if asset else f"envoi {k}")
            targets = resolve_targets(it.targets)
            codes = CODES if targets == "all" else targets
            if body.seulement_actifs:
                keep = [c for c in codes if c in actifs]
                exclus.update(c for c in codes if c not in actifs)
                codes = keep
            if not codes:
                raise ValueError("aucun compte actif dans les cibles")
            when = _parse_when(it.when)
            if it.ordre == "aleatoire":
                codes = codes[:]
                random.shuffle(codes)
            lots = [codes] if not it.taille_lot or it.taille_lot >= len(codes) else \
                   [codes[j:j + it.taille_lot] for j in range(0, len(codes), it.taille_lot)]
            for n, lot in enumerate(lots):
                i += 1
                w = (when + timedelta(minutes=n * max(1, it.intervalle))) if (when and len(lots) > 1) else when
                rows.append({"i": i, "item": k, "asset_id": it.asset_id, "title": title,
                             "media_type": media_type, "media_url": media_url, "caption": caption,
                             "platform": it.platform, "targets": lot,
                             "when": w.strftime("%Y-%m-%dT%H:%M") if w else "",
                             "stagger": len(lots) > 1, "lot": f"{n + 1}/{len(lots)}" if len(lots) > 1 else ""})
        except ValueError as e:
            erreurs.append(f"intention {k} ({it.title or it.asset_id}) : {e}")
    return rows, erreurs, sorted(exclus)


def _analyse(db, rows: list[dict], exclude_campaign: int | None = None) -> dict:
    rs = []
    for r in rows:
        rs.append({**r, "when": datetime.strptime(r["when"], "%Y-%m-%dT%H:%M") if r.get("when") else None})
    return insights.analyser(rs, _existing_rows(db, exclude_campaign))


@router.post("/api/campagnes/{cid}/generer")
def campagnes_generer(cid: int, body: BuildIn, user: User = Depends(require_admin), db=Depends(get_db)):
    c = db.get(Campaign, cid)
    if not c:
        raise HTTPException(404, "campagne inconnue")
    rows, erreurs, exclus = generer_rows(db, body)
    return {"rows": rows, "erreurs": erreurs, "exclus": exclus, "insights": _analyse(db, rows)}


class RowsIn(BaseModel):
    rows: list[dict]


@router.post("/api/campagnes/{cid}/analyser")
def campagnes_analyser(cid: int, body: RowsIn, user: User = Depends(require_admin), db=Depends(get_db)):
    """Ré-analyse des lignes modifiées à la main (heures, suppressions) avant programmation."""
    rows = [{**r, "i": r.get("i", n + 1)} for n, r in enumerate(body.rows)]
    return _analyse(db, rows)


@router.post("/api/campagnes/{cid}/programmer")
def campagnes_programmer(cid: int, body: RowsIn, user: User = Depends(require_admin), db=Depends(get_db)):
    c = db.get(Campaign, cid)
    if not c:
        raise HTTPException(404, "campagne inconnue")
    main = M()
    created, erreurs = [], []
    for n, r in enumerate(body.rows, start=1):
        try:
            when = _parse_when(r.get("when", ""))
            targets = r.get("targets") or "all"
            pin = main.PostIn(caption=r.get("caption", ""), media_url=(r.get("media_url") or "").replace("|", "\n"),
                              media_type=r.get("media_type", "IMAGE"), platform=r.get("platform", "instagram"),
                              scheduled_at=when, targets=targets, label=c.name,
                              campaign_id=c.id, asset_id=r.get("asset_id"))
            post, _ = main._create_post(db, user, pin)
            created.append(post.id)
        except HTTPException as e:
            erreurs.append(f"ligne {n} : {e.detail}")
        except ValueError as e:
            erreurs.append(f"ligne {n} : {e}")
    if created and c.status == "brouillon":
        c.status = "active"
        db.commit()
    audit(db, user.email, "campaign_schedule", f"#{c.id} envois={len(created)} erreurs={len(erreurs)}")
    return {"ok": len(created), "post_ids": created, "erreurs": erreurs}


@router.get("/api/campagnes/{cid}/insights")
def campagnes_insights(cid: int, user: User = Depends(current_user), db=Depends(get_db)):
    c = db.get(Campaign, cid)
    if not c:
        raise HTTPException(404, "campagne inconnue")
    rows = []
    for n, p in enumerate(c.posts, start=1):
        pend = [t for t in p.targets if t.status == "pending"]
        if not pend:
            continue
        rows.append({"i": p.id, "asset_id": p.asset_id, "media_type": p.media_type, "media_url": p.media_url,
                     "caption": p.caption, "targets": [t.account.department_code for t in pend],
                     "when": M().to_paris_iso(p.scheduled_at) or "", "stagger": True})
    # les envois de cette campagne sont dans rows : on exclut les mêmes posts des « existants »
    rs = [{**r, "when": datetime.strptime(r["when"], "%Y-%m-%dT%H:%M") if r["when"] else None} for r in rows]
    ids = {r["i"] for r in rows}
    exist = [e for e in _existing_rows(db) if e["id"] not in ids]
    res = insights.analyser(rs, exist)
    res["regles"] = insights.REGLES
    return res


@router.get("/api/insights/regles")
def insights_regles():
    return insights.REGLES


@router.get("/api/campagnes/{cid}/couverture")
def campagnes_couverture(cid: int, user: User = Depends(current_user), db=Depends(get_db)):
    c = db.get(Campaign, cid)
    if not c:
        raise HTTPException(404, "campagne inconnue")
    n = defaultdict(int)
    pub = defaultdict(int)
    for p in c.posts:
        for t in p.targets:
            if t.status != "cancelled":
                n[t.account.department_code] += 1
            if t.status == "published":
                pub[t.account.department_code] += 1
    status = {a.department_code: a.status for a in db.query(Account).all()}
    return {"departements": [{"code": code, "name": name, "n": n.get(code, 0), "publies": pub.get(code, 0),
                              "status": status.get(code, "unlinked"), "region": REGION_OF.get(code, "")}
                             for code, name in DEPARTMENTS],
            "couverts": sum(1 for code, _ in DEPARTMENTS if n.get(code)), "total": len(DEPARTMENTS)}


@router.get("/api/campagnes/{cid}/export.csv")
def campagnes_export(cid: int, user: User = Depends(current_user), db=Depends(get_db)):
    """Export au format du modèle d'import : ré-importable tel quel."""
    c = db.get(Campaign, cid)
    if not c:
        raise HTTPException(404, "campagne inconnue")
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["quand", "cibles", "type", "visuel", "legende", "libelle", "plateforme", "etat"])
    label = {"IMAGE": "post", "CAROUSEL": "carrousel", "REELS": "reel", "STORIES": "story"}
    for p in sorted(c.posts, key=lambda p: (p.scheduled_at or p.created_at)):
        when = M().to_paris_iso(p.scheduled_at)
        quand = datetime.strptime(when, "%Y-%m-%dT%H:%M").strftime("%d/%m/%Y %H:%M") if when else ""
        s = M()._serialize_post(p)
        cibles = "all" if p.is_national else ", ".join(s["targets"])
        w.writerow([quand, cibles, label.get(p.media_type, p.media_type), (p.media_url or "").replace("\n", "|"),
                    p.caption or "", p.label or c.name, p.platform, s["state"]])
    data = "﻿" + buf.getvalue()
    return Response(content=data.encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="plan-{c.id}.csv"'})


class ShiftIn(BaseModel):
    minutes: int


@router.post("/api/campagnes/{cid}/decaler")
def campagnes_decaler(cid: int, body: ShiftIn, user: User = Depends(require_admin), db=Depends(get_db)):
    """Décale tous les envois encore en attente de la campagne (retard de séquence, changement d'heure)."""
    c = db.get(Campaign, cid)
    if not c:
        raise HTTPException(404, "campagne inconnue")
    n = 0
    for p in c.posts:
        if p.scheduled_at and any(t.status == "pending" for t in p.targets):
            p.scheduled_at = p.scheduled_at + timedelta(minutes=body.minutes)
            n += 1
    db.commit()
    audit(db, user.email, "campaign_shift", f"#{c.id} {body.minutes:+d} min sur {n} envois")
    return {"decales": n}


@router.post("/api/campagnes/{cid}/annuler")
def campagnes_annuler(cid: int, user: User = Depends(require_admin), db=Depends(get_db)):
    c = db.get(Campaign, cid)
    if not c:
        raise HTTPException(404, "campagne inconnue")
    n = 0
    for p in c.posts:
        for t in p.targets:
            if t.status == "pending":
                t.status, t.error = "cancelled", "campagne annulee"
                n += 1
    db.commit()
    audit(db, user.email, "campaign_cancel", f"#{c.id} cibles={n}")
    return {"cancelled": n}


# ====================================================================== déclinaisons de visuels

from fastapi import Form  # noqa: E402
from . import visuels  # noqa: E402
from .database import Variant, VariantSet  # noqa: E402
from .config import settings as _settings  # noqa: E402


def _vset_out(vs: VariantSet) -> dict:
    base = _settings.public_base_url.rstrip("/")
    ex = next((v for v in vs.variants if v.code == "75"), vs.variants[0] if vs.variants else None)
    return {"id": vs.id, "title": vs.title, "template": vs.template, "style": vs.style, "position": vs.position,
            "n": len(vs.variants), "public_ok": vs.public_ok or 0,
            "zip_url": f"/static/{vs.zip_path}" if vs.zip_path else None,
            "exemple_url": f"/static/{ex.local_path}" if ex else None,
            "media_url": f"variant:{vs.id}", "kdrive_folder_id": vs.kdrive_folder_id,
            "public_base": base, "created_at": vs.created_at.strftime("%Y-%m-%d %H:%M") if vs.created_at else ""}


async def _base_bytes(file: UploadFile | None, url: str, db) -> bytes:
    if file is not None and file.filename:
        data = await file.read()
        if len(data) > 25 * 1024 * 1024:
            raise HTTPException(400, "image trop lourde (25 Mo maximum)")
        return data
    url = (url or "").strip()
    if not url:
        raise HTTPException(400, "fournissez une image (fichier ou URL https)")
    if url.startswith("/static/"):
        p = kdrive.MEDIA_DIR.parent / url[len("/static/"):]
        if not p.exists():
            raise HTTPException(400, "fichier local introuvable")
        return p.read_bytes()
    try:
        import httpx
        with httpx.Client(timeout=60, follow_redirects=True) as c:
            r = c.get(url)
        if r.status_code != 200:
            raise HTTPException(400, f"image injoignable ({r.status_code})")
        return r.content
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"image injoignable : {e}")


@router.post("/api/visuels/apercu")
async def visuels_apercu(file: UploadFile | None = File(None), url: str = Form(""), template: str = Form("{departement}"),
                         style: str = Form("bandeau_bleu"), position: str = Form("bas"), taille: float = Form(1.0),
                         minuscules: bool = Form(True), code: str = Form("75"),
                         user: User = Depends(require_admin), db=Depends(get_db)):
    data = await _base_bytes(file, url, db)
    try:
        out = visuels.apercu(data, template, code, style=style, position=position, taille=taille, minuscules=minuscules)
    except Exception as e:
        raise HTTPException(400, f"image illisible : {e}")
    return Response(content=out, media_type="image/jpeg")


@router.post("/api/visuels/generer")
async def visuels_generer(file: UploadFile | None = File(None), url: str = Form(""), title: str = Form("Visuel décliné"),
                          template: str = Form("{departement}"), style: str = Form("bandeau_bleu"),
                          position: str = Form("bas"), taille: float = Form(1.0), minuscules: bool = Form(True),
                          caption: str = Form(""), tags: str = Form(""), creer_post: bool = Form(True),
                          user: User = Depends(require_admin), db=Depends(get_db)):
    """Génère les 101 versions + zip, enregistre le jeu, et crée un post de banque « variant:<id> »."""
    data = await _base_bytes(file, url, db)
    vs = VariantSet(title=title.strip()[:120] or "Visuel décliné", template=template, style=style, position=position,
                    slug=visuels._slug(title))
    db.add(vs)
    db.flush()
    rel, _ = kdrive.save_media(data, (file.filename if file and file.filename else "base.jpg"), prefix=f"base_set{vs.id}_")
    vs.base_path = rel
    try:
        files = visuels.generer(vs.id, data, template, title, style=style, position=position, taille=taille, minuscules=minuscules)
    except Exception as e:
        db.rollback()
        raise HTTPException(400, f"génération impossible : {e}")
    for f in files:
        db.add(Variant(set_id=vs.id, code=f["code"], local_path=f["path"]))
    vs.zip_path = visuels.zip_path(vs.id, title)
    asset = None
    if creer_post:
        asset = Asset(title=title.strip()[:120], media_type="IMAGE", media_url=f"variant:{vs.id}",
                      caption=caption, tags=tags, source="variants", status="pret",
                      notes=f"Visuel décliné par département (jeu #{vs.id}, texte « {template} »).")
        db.add(asset)
    db.commit()
    audit(db, user.email, "variants_generate", f"set={vs.id} {title} n={len(files)}")
    out = _vset_out(vs)
    out["asset_id"] = asset.id if asset else None
    return out


@router.get("/api/visuels")
def visuels_list(user: User = Depends(current_user), db=Depends(get_db)):
    return [_vset_out(vs) for vs in db.query(VariantSet).order_by(VariantSet.created_at.desc()).all()]


@router.get("/api/visuels/{sid}")
def visuels_get(sid: int, user: User = Depends(current_user), db=Depends(get_db)):
    vs = db.get(VariantSet, sid)
    if not vs:
        raise HTTPException(404, "jeu inconnu")
    out = _vset_out(vs)
    out["variants"] = [{"code": v.code, "url": f"/static/{v.local_path}", "public_url": v.public_url,
                        "kdrive_file_id": v.kdrive_file_id} for v in sorted(vs.variants, key=lambda x: x.code)]
    return out


@router.post("/api/visuels/{sid}/kdrive")
def visuels_kdrive(sid: int, user: User = Depends(require_admin), db=Depends(get_db)):
    """Dépose les 101 images sur kDrive (dossier public), crée les liens publics, vérifie que Meta
    pourra les lire, et enregistre l'adresse directe de chaque version. Nécessite KDRIVE_ECRITURE=1."""
    vs = db.get(VariantSet, sid)
    if not vs:
        raise HTTPException(404, "jeu inconnu")
    try:
        kdrive.ecriture_autorisee()
    except kdrive.KDriveError as e:
        raise HTTPException(400, str(e))
    folder = _settings.kdrive_public_folder_id or _settings.kdrive_folder_id or "1"
    ok, erreurs, journal = 0, [], ""
    for v in sorted(vs.variants, key=lambda x: x.code):
        if v.public_url:
            ok += 1
            continue
        try:
            p = kdrive.MEDIA_DIR.parent / v.local_path
            if not v.kdrive_file_id:
                up = kdrive.upload_bytes(p.name, p.read_bytes(), folder)
                v.kdrive_file_id = up["id"]
            share = kdrive.create_public_share(v.kdrive_file_id)
            url, journal = kdrive.public_url_for(share, v.kdrive_file_id)
            if url:
                v.public_url = url
                ok += 1
            else:
                erreurs.append(f"{v.code} : lien public créé mais aucune adresse directe lisible")
            db.commit()
        except Exception as e:
            erreurs.append(f"{v.code} : {e}")
            db.rollback()
            if len(erreurs) >= 3 and ok == 0:
                break  # inutile d'insister 101 fois si la config est mauvaise
    vs.kdrive_folder_id = folder
    vs.public_ok = ok
    db.commit()
    audit(db, user.email, "variants_kdrive", f"set={vs.id} ok={ok} erreurs={len(erreurs)}")
    return {"ok": ok, "total": len(vs.variants), "erreurs": erreurs[:10], "journal": journal[-1500:]}
