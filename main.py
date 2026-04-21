from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn
import os
from database import engine, Base
import models
from routers import auth, wallet, ads, trades, users, admin, notifications

# Create database tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="SwapSafe P2P", version="1.0.0")

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers
app.include_router(auth.router,          prefix="/api/auth",          tags=["Auth"])
app.include_router(wallet.router,        prefix="/api/wallet",        tags=["Wallet"])
app.include_router(ads.router,           prefix="/api/ads",           tags=["Ads"])
app.include_router(trades.router,        prefix="/api/trades",        tags=["Trades"])
app.include_router(users.router,         prefix="/api/users",         tags=["Users"])
app.include_router(admin.router,         prefix="/api/admin",         tags=["Admin"])
app.include_router(notifications.router, prefix="/api/notifications", tags=["Notifications"])

# --- FRONTEND CONFIGURATION ---

# 1. Serve static files (CSS, JS, Images) if they exist in a folder named 'static'
# If you don't have a static folder yet, this won't crash, but it's good practice.
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

# 2. Serve the index.html file at the root URL
@app.get("/")
def serve_home():
    return FileResponse("index.html")

# --- END FRONTEND CONFIGURATION ---

@app.get("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    # Note: using "main:app" string is required for 'reload=True' to work
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
