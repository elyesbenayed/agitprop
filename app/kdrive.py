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
                  params={"per_page": 200, "order_by": "last_modified_at", "order": "desc"},
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
