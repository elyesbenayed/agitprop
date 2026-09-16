"""Client API Meta (Graph API) : Instagram + Pages Facebook.

Prérequis côté Meta (à faire une fois) :
  1. Chaque compte départemental converti en compte PROFESSIONNEL (Business).
  2. Une app Meta (developers.facebook.com) avec les permissions :
     instagram_basic, instagram_content_publish, instagram_manage_insights,
     pages_read_engagement (si liaison via Page Facebook).
  3. App Review validée par Meta pour ces permissions (mode Live).
  4. Tokens longue durée (60 jours) générés par compte, stockés chiffrés.

Limites Meta (juin 2026) :
  - 25 publications max / 24 h / compte (Reels et Stories comptent dedans).
  - ~200 appels API / heure / compte.
"""
import asyncio
import json
from datetime import datetime, timedelta

import httpx

from .config import GRAPH_URL, settings


class InstagramAPIError(Exception):
    def __init__(self, message: str, code: int | None = None, is_auth: bool = False):
        super().__init__(message)
        self.code = code
        self.is_auth = is_auth  # token invalide/expiré → alerte sécurité


IG_GRAPH_URL = f"https://graph.instagram.com/{settings.meta_graph_version}"


def is_instagram_login_token(token: str) -> bool:
    """True pour un token issu de la « connexion Instagram business » (IG…),
    False pour un token Facebook (EAA…). META_API=instagram|facebook force le choix."""
    mode = (settings.meta_api or "auto").lower()
    if mode == "instagram":
        return True
    if mode == "facebook":
        return False
    return token.startswith("IG")


def _base(token: str) -> str:
    """Hôte Graph à utiliser pour ce token."""
    return IG_GRAPH_URL if is_instagram_login_token(token) else GRAPH_URL


def _raise_for_error(data: dict):
    if "error" in data:
        err = data["error"]
        code = err.get("code")
        # codes 190/102 = token invalide ou expiré
        raise InstagramAPIError(err.get("message", "Erreur API Meta"),
                                code=code, is_auth=code in (190, 102))


async def _get(client: httpx.AsyncClient, url: str, params: dict) -> dict:
    r = await client.get(url, params=params)
    data = r.json()
    _raise_for_error(data)
    return data


async def _post(client: httpx.AsyncClient, url: str, data: dict) -> dict:
    r = await client.post(url, data=data)
    out = r.json()
    _raise_for_error(out)
    return out


def split_urls(media_url: str) -> list[str]:
    """Découpe une liste d'URLs (une par ligne ou séparées par |)."""
    seps = media_url.replace("|", "\n")
    return [u.strip() for u in seps.splitlines() if u.strip()]


async def publish_media(ig_user_id: str, access_token: str, media_url: str,
                        caption: str, media_type: str = "IMAGE") -> str:
    """Publie un média en deux étapes (conteneur puis publication).

    Pour un carrousel (CAROUSEL) : media_url contient 2 à 10 URLs d'images,
    une par ligne. Retourne l'ID du média publié.
    """
    base = _base(access_token)
    async with httpx.AsyncClient(timeout=120) as client:
        params: dict = {"caption": caption, "access_token": access_token}
        if media_type == "IMAGE":
            params["image_url"] = media_url
        elif media_type == "REELS":
            params.update({"media_type": "REELS", "video_url": media_url})
        elif media_type == "STORIES":
            params.update({"media_type": "STORIES", "image_url": media_url})
        elif media_type == "CAROUSEL":
            urls = split_urls(media_url)
            if not 2 <= len(urls) <= 10:
                raise InstagramAPIError(
                    f"Un carrousel demande 2 à 10 images ({len(urls)} fournies)")
            children = []
            for u in urls:
                child = await _post(client, f"{base}/{ig_user_id}/media",
                                    {"image_url": u, "is_carousel_item": "true",
                                     "access_token": access_token})
                children.append(child["id"])
            params.update({"media_type": "CAROUSEL",
                           "children": ",".join(children)})
        else:
            raise InstagramAPIError(f"Type non géré : {media_type}")

        container = await _post(client, f"{base}/{ig_user_id}/media", params)
        container_id = container["id"]

        # Les vidéos demandent un délai de traitement côté Meta.
        if media_type == "REELS":
            for _ in range(30):
                status = await _get(client, f"{base}/{container_id}",
                                    {"fields": "status_code", "access_token": access_token})
                if status.get("status_code") == "FINISHED":
                    break
                await asyncio.sleep(5)

        result = await _post(client, f"{base}/{ig_user_id}/media_publish",
                             {"creation_id": container_id, "access_token": access_token})
        return result["id"]


def whoami_sync(access_token: str) -> dict:
    """Identifiant + nom du compte Instagram professionnel associe a ce token.

    Connexion Instagram business : GET /me?fields=user_id,username.
    Appel synchrone (utilise depuis les routes non-async de liaison de compte)."""
    with httpx.Client(timeout=20) as client:
        r = client.get(f"{_base(access_token)}/me",
                       params={"fields": "user_id,username,id",
                               "access_token": access_token})
        data = r.json()
        _raise_for_error(data)
        return {"user_id": str(data.get("user_id") or data.get("id") or ""),
                "username": data.get("username") or ""}


