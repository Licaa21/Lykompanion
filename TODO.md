# Add tool to monitor hardware like temps and stuff. Research if there's any API support for this from NVIDIA or HWINFO or any other monitoring tools.

# Launching/closing games/apps via voice commands

# Add sources system, similar to memories, where AI can save good sources for future web search re-use, like game wiki pages

# Improve how and when voice input triggers, minimizing sending random requests to the AI when there's just some coughing for example. An idea would be to check if there's any clean, light tool we can use to detect if actual voice has been passed or not, without bothering with transcription or lowering latency alltogether.

# Store the user's voice messages so we can replay them via the UI instead of just showing a simple label "Voice  message".

# Check what other Third Party APIs we can hook in (like allkeyshop or grouvee or idk)

# Add a voice "wake word" command (e.g. "Hey Lykompanion") that re-enables hands-free listening after it's been stopped, instead of only being able to turn it back on manually via the mic toggle.

# Reduce screenshot token cost: lower default max_width/JPEG quality in app/services/screenshot/capture.py (e.g. 960px/q70 vs current 1280px/q80), and expose both as configurable settings (UI + .env) instead of hardcoded.

# Add A debug button on the lower left. When clicked, we can inspect the last 10 requests we made fully -> show full prompt, tool call details, tokens, etc.

# Add new tool: Monitor process -> Allows the companion to monitor a process when it detects or concludes the user is playing a game. The backend will automatically monitor that process and keep feeding the prompts with "The user is currently playing [game]." When the backend detects that the process is killed, we should only feed the next prompt once with "Status update: The user has stopped playing [game]". After we the update once to the Agent, we stop monitoring and stop injecting this into prompts.

# Add instruction in front end whenever user select Chirp voices that they need to install the Google requirement and restart the app. Maybe gate this behind a check, trying to check if the requirement is installed or not.

# Add custom APIs support, where users can configure various APIs which might be game specific like riot account APIs for league of legends. How can we make this work so the user can freely configure APIs on their own without me having to add support for them first?

# Add more metrics in the Consumption view, such as consumption per tool/feature, timeframes like last x hours, last x days, last x months, custom interval, etc.

# [DONE, needs accuracy testing] Passive game-state awareness (quest/location/character) via background OCR -> cheap LLM structuring pass -> injected into system prompt. Originally targeted Windows.Media.Ocr (winsdk), but winsdk has no prebuilt wheel for Python 3.13 (only up to 3.12) and building from source needs Visual Studio - not viable for end users. Switched to pytesseract instead (app/services/ocr/tesseract_ocr.py); requires installing the separate Tesseract OCR engine binary (https://github.com/UB-Mannheim/tesseract/wiki on Windows) and either having tesseract.exe on PATH or setting TESSERACT_CMD in .env. Opt-in via Settings > Behavior (off by default). Still need to validate OCR accuracy against real game HUDs with stylized fonts.

# Add new tools: 
- Add reminder -> Allows the companion to register a reminder with a custom LLM generated message every x interval. This will be entirely handled by the backend, simulating a message coming from the agent, so there's really one one tool call needed, with no subsequent LLM requests.
- Remove reminder -> Allows the companion to remove the above.
- Add alarm -> Allows the companion to register an alarm at a specific time, usually meant for one-time only reminders, not repetitive like the above. It should behave similar to reminders.
- Cancel Alarm -> Self explanatory.
