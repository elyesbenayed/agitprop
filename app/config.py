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
    # Banque de posts / kDrive (Infomaniak) : token API avec le périmètre « drive »
    kdrive_token: str = ""
    kdrive_drive_id: str = ""
    kdrive_folder_id: str = "1"          # 1 = racine du kDrive
    # Écriture sur kDrive (dépôt des visuels déclinés + liens publics) : désactivée par défaut
    kdrive_ecriture: bool = False
    kdrive_public_folder_id: str = ""    # dossier kDrive où déposer les visuels publics
    # Adresse publique de l'app : sert à construire les URL des médias stockés localement
    public_base_url: str = "http://localhost:8000"
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
