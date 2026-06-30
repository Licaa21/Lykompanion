import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import chat, config, debug, game_state, instructions, memory, models, screenshot, tts, usage
from app.services.llm.game_state_extraction import run_game_state_poller

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    poller_task = asyncio.create_task(run_game_state_poller())
    try:
        yield
    finally:
        poller_task.cancel()


app = FastAPI(title="Lykompanion", lifespan=lifespan)

app.include_router(chat.router)
app.include_router(tts.router)
app.include_router(screenshot.router)
app.include_router(config.router)
app.include_router(models.router)
app.include_router(instructions.router)
app.include_router(memory.router)
app.include_router(usage.router)
app.include_router(game_state.router)
app.include_router(debug.router)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
