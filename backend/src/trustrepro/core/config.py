from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from trustrepro.domain.money import CURRENCY, DEFAULT_USD_TO_CNY, from_usd


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TRUSTREPRO_",
        env_file=Path(__file__).resolve().parents[4] / ".env",
        extra="ignore",
    )
    database_url: str = "postgresql+psycopg://trustrepro:trustrepro_dev@127.0.0.1:5432/trustrepro"
    allowed_origins: list[str] = ["http://127.0.0.1:5173", "http://localhost:5173"]
    model_base_url: str = "http://host.docker.internal:8317/v1"
    model_api_key: SecretStr = SecretStr("")
    model_name: str = "gpt-6.1-sol"
    bootstrap_model: bool = True
    frontend_root: Path | None = None
    desktop_token_file: Path | None = None
    desktop_windows_loopback: bool = False
    # Missing unit identifies legacy environment files. Public/configured prices are CNY.
    model_price_currency: Literal["USD", "CNY"] = "USD"
    usd_to_cny_rate: Decimal = Field(default=DEFAULT_USD_TO_CNY, gt=0, allow_inf_nan=False)
    model_input_cost_per_million: Decimal = Field(default=1, ge=0, allow_inf_nan=False)
    model_output_cost_per_million: Decimal = Field(default=5, ge=0, allow_inf_nan=False)
    codex_binary: str = "codex"
    codex_version: str = "0.154.0"
    internal_api_url: str = "http://api:8000"
    model_unknown_reservation: Decimal = Field(default=Decimal("0.15"), gt=0, allow_inf_nan=False)
    sandbox_image: str = "trustrepro-sandbox:local"
    runtime_volume: str = "trustrepro-runtime"
    sandbox_network: str = "trustrepro-sandbox"
    egress_proxy: str = "http://egress:3128"
    var_root: Path = Path(__file__).resolve().parents[4] / "var"
    secrets_root: Path = Path(__file__).resolve().parents[4] / "var" / "api-secrets"

    def cny(self, value):
        return (
            from_usd(value, self.usd_to_cny_rate)
            if self.model_price_currency == "USD"
            else Decimal(str(value))
        )

    @property
    def model_pricing(self):
        return {
            "currency": CURRENCY,
            "inputRate": str(self.cny(self.model_input_cost_per_million)),
            "outputRate": str(self.cny(self.model_output_cost_per_million)),
        }

    @property
    def unknown_reservation_cny(self):
        return self.cny(self.model_unknown_reservation)


@lru_cache
def get_settings() -> Settings:
    return Settings()
