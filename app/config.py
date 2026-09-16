"""Configuration centrale de l'application."""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_graph_version: str = "v23.0"
    # auto : graph.instagram.com pour les tokens « connexion Instagram business »
    # (préfixe IG…), graph.facebook.com pour les tokens Facebook (préfixe EAA…).
    meta_api: str = "auto"  # auto | instagram | facebook
    fernet_key: str = ""
    session_secret: str = "dev-secret"
    https_only: bool = False  # HTTPS_ONLY=1 en production : cookie de session "Secure"
    database_url: str = "sqlite:///./igmanager.db"
    admin_email: str = "admin@parti.fr"
    admin_password: str = "changez_moi_immediatement"

    # Limites Meta (juin 2026) : 25 publications / 24 h / compte,
    # ~200 appels API / heure / compte.
    publish_daily_limit: int = 25
    api_hourly_limit: int = 200

    class Config:
        env_file = ".env"


settings = Settings()
GRAPH_URL = f"https://graph.facebook.com/{settings.meta_graph_version}"
