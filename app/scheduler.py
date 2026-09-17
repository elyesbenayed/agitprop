"""Tâches de fond : publication différée, collecte nocturne des insights,
rafraîchissement des tokens, détection d'anomalies."""
import logging
from datetime import date, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from . import instagram_api as ig
from .database import Account, InsightSnapshot, Post, PostTarget, SessionLocal
from .security import decrypt_token, encrypt_token

log = logging.getLogger("igmanager")
scheduler = AsyncIOScheduler()


def _check_daily_quota(db, account: Account) -> bool:
    """Quota local : 25 publications / 24 h / compte (limite Meta)."""
    today = date.today().isoformat()
    if account.posts_today_date != today:
        account.posts_today, account.posts_today_date = 0, today
        db.commit()
    return account.posts_today < 25


async def process_pending_posts():
    """Publie les posts en attente dont l'heure est arrivée."""
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        targets = (db.query(PostTarget).join(Post)
                   .filter(PostTarget.status == "pending")
                   .filter((Post.scheduled_at.is_(None)) | (Post.scheduled_at <= now))
                   .order_by(Post.scheduled_at, PostTarget.id).limit(400).all())
        for t in targets:
            acc = t.account
            if acc.status != "active":
                t.status, t.error = "failed", f"Compte {acc.department_code} non actif"
                db.commit()
                continue
            if t.platform != "facebook" and not _check_daily_quota(db, acc):
                t.status, t.error = "rate_limited", "Quota 25 posts/24h atteint"
                db.commit()
                continue
            try:
                if t.platform == "facebook":
                    if not (acc.fb_page_id and acc.fb_page_token_enc):
                        t.status = "failed"
                        t.error = f"Page Facebook non liee ({acc.department_code})"
                        db.commit()
                        continue
                    token = decrypt_token(acc.fb_page_token_enc)
                    media_id = await ig.publish_facebook(
                        acc.fb_page_id, token, t.post.media_url,
                        t.post.caption, t.post.media_type)
                else:
                    if not acc.access_token_enc:
                        t.status = "failed"
                        t.error = f"Compte Instagram non lie ({acc.department_code})"
                        db.commit()
                        continue
                    token = decrypt_token(acc.access_token_enc)
                    media_id = await ig.publish_media(
                        acc.ig_user_id, token, t.post.media_url,
                        t.post.caption, t.post.media_type)
                    acc.posts_today += 1
                t.status, t.ig_media_id, t.published_at = "published", media_id, now
            except ig.InstagramAPIError as e:
                t.status, t.error = "failed", str(e)
                if e.is_auth:
                    acc.status = "token_expired"
                    log.warning("ALERTE : token invalide pour %s", acc.department_code)
            db.commit()
    finally:
        db.close()


async def collect_insights():
    """Collecte nocturne des métriques de chaque compte actif (1 snapshot/jour)."""
    db = SessionLocal()
    try:
        today = date.today().isoformat()
        for acc in db.query(Account).filter(Account.status == "active").all():
            if not acc.access_token_enc:
                continue  # compte lie uniquement a Facebook
            try:
                token = decrypt_token(acc.access_token_enc)
                data = await ig.get_account_insights(acc.ig_user_id, token)
                db.add(InsightSnapshot(
                    account_id=acc.id, date=today,
                    followers=data.get("followers", 0),
                    reach=data.get("reach", 0),
                    profile_views=data.get("profile_views", 0)))
                db.commit()
            except ig.InstagramAPIError as e:
                if e.is_auth:
                    acc.status = "token_expired"
                    db.commit()
                log.warning("Insights KO pour %s : %s", acc.department_code, e)
    finally:
        db.close()


async def refresh_expiring_tokens():
    """Rafraîchit les tokens qui expirent sous 10 jours (validité 60 jours)."""
    db = SessionLocal()
    try:
        limit = datetime.utcnow() + timedelta(days=10)
        accounts = (db.query(Account)
                    .filter(Account.status == "active",
                            Account.access_token_enc.isnot(None),
                            Account.token_expires_at.isnot(None),
                            Account.token_expires_at <= limit).all())
        for acc in accounts:
            try:
                old = decrypt_token(acc.access_token_enc)
                new_token, expires = await ig.refresh_long_lived_token(old)
                acc.access_token_enc = encrypt_token(new_token)
                acc.token_expires_at = expires
                db.commit()
                log.info("Token rafraîchi : %s", acc.department_code)
            except Exception as e:
                acc.status = "token_expired"
                db.commit()
                log.error("ALERTE : échec refresh token %s : %s", acc.department_code, e)
    finally:
        db.close()


def start_scheduler():
    scheduler.add_job(process_pending_posts, "interval", minutes=1)
    scheduler.add_job(collect_insights, "cron", hour=3, minute=0)
    scheduler.add_job(refresh_expiring_tokens, "cron", hour=4, minute=0)
    scheduler.start()
