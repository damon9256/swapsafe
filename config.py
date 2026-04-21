from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///./swapsafe.db"
    REDIS_URL: str = "redis://localhost:6379"
    BOT_TOKEN: str = "YOUR_BOT_TOKEN_HERE"
    BOT_USERNAME: str = "swapsafe_bot"
    SECRET_KEY: str = "swapsafe-secret-key-change-this"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 720
    PLATFORM_FEE_PERCENT: float = 0.0
    WITHDRAWAL_FEE_USDT: float = 0.0
    MAX_ADS_PER_HOUR: int = 3
    PIN_MAX_ATTEMPTS: int = 3
    PIN_LOCKOUT_MINUTES: int = 15
    TRADE_AUTO_DISPUTE_MINUTES: int = 15
    MAX_WITHDRAWAL_PER_DAY: float = 50000.0
    NEW_USER_MAX_TRADE_INR: float = 50000.0
    TRON_WEBHOOK_SECRET: str = "tron-secret"
    ETH_WEBHOOK_SECRET: str = "eth-secret"
    BSC_WEBHOOK_SECRET: str = "bsc-secret"
    POLYGON_WEBHOOK_SECRET: str = "polygon-secret"
    SOLANA_WEBHOOK_SECRET: str = "solana-secret"
    ARBITRUM_WEBHOOK_SECRET: str = "arbitrum-secret"
    OPTIMISM_WEBHOOK_SECRET: str = "optimism-secret"

    class Config:
        env_file = ".env"

settings = Settings()
