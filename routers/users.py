from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from security import get_current_user
import models

router = APIRouter()

@router.get("/profile/{user_id}")
def get_profile(user_id: int, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    reviews = db.query(models.Review).filter(models.Review.reviewed_id == user_id).order_by(models.Review.created_at.desc()).limit(10).all()
    return {
        "id": user.id, "username": user.username, "first_name": user.first_name,
        "tier": user.tier, "total_trades": user.total_trades, "completed_trades": user.completed_trades,
        "completion_rate": user.completion_rate, "avg_rating": user.avg_rating,
        "avg_release_time": user.avg_release_time, "total_volume": user.total_volume,
        "member_since": str(user.created_at),
        "reviews": [{"rating": r.rating, "comment": r.comment, "created_at": str(r.created_at)} for r in reviews]
    }

@router.get("/me")
def get_me(current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    return get_profile(current_user.id, db)
