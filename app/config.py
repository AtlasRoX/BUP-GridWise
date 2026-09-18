import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    nvidia_nim_api_key: str = Field(
        default="",
        alias="NVIDIA_NIM_API_KEY",
        description="API key for NVIDIA NIM"
    )
    # Also support generic LLM_API_KEY as fallback
    llm_api_key: str = Field(
        default="",
        alias="LLM_API_KEY",
        description="Generic LLM API key fallback"
    )
    nvidia_nim_base_url: str = Field(
        default="https://integrate.api.nvidia.com/v1",
        alias="NVIDIA_NIM_BASE_URL",
        description="NVIDIA NIM OpenAI-compatible API base URL"
    )
    llm_base_url: str = Field(
        default="https://integrate.api.nvidia.com/v1",
        alias="LLM_BASE_URL",
        description="Generic LLM Base URL fallback"
    )
    nvidia_nim_model: str = Field(
        default="nvidia/nemotron-3-super-120b-a12b",
        alias="NVIDIA_NIM_MODEL",
        description="NVIDIA NIM model identifier"
    )
    llm_model: str = Field(
        default="nvidia/nemotron-3-super-120b-a12b",
        alias="LLM_MODEL",
        description="Generic LLM model fallback"
    )
    llm_timeout_seconds: float = Field(
        default=6.0,
        alias="LLM_TIMEOUT_SECONDS",
        description="Timeout for LLM inference requests"
    )
    solver_timeout_seconds: float = Field(
        default=4.0,
        alias="SOLVER_TIMEOUT_SECONDS",
        description="Timeout for HiGHS MILP solver"
    )
    port: int = Field(
        default=8000,
        alias="PORT",
        description="HTTP port to bind on (Render sets PORT)"
    )
    host: str = Field(
        default="0.0.0.0",
        alias="HOST",
        description="Host interface to bind on"
    )

    @property
    def effective_api_key(self) -> str:
        return self.nvidia_nim_api_key or self.llm_api_key or os.environ.get("NVIDIA_NIM_API_KEY", os.environ.get("LLM_API_KEY", ""))

    @property
    def effective_base_url(self) -> str:
        return self.nvidia_nim_base_url or self.llm_base_url or "https://integrate.api.nvidia.com/v1"

    @property
    def effective_model(self) -> str:
        return self.nvidia_nim_model or self.llm_model or "nvidia/nemotron-3-super-120b-a12b"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
