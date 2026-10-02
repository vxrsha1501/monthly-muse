"""Profile, preferences, learned values, data export and deletion."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.arms import arm_catalog
from app.core.deps import get_current_user, get_db
from app.db.models import FeedbackEvent, GeneratedMessage, ToneStat, User, UserPreferences
from app.db.session import blob_to_vec
from app.schemas.auth import MeUpdate, UserOut
from app.schemas.content import LearnedPreferences, PreferencesIn, PreferencesOut
from app.services import account_service

router = APIRouter(prefix="/me", tags=["me"])


@router.get("", response_model=UserOut)
def get_me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(user)


@router.patch("", response_model=UserOut)
def patch_me(data: MeUpdate, user: User = Depends(get_current_user),
             db: Session = Depends(get_db)) -> UserOut:
    return UserOut.model_validate(account_service.update_profile(db, user, data))


@router.get("/preferences", response_model=PreferencesOut)
def get_prefs(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> PreferencesOut:
    prefs = account_service.get_preferences(db, user)
    return PreferencesOut.model_validate(prefs)


@router.put("/preferences", response_model=PreferencesOut)
def put_prefs(data: PreferencesIn, user: User = Depends(get_current_user),
              db: Session = Depends(get_db)) -> PreferencesOut:
    prefs = account_service.update_preferences(db, user, data)
    return PreferencesOut.model_validate(prefs)


@router.get("/preferences/learned", response_model=LearnedPreferences)
def learned(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> LearnedPreferences:
    """Settings > 'Your learned priorities' (Section 5.6.3 / 7.4)."""
    from app.ai.scoring import DEFAULT_WEIGHTS, weights_from_dict
    prefs = db.get(UserPreferences, user.id) or UserPreferences(user_id=user.id)
    weights = weights_from_dict(prefs.scoring_weights)
    u = blob_to_vec(prefs.preference_vector)
    tone_counts = (prefs.tone_counts or {}).get("__all__", {})
    arms = list(db.scalars(select(ToneStat).where(ToneStat.user_id == user.id)).all())
    n_events = db.scalar(select(func.count(FeedbackEvent.id)).where(FeedbackEvent.user_id == user.id)) or 0
    return LearnedPreferences(
        scoring_weights={k: round(v, 3) for k, v in zip(
            ("R", "T", "P", "N", "Len", "Hist"), weights)},
        learned_length={"mean": prefs.length_mean, "std": prefs.length_std, "n": prefs.length_n},
        preference_vector_norm=round(float((u ** 2).sum() ** 0.5), 3) if u is not None else None,
        tone_preferences=[{"tone": t, "count": c, "share": round(c / max(sum(tone_counts.values()), 1), 2)}
                          for t, c in sorted(tone_counts.items(), key=lambda kv: -kv[1])],
        arm_stats=[{"tone": a.tone, "structure": a.structure, "alpha": round(a.alpha, 2),
                    "beta": round(a.beta, 2), "mean": round(a.alpha / (a.alpha + a.beta), 3),
                    "n_shown": a.n_shown, "n_selected": a.n_selected} for a in arms],
        n_feedback_events=n_events,
        learning_paused=bool(prefs.learning_paused),
    )


@router.post("/preferences/reset-learning")
def reset(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    return account_service.reset_learning(db, user)


@router.get("/export")
def export(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    return account_service.export_data(db, user)


@router.delete("", status_code=204)
def delete_me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    account_service.delete_account(db, user)
