"""Connecteur kDrive (Infomaniak) pour la banque de posts.

Configuration (.env) : KDRIVE_TOKEN (token API, périmètre « drive »), KDRIVE_DRIVE_ID,
KDRIVE_FOLDER_ID (1 = racine). Les fichiers importés sont copiés sous static/media/ et servis
à l'adresse PUBLIC_BASE_URL/static/media/… — c'est cette URL que Meta viendra chercher.

API utilisée (documentation développeur Infomaniak) :
  GET  https://api.infomaniak.com/3/drive/{drive_id}/files/{dir_id}/files   liste d'un dossier
  GET  https://api.infomaniak.com/2/drive/{drive_id}/files/{file_id}/download  contenu d'un fichier
  GET  https://api.infomaniak.com/2/drive/{drive_id}/files/{file_id}/thumbnail vignette
Les chemins sont centralisés ici pour être ajustés en un seul endroit si l'API évolue.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path

import httpx

from .config import settings

API = "https://api.infomaniak.com"
MEDIA_DIR = Path(__file__).parent.parent / "static" / "media"
EXTENSIONS_OK = {".jpg", ".jpeg", ".png", ".mp4", ".mov", ".webp"}


class KDriveError(Exception):
    pass


def configured() -> bool:
    return bool(settings.kdrive_token and settings.kdrive_drive_id)


def etat() -> dict:
    return {"configure": configured(), "drive_id": settings.kdrive_drive_id or None,
            "dossier": settings.kdrive_folder_id or "1",
            "public_base_url": settings.public_base_url,
            "url_publique": not settings.public_base_url.startswith(("http://localhost", "http://127.")),
            "media_dir": str(MEDIA_DIR)}


def _headers() -> dict:
    if not configured():
        raise KDriveError("kDrive non configuré : renseignez KDRIVE_TOKEN et KDRIVE_DRIVE_ID dans le .env")
    return {"Authorization": f"Bearer {settings.kdrive_token}", "Accept": "application/json"}


def _check(r: httpx.Response) -> dict:
    if r.status_code == 401:
        raise KDriveError("token kDrive refusé (401) : vérifiez KDRIVE_TOKEN et son périmètre « drive »")
    if r.status_code == 404:
        raise KDriveError("dossier ou fichier kDrive introuvable (404) : vérifiez KDRIVE_DRIVE_ID / KDRIVE_FOLDER_ID")
    if r.status_code >= 400:
        raise KDriveError(f"kDrive a répondu {r.status_code} : {r.text[:200]}")
    try:
        data = r.json()
    except ValueError:
        raise KDriveError("réponse kDrive illisible")
    if isinstance(data, dict) and data.get("result") == "error":
        raise KDriveError(f"kDrive : {data.get('error', {}).get('description', 'erreur')}")
    return data


def list_files(folder_id: str | None = None) -> list[dict]:
    """Fichiers et sous-dossiers d'un dossier kDrive (images et vidéos utiles en premier)."""
    folder_id = folder_id or settings.kdrive_folder_id or "1"
    with httpx.Client(timeout=30) as c:
        r = c.get(f"{API}/3/drive/{settings.kdrive_drive_id}/files/{folder_id}/files",
                  params={"limit": 200},
                  headers=_headers())
        data = _check(r)
    items = data.get("data", data) if isinstance(data, dict) else data
    out = []
    for f in items or []:
        name = f.get("name", "")
        ext = Path(name).suffix.lower()
        kind = f.get("type", "file")
        out.append({"id": str(f.get("id")), "name": name, "type": kind,
                    "size": f.get("size", 0), "mime": f.get("mime_type", ""),
                    "modified": f.get("last_modified_at"),
                    "utilisable": kind == "file" and ext in EXTENSIONS_OK,
                    "ext": ext})
    out.sort(key=lambda x: (x["type"] != "dir", not x["utilisable"], x["name"].lower()))
    return out


def download(file_id: str) -> tuple[bytes, str]:
    """Contenu binaire + type MIME d'un fichier."""
    with httpx.Client(timeout=120, follow_redirects=True) as c:
        r = c.get(f"{API}/2/drive/{settings.kdrive_drive_id}/files/{file_id}/download",
                  headers=_headers())
        if r.status_code >= 400:
            _check(r)
        return r.content, r.headers.get("content-type", "application/octet-stream")


def thumbnail(file_id: str) -> tuple[bytes, str]:
    with httpx.Client(timeout=30, follow_redirects=True) as c:
        r = c.get(f"{API}/2/drive/{settings.kdrive_drive_id}/files/{file_id}/thumbnail",
                  headers=_headers())
        if r.status_code >= 400:
            _check(r)
        return r.content, r.headers.get("content-type", "image/jpeg")


def delete_file(file_id: str) -> None:
    """Supprime (corbeille) un fichier kDrive — réservé aux fichiers déposés par l'app."""
    ecriture_autorisee()
    with httpx.Client(timeout=30) as c:
        r = c.delete(f"{API}/2/drive/{settings.kdrive_drive_id}/files/{file_id}", headers=_headers())
        if r.status_code >= 400:
            _check(r)


