"""Application settings loaded from environment / .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- app ---
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"

    # --- data stores ---
    database_url: str = "postgresql+asyncpg://nettwin:nettwin@localhost:5432/nettwin"
    redis_url: str = "redis://localhost:6379/0"

    # --- discovery ---
    discovery_subnet: str = "10.0.0.0/24"
    discovery_interval_seconds: int = 300
    discovery_source: str = "simulator"  # live | simulator

    # --- snmp ---
    snmp_community: str = "public"
    snmp_timeout_seconds: float = 2.0
    snmp_retries: int = 1

    # --- monitoring ---
    monitor_interval_seconds: int = 15
    alert_latency_threshold_ms: float = 200.0
    alert_packet_loss_threshold_pct: float = 10.0

    # --- anomaly detection ---
    anomaly_detection_enabled: bool = True
    anomaly_min_samples: int = 10
    anomaly_z_threshold: float = 3.5

    # --- forecasting / capacity planning ---
    forecast_enabled: bool = True
    forecast_interval_seconds: int = 300
    forecast_window_points: int = 30  # samples per series fed to the fit
    forecast_horizon_minutes: int = 720  # how far ahead to project (12 h)
    forecast_capacity_bps: float = 1_000_000_000.0  # 1 Gbps line-rate assumption


@lru_cache
def get_settings() -> Settings:
    return Settings()
