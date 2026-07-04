# TODO

- [ ] Updating the tickboxes for overlay and certain trackers dosen't take effect immediately live as it should, you need to restart application in order to make them reset or take effect. Make sure every setting updates live.
- [ ] a button to be able to turn on the overlay from the controller to see it (not controlling it)
- [ ] verify how to make the llm respond/use tools and other stuff faster without dropping its performance/smartness and without changing the model
- [ ] verify the code logic for the toasts in the overlay that show only when the narrator speaks and dissappear when the narrator finishes talking, because when a message is too long, it gets chunked on multiple toasts and sometimes some toasts appear too late.
- [ ] check if OCR frame skips also skips extraction passes. E.g: User sits in the pause menu for 10 minutes, we don't want to keep extracting during that time.
- [ ] Overlay Editor -> Add presets for positions, shown in edit mode + a DONE button