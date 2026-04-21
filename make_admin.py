import sys
from database import SessionLocal, engine
import models
models.Base.metadata.create_all(bind=engine)
def make_admin(telegram_id: str):
    db = SessionLocal()
    user = db.query(models.User).filter(models.User.telegram_id == str(telegram_id)).first()
    if not user:
        print(f"User {telegram_id} not found. Login to the app first.")
        return
    user.tier = models.UserTier.ADMIN
    db.commit()
    print(f"Done! @{user.username} is now ADMIN")
    db.close()
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python make_admin.py YOUR_TELEGRAM_ID")
    else:
        make_admin(sys.argv[1])
