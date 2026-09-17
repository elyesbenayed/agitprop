"""Dépendances FastAPI partagées (session utilisateur, rôle admin)."""
from fastapi import Depends, HTTPException, Request

from .database import User, get_db


def current_user(request: Request, db=Depends(get_db)) -> User:
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(401, "Non connecte")
    user = db.get(User, uid)
    if not user or not user.is_active:
        raise HTTPException(401, "Compte desactive")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Reserve a l'equipe nationale")
    return user
