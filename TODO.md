# More voice commands: Having the LLM launch executables or execute bat scripts. We need to allow the user to configure executables and .bat/.cmd/etc. files with location and instructions for the LLM via the frontend. The LLm should never be able to read file system or anything like that, it should just be able to call a tool which will execute exactly what the user has configured when user requests it.

# Possible in-game overlay?

# Improve how LLM decides to disable live mic mode. It looks like they're not able to clearly determine that the user has sent an unwanted voice message and stop listening accordingly. The LLM always tries to continue the conversation.

# Using Windows Core Audio APIs, add tools for the companion to be able to adjust application volume on OS-level. E.g: "Hey Lykan, Rocket League is too loud, I can't hear my music."

# Co-Pilot Strategy Mode (Proactive Pings):How it works: An opt-in toggle where the background passive OCR process doesn't just inject context, but can trigger the LLM to speak without being spoken to, but only under high-confidence conditions.

# Examples:

# If the passive OCR sees your health bar at 5% and you have no potions left, it might say, "Careful, you're out of heals."

# If you've been on the exact same quest objective text for 20 minutes (stuck on a puzzle), it can softly pip up: "Hey,do you want a hint for this puzzle, or do you want to keep searching/looking/etc..?

# Add new tools:

- Add reminder -> Allows the companion to register a reminder with a custom LLM generated message every x interval. This will be entirely handled by the backend, simulating a message coming from the agent, so there's really one one tool call needed, with no subsequent LLM requests.
- Remove reminder -> Allows the companion to remove the above.
- Add alarm -> Allows the companion to register an alarm at a specific time, usually meant for one-time only reminders, not repetitive like the above. It should behave similar to reminders.
- Cancel Alarm -> Self explanatory.
- Tool to monitor hardware like temps and stuff. Research if there's any API support for this from NVIDIA or HWINFO or any other monitoring tools.
- 

