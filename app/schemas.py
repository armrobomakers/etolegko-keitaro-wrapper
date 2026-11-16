from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class CampaignCreateInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    country_codes: list[str] = Field(min_length=1, max_length=100)
    offer_id: int = Field(gt=0)
    domain_id: int = Field(gt=0)
    group_id: int = Field(gt=0)
    traffic_source_id: int = Field(gt=0)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("Укажите название кампании")
        return cleaned

    @field_validator("country_codes", mode="before")
    @classmethod
    def normalize_countries(cls, value: list[str] | str) -> list[str]:
        values = value.replace(";", ",").split(",") if isinstance(value, str) else value
        if not isinstance(values, list):
            raise ValueError("GEO укажите списком кодов через запятую")
        result: list[str] = []
        for raw in values:
            code = str(raw).strip().upper()
            if not code:
                continue
            if not code.isalpha() or len(code) not in (2, 3):
                raise ValueError(f"Некорректный GEO-код: {code}. Используйте 2–3 латинские буквы")
            if code not in result:
                result.append(code)
        if not result:
            raise ValueError("Укажите хотя бы один GEO-код страны")
        return result


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=1024)


class OfferCommitInput(BaseModel):
    offer_ids: list[int] = Field(min_length=1, max_length=1000)
    expected_version: str = Field(min_length=16, max_length=64)

    @field_validator("offer_ids")
    @classmethod
    def positive_unique_ids(cls, value: list[int]) -> list[int]:
        if any(offer_id <= 0 for offer_id in value):
            raise ValueError("ID оффера должен быть положительным числом")
        if len(set(value)) != len(value):
            raise ValueError("Оффер не может встречаться в потоке дважды")
        return value


class OfferView(BaseModel):
    offer_id: int
    name: str
    share: int
    state: str


class StreamView(BaseModel):
    id: int
    campaign_id: int
    name: str
    schema_name: str = Field(alias="schema")
    type: str
    updated_at: str | None = None
    offers: list[OfferView]
    version: str
    editable: bool
    edit_reason: str | None = None


class CampaignView(BaseModel):
    id: int
    name: str
    alias: str | None = None
    state: str | None = None

