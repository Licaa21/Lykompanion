# TODO

- [ ] Updating the tickboxes for overlay and certain trackers dosen't take effect immediately live as it should, you need to restart application in order to make them reset or take effect.
- [ ] When I tested the overlay in Baldur's Gate 3, the overlay/game state/OCR stuck and wouldn't update no more, could you debug this ?
- [ ] add a disable/enabled button to the game state information in the overlay.
- [ ] a button to be able to turn on the overlay from the controller to see it (not controlling it)
- [ ] make the listening button in the overlay more subtle, just the icon or something
- [ ] verify how to make the llm respond/use tools and other stuff faster without dropping its performance/smartness and without changing the model
- [ ] verify the code logic for the toasts in the overlay that show only when the narrator speaks and dissappear when the narrator finishes talking, because when a message is too long, it gets chunked on multiple toasts and sometimes some toasts appear too late.
- [ ] if the ocr captures pause screen, stop the ocr somehow and make it start again when exiting the pause menu.
- [ ] Frameless desktop window: no native edge-resize / Aero Snap / maximize (frameless tradeoff). Add a maximize button + custom resize grips if missed.
- [ ] Tier 1 packaging: build run_app.py into a single `Lykompanion.exe` (Nuitka preferred) so Task Manager shows the app name/icon, not python.exe.
