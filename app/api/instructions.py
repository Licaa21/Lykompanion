from fastapi import APIRouter

from app.core.instructions import load_custom_instructions, save_custom_instructions
from app.models.schemas import CustomInstructions

router = APIRouter(prefix="/api/instructions", tags=["instructions"])


@router.get("", response_model=CustomInstructions)
async def get_instructions() -> CustomInstructions:
    return CustomInstructions(instructions=load_custom_instructions())


@router.put("", response_model=CustomInstructions)
async def update_instructions(payload: CustomInstructions) -> CustomInstructions:
    save_custom_instructions(payload.instructions)
    return payload
