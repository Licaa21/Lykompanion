# Add tool to monitor hardware like temps and stuff. Research if there's any API support for this from NVIDIA or HWINFO or any other monitoring tools.

# More voice commands: Having the LLM launch executables or execute bat scripts. We need to allow the user to configure executables and .bat/.cmd/etc. files with location and instructions for the LLM via the frontend. The LLm should never be able to read file system or anything like that, it should just be able to call a tool which will execute exactly what the user has configured when user requests it.

# Add sources system, similar to memories, where AI can save good sources for future web search re-use, like game wiki pages

# Check what other Third Party APIs we can hook in (like allkeyshop or grouvee or idk)

# Reduce screenshot token cost: lower default max_width/JPEG quality in app/services/screenshot/capture.py (e.g. 960px/q70 vs current 1280px/q80), and expose both as configurable settings (UI + .env) instead of hardcoded.

# Add A debug button on the lower left. When clicked, we can inspect the last 10 requests we made fully -> show full prompt, tool call details, tokens, etc.

# Add new tool: Monitor process -> Allows the companion to monitor a process when it detects or concludes the user is playing a game. The backend will automatically monitor that process and keep feeding the prompts with "The user is currently playing [game]." When the backend detects that the process is killed, we should only feed the next prompt once with "Status update: The user has stopped playing [game]". After we the update once to the Agent, we stop monitoring and stop injecting this into prompts.

# Add instruction in front end whenever user select Chirp voices that they need to install the Google requirement and restart the app. Maybe gate this behind a check, trying to check if the requirement is installed or not.

# Add more metrics in the Consumption view, such as consumption per tool/feature, timeframes like last x hours, last x days, last x months, custom interval, etc.

# Possible in-game overlay?

# Improve how LLM decides to disable live mic mode. It looks like they're not able to clearly determine that the user has sent an unwanted voice message and stop listening accordingly. The LLM always tries to continue the conversation.

# Add new tools: 
- Add reminder -> Allows the companion to register a reminder with a custom LLM generated message every x interval. This will be entirely handled by the backend, simulating a message coming from the agent, so there's really one one tool call needed, with no subsequent LLM requests.
- Remove reminder -> Allows the companion to remove the above.
- Add alarm -> Allows the companion to register an alarm at a specific time, usually meant for one-time only reminders, not repetitive like the above. It should behave similar to reminders.
- Cancel Alarm -> Self explanatory.