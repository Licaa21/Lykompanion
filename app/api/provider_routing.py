from fastapi import APIRouter

from app.core import provider_routing
from app.models.schemas import ProviderRoutingConfig

router = APIRouter(prefix="/api/provider-routing", tags=["provider-routing"])


@router.get("/{model_id:path}", response_model=ProviderRoutingConfig)
async def get_provider_routing(model_id: str) -> ProviderRoutingConfig:
    return ProviderRoutingConfig(**provider_routing.get_for_model(model_id))


@router.put("/{model_id:path}", response_model=ProviderRoutingConfig)
async def update_provider_routing(model_id: str, config: ProviderRoutingConfig) -> ProviderRoutingConfig:
    saved = provider_routing.set_for_model(model_id, config.model_dump())
    return ProviderRoutingConfig(**saved)
