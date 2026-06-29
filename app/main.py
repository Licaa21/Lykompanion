from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import chat, config, instructions, memory, models, screenshot, tts, usage

app = FastAPI(title="Lykompanion")

app.include_router(chat.router)
app.include_router(tts.router)
app.include_router(screenshot.router)
app.include_router(config.router)
app.include_router(models.router)
app.include_router(instructions.router)
app.include_router(memory.router)
app.include_router(usage.router)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
