from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database import get_db
from security import get_current_user
import models

router = APIRouter()

@router.get("/")
def get_notifications(current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    notifs = db.query(models.Notification).filter(models.Notification.user_id == current_user.id).order_by(models.Notification.created_at.desc()).limit(50).all()
    return [{"id": n.id, "title": n.title, "body": n.body, "type": n.notif_type, "is_read": n.is_read, "created_at": str(n.created_at)} for n in notifs]

@router.post("/read-all")
def mark_all_read(current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.query(models.Notification).filter(models.Notification.user_id == current_user.id, models.Notification.is_read == False).update({"is_read": True})
    db.commit()
    return {"message": "All read"}

@router.get("/unread-count")
def unread_count(current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    count = db.query(models.Notification).filter(models.Notification.user_id == current_user.id, models.Notification.is_read == False).count()
    return {"count": count}
