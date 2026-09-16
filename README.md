# Agitprop — Gestion des 101 comptes Instagram départementaux

Application FastAPI pour publier (posts partagés nationaux ou départementaux),
collecter les statistiques et superviser la sécurité des 101 comptes via l'API
officielle Meta (Instagram Platform).

## Architecture

```
app/
  main.py           # API + dashboard (auth, RBAC, publication, stats, audit)
  instagram_api.py  # Client Meta Graph API (publication, insights, refresh token)
  scheduler.py      # Tâches de fond : publication différée, insights nocturnes, refresh tokens
  database.py       # Modèles : Account, Post, PostTarget, InsightSnapshot, User, AuditLog
  security.py       # Chiffrement Fernet des tokens, bcrypt, journal d'audit, RBAC
  departments.py    # Les 101 départements
templates/          # Dashboard et login
```

## Prérequis côté Meta (à faire AVANT la distribution des comptes)

1. **Convertir chaque compte en compte professionnel** (Business) :
   Instagram → Paramètres → Type de compte → Passer au compte professionnel.
   L'API officielle ne fonctionne pas avec des comptes personnels.
2. **Créer une app Meta** sur developers.facebook.com (type Business) avec les
   permissions : `instagram_basic`, `instagram_content_publish`,
   `instagram_manage_insights` (+ `pages_read_engagement` si liaison via Page Facebook).
3. **Passer l'App Review Meta** pour ces permissions (mode Live). Compter
   plusieurs jours/semaines — à anticiper.
4. **Générer un token longue durée (60 jours) par compte** et l'enregistrer
   via `POST /api/accounts/{code}/token`. L'app rafraîchit ensuite les tokens
   automatiquement chaque nuit avant expiration.

Deux variantes d'intégration possibles :
- **API avec connexion Facebook** : compte pro lié à une Page Facebook (fonctions complètes).
- **API avec connexion Instagram** : sans Page Facebook (publication + insights disponibles depuis 2024-2025).

## Limites Meta à connaître (juin 2026)

- **25 publications max / 24 h / compte** (Reels et Stories comptent dedans) — l'app applique ce quota localement.
- **~200 appels API / heure / compte** — la collecte d'insights est étalée la nuit.
- Les médias doivent être accessibles via une **URL publique HTTPS** (hébergez-les sur votre serveur ou un bucket S3).

## Installation

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Remplir .env : META_APP_ID, META_APP_SECRET, FERNET_KEY, ADMIN_PASSWORD
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # → FERNET_KEY
uvicorn app.main:app --reload
```

Au premier démarrage : les 101 départements sont créés en base et le compte
admin défini dans `.env` est généré. Dashboard sur http://localhost:8000.

## Rôles

| Rôle | Droits |
|---|---|
| `admin` (équipe nationale) | Tous les comptes, posts nationaux (`targets: "all"`), gel de compte, journal d'audit |
| `correspondent` | Uniquement son département : publication, stats, mise à jour du token |

## Endpoints principaux

| Méthode | Route | Description |
|---|---|---|
| POST | `/api/posts` | Publier (immédiat ou planifié) sur 1, N ou 101 comptes |
| GET | `/api/posts/{id}/status` | Statut de publication par département |
| GET | `/api/stats/overview` | Totaux nationaux + top départements |
| GET | `/api/stats/{code}` | Série temporelle d'un département |
| POST | `/api/accounts/{code}/token` | Enregistrer/mettre à jour un token (chiffré) |
| POST | `/api/accounts/{code}/suspend` | **Kill switch** : gel immédiat d'un compte (admin) |
| GET | `/api/audit` | Journal d'audit (admin) |

## Sécurité intégrée

- Tokens chiffrés en base (Fernet/AES) — jamais en clair, refus de démarrer sans clé.
- Mots de passe hachés bcrypt ; RBAC strict par département.
- Journal d'audit de toute action sensible (login, publication, token, gel).
- Détection automatique des tokens invalides (codes 190/102) → compte marqué
  `token_expired` + alerte dans les logs.
- Kill switch admin : gel d'un compte + purge locale de son token.

En production : HTTPS obligatoire (`https_only=True` dans main.py), PostgreSQL,
reverse proxy (nginx/caddy), sauvegardes chiffrées de la base, MFA sur les
comptes Instagram eux-mêmes (voir protocole de sécurité joint).
