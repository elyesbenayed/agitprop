# Mise en ligne d'Agitprop chez Infomaniak

**Pourquoi un VPS.** L'hébergement Web mutualisé Infomaniak (celui de `dnum.lafrancehumaniste.fr`)
exécute PHP et des « sites Node.js », pas d'application Python. La doc Infomaniak renvoie les
applications Python (Django, Flask, FastAPI…) vers un VPS Lite / VPS Cloud
(FAQ 516 « Installer Django ») ou un Serveur Cloud managé (FAQ 2171, ports 4000-4009).
Un VPS Lite suffit largement pour 101 comptes.

## 1. Commander le serveur (à faire par vous, 5 min)

manager.infomaniak.com → Public Cloud / VPS → **VPS Lite**, image **Ubuntu 24.04**,
authentification par clé SSH (ou mot de passe root). Notez l'**adresse IP**.

## 2. Sous-domaine (2 min)

Manager → Domaine `lafrancehumaniste.fr` → Zone DNS → enregistrement **A** :
`agitprop` → IP du VPS (nom libre : `socials`, `reseaux`…). Attendre quelques minutes.

## 3. Envoyer les sources (depuis ce PC, PowerShell, dans le dossier IGManager)

```
ssh root@IP "mkdir -p /root/agitprop-src"
scp -r app templates static requirements.txt deploy root@IP:/root/agitprop-src/
```

Le `.env` local et la base locale ne partent **pas** : le serveur génère ses propres secrets.

## 4. Installer (5 min)

```
ssh root@IP
cd /root/agitprop-src
bash deploy/install-vps.sh agitprop.lafrancehumaniste.fr votre@email.fr
```

Le script installe Python, nginx, le certificat HTTPS Let's Encrypt, le service systemd
(redémarrage automatique) et le pare-feu. À la fin : `https://agitprop.lafrancehumaniste.fr`.
Identifiants admin dans `/opt/agitprop/IDENTIFIANTS.txt` : à ranger dans le gestionnaire
de mots de passe, puis supprimer le fichier.

## 5. Ensuite

- Renseigner `META_APP_ID` et `META_APP_SECRET` dans `/opt/agitprop/.env`, puis `systemctl restart agitprop`.
- Mettre à jour l'app : refaire les étapes 3 et 4 (les données sont conservées).
- Sauvegarder régulièrement `/opt/agitprop/data/agitprop.db` et `/opt/agitprop/.env`.
- Journal : `journalctl -u agitprop -f`.
