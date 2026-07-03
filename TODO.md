# TODO

- [ ] Updating the tickboxes for overlay and certain trackers dosen't take effect immediately live as it should, you need to restart application in order to make them reset or take effect. Make sure every setting updates live.
- [ ] **MORE OVERLAY CUSTOMIZATION** -> Split the toast area and memories area into two actual areas. Allow the user to configure which areas they want to be shown and which not via overlay editor. Add text size and font customization.  
- [ ] a button to be able to turn on the overlay from the controller to see it (not controlling it)
- [ ] make the listening button in the overlay more subtle, just the icon or something
- [ ] verify how to make the llm respond/use tools and other stuff faster without dropping its performance/smartness and without changing the model
- [ ] verify the code logic for the toasts in the overlay that show only when the narrator speaks and dissappear when the narrator finishes talking, because when a message is too long, it gets chunked on multiple toasts and sometimes some toasts appear too late.
- [ ] if the ocr captures pause screen, stop the ocr somehow and make it start again when exiting the pause menu.
- [ ] Tier 1 packaging: build run_app.py into a single `Lykompanion.exe` (Nuitka preferred) so Task Manager shows the app name/icon, not python.exe.
- [ ] The Usage & Debug date-range inputs (`type="datetime-local"`, Usage tab) still open the browser's native calendar popup — the input box is themed but the picker itself isn't. Build a custom date/time picker if we want that fully on-theme too. (File pickers for avatar/backup import are OS-native and can't be themed.)