async def get_publishing_quota(ig_user_id: str, access_token: str) -> int:
    """Nombre de publications déjà utilisées sur les dernières 24 h (quota 25)."""
    async with httpx.AsyncClient(timeout=30) as client:
        data = await _get(client, f"{_base(access_token)}/{ig_user_id}/content_publishing_limit",
                          {"access_token": access_token})
        return data["data"][0]["quota_usage"]


async def get_account_insights(ig_user_id: str, access_token: str) -> dict:
    """Métriques du compte : followers, reach, vues de profil."""
    base = _base(access_token)
    async with httpx.AsyncClient(timeout=30) as client:
        profile = await _get(client, f"{base}/{ig_user_id}",
                             {"fields": "followers_count,media_count,username",
                              "access_token": access_token})
        out = {"followers": profile.get("followers_count", 0),
               "username": profile.get("username"),
               "media_count": profile.get("media_count", 0),
               "reach": 0, "profile_views": 0}
        # Métriques une par une : une métrique refusée (permission, version d'API)
        # n'empêche pas d'enregistrer les autres. profile_views exige metric_type=total_value.
        for metric, extra in (("reach", {}), ("profile_views", {"metric_type": "total_value"})):
            try:
                data = await _get(client, f"{base}/{ig_user_id}/insights",
                                  {"metric": metric, "period": "day",
                                   "access_token": access_token, **extra})
            except InstagramAPIError as e:
                if e.is_auth:
                    raise
                continue
            for m in data.get("data", []):
                if "total_value" in m:
                    out[m["name"]] = m["total_value"].get("value", 0)
                else:
                    values = m.get("values", [])
                    out[m["name"]] = values[-1]["value"] if values else 0
        return out


async def get_media_insights(media_id: str, access_token: str) -> dict:
    """Métriques d'un post : reach, likes, commentaires, partages, enregistrements."""
    async with httpx.AsyncClient(timeout=30) as client:
        data = await _get(client, f"{_base(access_token)}/{media_id}/insights",
                          {"metric": "reach,likes,comments,shares,saved",
                           "access_token": access_token})
        return {m["name"]: (m["values"][-1]["value"] if m.get("values") else 0)
                for m in data.get("data", [])}


async def refresh_long_lived_token(access_token: str) -> tuple[str, datetime]:
    """Rafraîchit un token longue durée (validité 60 jours).

    Connexion Instagram business : refresh_access_token (sans clé secrète).
    Connexion Facebook : oauth/access_token avec fb_exchange_token.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        if is_instagram_login_token(access_token):
            data = await _get(client, "https://graph.instagram.com/refresh_access_token",
                              {"grant_type": "ig_refresh_token",
                               "access_token": access_token})
        else:
            data = await _get(client, f"{GRAPH_URL}/oauth/access_token",
                              {"grant_type": "fb_exchange_token",
                               "client_id": settings.meta_app_id,
                               "client_secret": settings.meta_app_secret,
                               "fb_exchange_token": access_token})
        expires = datetime.utcnow() + timedelta(seconds=data.get("expires_in", 60 * 86400))
        return data["access_token"], expires


async def publish_facebook(page_id: str, page_token: str, media_url: str,
                           caption: str, media_type: str = "IMAGE") -> str:
    """Publie sur une Page Facebook (token de Page requis).

    IMAGE -> photo ; STORIES -> story de Page ; REELS -> video ;
    CAROUSEL -> album multi-photos. Permission requise : pages_manage_posts.
    """
    async with httpx.AsyncClient(timeout=120) as client:
        if media_type == "IMAGE":
            r = await _post(client, f"{GRAPH_URL}/{page_id}/photos",
                            {"url": media_url, "message": caption,
                             "access_token": page_token})
            return r.get("post_id") or r["id"]
        if media_type == "STORIES":
            # story de Page : photo non publiee puis endpoint photo_stories
            p = await _post(client, f"{GRAPH_URL}/{page_id}/photos",
                            {"url": media_url, "published": "false",
                             "access_token": page_token})
            r = await _post(client, f"{GRAPH_URL}/{page_id}/photo_stories",
                            {"photo_id": p["id"], "access_token": page_token})
            return r.get("post_id") or p["id"]
        if media_type == "REELS":
            r = await _post(client, f"{GRAPH_URL}/{page_id}/videos",
                            {"file_url": media_url, "description": caption,
                             "access_token": page_token})
            return r["id"]
        if media_type == "CAROUSEL":
            urls = split_urls(media_url)
            if len(urls) < 2:
                raise InstagramAPIError("Un album demande au moins 2 images")
            media_ids = []
            for u in urls:
                p = await _post(client, f"{GRAPH_URL}/{page_id}/photos",
                                {"url": u, "published": "false",
                                 "access_token": page_token})
                media_ids.append(p["id"])
            params = {"message": caption, "access_token": page_token}
            for i, mid in enumerate(media_ids):
                params[f"attached_media[{i}]"] = json.dumps({"media_fbid": mid})
            r = await _post(client, f"{GRAPH_URL}/{page_id}/feed", params)
            return r["id"]
        raise InstagramAPIError(f"Type non gere pour Facebook : {media_type}")
