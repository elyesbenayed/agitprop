"""Modèles SQLAlchemy : comptes, posts, insights, utilisateurs, audit."""
from datetime import datetime

from sqlalchemy import (Boolean, Column, DateTime, Float, ForeignKey, Integer,
                        String, Text, create_engine)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

from .config import settings

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False)
Base = declarative_base()


class User(Base):
    """Utilisateur du dashboard : admin national ou correspondant départemental."""
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(String, default="correspondent")  # "admin" | "correspondent"
    department_code = Column(String, ForeignKey("accounts.department_code"), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Account(Base):
    """Un compte Instagram départemental."""
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True)
    department_code = Column(String, unique=True, nullable=False)   # "01" … "976"
    department_name = Column(String, nullable=False)
    ig_username = Column(String, nullable=True)
    ig_user_id = Column(String, nullable=True)        # ID Instagram professionnel
    access_token_enc = Column(Text, nullable=True)    # token IG chiffré (Fernet)
    token_expires_at = Column(DateTime, nullable=True)
    fb_page_id = Column(String, nullable=True)        # Page Facebook liée
    fb_page_token_enc = Column(Text, nullable=True)   # token de Page chiffré
    status = Column(String, default="unlinked")       # unlinked|active|token_expired|suspended|compromised
    posts_today = Column(Integer, default=0)
    posts_today_date = Column(String, default="")
    insights = relationship("InsightSnapshot", back_populates="account")


class Post(Base):
    """Un post national ou départemental, mono ou multi-comptes."""
    __tablename__ = "posts"
    id = Column(Integer, primary_key=True)
    caption = Column(Text, nullable=False)
    media_url = Column(String, nullable=False)        # URL publique de l'image/vidéo
    media_type = Column(String, default="IMAGE")      # IMAGE | REELS | CAROUSEL | STORIES
    platform = Column(String, default="instagram")    # instagram | facebook | both
    scheduled_at = Column(DateTime, nullable=True)    # None = immédiat
    created_by = Column(Integer, ForeignKey("users.id"))
    is_national = Column(Boolean, default=False)      # post coordonné national
    created_at = Column(DateTime, default=datetime.utcnow)
    targets = relationship("PostTarget", back_populates="post")


class PostTarget(Base):
    """Statut d'un post pour un compte cible donné."""
    __tablename__ = "post_targets"
    id = Column(Integer, primary_key=True)
    post_id = Column(Integer, ForeignKey("posts.id"))
    account_id = Column(Integer, ForeignKey("accounts.id"))
    platform = Column(String, default="instagram")    # instagram | facebook
    status = Column(String, default="pending")        # pending|published|failed|rate_limited
    ig_media_id = Column(String, nullable=True)
    error = Column(Text, nullable=True)
    published_at = Column(DateTime, nullable=True)
    post = relationship("Post", back_populates="targets")
    account = relationship("Account")


class InsightSnapshot(Base):
    """Photo quotidienne des métriques d'un compte (collecte nocturne)."""
    __tablename__ = "insight_snapshots"
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id"))
    date = Column(String, nullable=False)             # "2026-06-10"
    followers = Column(Integer, default=0)
    reach = Column(Integer, default=0)
    impressions = Column(Integer, default=0)
    profile_views = Column(Integer, default=0)
    engagement = Column(Float, default=0.0)
    account = relationship("Account", back_populates="insights")


class AuditLog(Base):
    """Journal d'audit : toute action sensible est tracée."""
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    user_email = Column(String)
    action = Column(String)        # login, publish, token_update, account_suspend, …
    detail = Column(Text)
    ip = Column(String, nullable=True)


def init_db():
    Base.metadata.create_all(engine)
    # migrations legeres pour les bases existantes
    from sqlalchemy import text
    with engine.connect() as conn:
        for stmt in (
            "ALTER TABLE accounts ADD COLUMN fb_page_id VARCHAR",
            "ALTER TABLE accounts ADD COLUMN fb_page_token_enc TEXT",
            "ALTER TABLE posts ADD COLUMN platform VARCHAR DEFAULT 'instagram'",
            "ALTER TABLE post_targets ADD COLUMN platform VARCHAR DEFAULT 'instagram'",
        ):
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                pass  # colonne deja presente


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