def _safe_name(name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "media"
    return base[:80]


def save_media(content: bytes, original_name: str, prefix: str = "") -> tuple[str, str]:
    """Écrit un média sous static/media/ ; renvoie (chemin relatif static, URL publique)."""
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    ext = Path(original_name).suffix.lower()
    if ext not in EXTENSIONS_OK:
        raise KDriveError(f"format non pris en charge : {ext or 'inconnu'} (jpg, png, webp, mp4, mov)")
    fname = f"{prefix}{uuid.uuid4().hex[:8]}_{_safe_name(Path(original_name).stem)}{ext}"
    (MEDIA_DIR / fname).write_bytes(content)
    rel = f"media/{fname}"
    url = f"{settings.public_base_url.rstrip('/')}/static/{rel}"
    return rel, url


def import_file(file_id: str) -> dict:
    """Télécharge un fichier kDrive dans static/media/ et renvoie ses infos."""
    files = {f["id"]: f for f in list_files()}
    meta = files.get(str(file_id))
    name = meta["name"] if meta else f"kdrive-{file_id}.jpg"
    content, mime = download(file_id)
    rel, url = save_media(content, name, prefix="kdrive_")
    return {"file_id": str(file_id), "name": name, "mime": mime, "size": len(content),
            "local_path": rel, "url": url}


# ====================================================================== ÉCRITURE (gardée)
# Rien ci-dessous ne s'exécute tant que KDRIVE_ECRITURE=1 n'est pas dans le .env.

UPLOAD_API = "https://api.kdrive.infomaniak.com"


def ecriture_autorisee() -> None:
    if not settings.kdrive_ecriture:
        raise KDriveError("écriture sur kDrive désactivée (KDRIVE_ECRITURE=0) : l'app ne dépose rien")
    _headers()


def upload_bytes(file_name: str, data: bytes, directory_id: str | None = None,
                 conflict: str = "rename") -> dict:
    """Dépose un fichier dans un dossier kDrive (upload direct v3). Renvoie {id, name, size}."""
    ecriture_autorisee()
    directory_id = directory_id or settings.kdrive_public_folder_id or settings.kdrive_folder_id or "1"
    with httpx.Client(timeout=180) as c:
        r = c.post(f"{UPLOAD_API}/3/drive/{settings.kdrive_drive_id}/upload",
                   params={"directory_id": directory_id, "total_size": len(data),
                           "file_name": file_name, "conflict": conflict},
                   headers={**_headers(), "Content-Type": "application/octet-stream"},
                   content=data)
        data_r = _check(r)
    f = data_r.get("data", data_r)
    return {"id": str(f.get("id")), "name": f.get("name", file_name), "size": f.get("size", len(data))}


def create_public_share(file_id: str) -> dict:
    """Crée (ou récupère) un lien public sans mot de passe ni expiration pour un fichier."""
    ecriture_autorisee()
    base = f"{API}/2/drive/{settings.kdrive_drive_id}/files/{file_id}"
    with httpx.Client(timeout=30) as c:
        r = c.get(f"{base}/link", headers=_headers())
        if r.status_code == 200:
            d = r.json().get("data") or {}
            if d.get("url"):
                return d
        r = c.post(f"{base}/link", headers=_headers(),
                   json={"right": "public", "can_download": True})
        if r.status_code >= 400:
            # variante d'API : /shares avec type public
            r = c.post(f"{base}/shares", headers=_headers(),
                       json={"type": "public", "password_protected": False, "expiration_date": 0})
        d = _check(r)
    return d.get("data", d)


def candidate_public_urls(share: dict, file_id: str) -> list[str]:
    """Adresses possibles de téléchargement direct d'un fichier partagé publiquement.

    Priorité au partage public du dossier (KDRIVE_SHARE_UUID) : un fichier déposé dans le dossier
    partagé est joignable sans créer de lien individuel. Observé sur l'app kDrive (sept. 2026) :
    /3/app/<drive>/share/<uuid>/files/<id>/files pour lister, /2/app/… pour les fichiers.
    """
    url = (share or {}).get("url") or (share or {}).get("share_url") or (share or {}).get("ShareURL") or ""
    uuid = (share or {}).get("uuid") or (share or {}).get("token") or (url.rstrip("/").split("/")[-1] if url else "")
    d = settings.kdrive_drive_id
    out = []
    su = settings.kdrive_share_uuid
    if su:
        out += [f"https://kdrive.infomaniak.com/2/app/{d}/share/{su}/files/{file_id}/download",
                f"https://kdrive.infomaniak.com/3/app/{d}/share/{su}/files/{file_id}/download",
                f"https://kdrive.infomaniak.com/2/app/{d}/share/{su}/files/{file_id}/preview",
                f"https://kdrive.infomaniak.com/app/share/{d}/{su}/files/{file_id}/download"]
    if uuid:
        out += [f"https://kdrive.infomaniak.com/2/drive/{d}/share/{uuid}/files/{file_id}/download",
                f"https://kdrive.infomaniak.com/2/drive/{d}/share/{uuid}/download",
                f"https://kdrive.infomaniak.com/app/share/{d}/{uuid}/download",
                f"https://kdrive.infomaniak.com/app/share/{d}/{uuid}/files/{file_id}/download"]
    if url:
        out += [url + ("&" if "?" in url else "?") + "download=1", url]
    return out


def verify_public_image(url: str) -> tuple[bool, str]:
    """Meta-compatible ? GET sans authentification, 200, type image, contenu binaire."""
    try:
        with httpx.Client(timeout=30, follow_redirects=True) as c:
            r = c.get(url, headers={"User-Agent": "facebookexternalhit/1.1"})
        ct = r.headers.get("content-type", "")
        ok = r.status_code == 200 and ct.startswith(("image/", "video/")) and len(r.content) > 1000
        return ok, f"{r.status_code} {ct} {len(r.content)} octets"
    except Exception as e:
        return False, str(e)


def public_url_for(share: dict, file_id: str) -> tuple[str | None, str]:
    """Teste les adresses candidates et renvoie la première que Meta pourra lire."""
    journal = []
    for u in candidate_public_urls(share, file_id):
        ok, info = verify_public_image(u)
        journal.append(f"{u} -> {info}")
        if ok:
            return u, "\n".join(journal)
    return None, "\n".join(journal)
