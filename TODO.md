# More voice commands: Having the LLM launch executables or execute bat scripts. We need to allow the user to configure executables and .bat/.cmd/etc. files with location and instructions for the LLM via the frontend. The LLm should never be able to read file system or anything like that, it should just be able to call a tool which will execute exactly what the user has configured when user requests it.

# Possible in-game overlay?

# Improve how LLM decides to disable live mic mode. It looks like they're not able to clearly determine that the user has sent an unwanted voice message and stop listening accordingly. The LLM always tries to continue the conversation.

# Using Windows Core Audio APIs, add tools for the companion to be able to adjust application volume on OS-level. E.g: "Hey Lykan, Rocket League is too loud, I can't hear my music."

# Co-Pilot Strategy Mode (Proactive Pings):How it works: An opt-in toggle where the background passive OCR process doesn't just inject context, but can trigger the LLM to speak without being spoken to, but only under high-confidence conditions.

# Examples:

# If the passive OCR sees your health bar at 5% and you have no potions left, it might say, "Careful, you're out of heals."

