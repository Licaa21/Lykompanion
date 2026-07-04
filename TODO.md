# TODO

- [ ] Updating the tickboxes for overlay and certain trackers dosen't take effect immediately live as it should, you need to restart application in order to make them reset or take effect. Make sure every setting updates live.
- [ ] a button to be able to turn on the overlay from the controller to see it (not controlling it)
- [ ] verify how to make the llm respond/use tools and other stuff faster without dropping its performance/smartness and without changing the model
- [ ] verify the code logic for the toasts in the overlay that show only when the narrator speaks and dissappear when the narrator finishes talking, because when a message is too long, it gets chunked on multiple toasts and sometimes some toasts appear too late.
- [ ] Overlay Editor -> Make the key combo customizable by the user in the settings. In the overlay, add presets for positions, shown in edit mode + a SAVE and DISCARD/CLOSE button + Exit Editor button. Plus, add controller support for the overlay editor, as follows:
    * Add voice command for entering the editor -> User says "I want to edit the overlay." -> Companion uses a tool which enables edit mode in the overlay.
    * The user can use the left stick or the D-pad to toggle between widgets/toasts.
    * With a widget or toast selected, the user can move it with the right stick.
    * B button will discard and close the editor
    * A button will save to the selected preset.
    * X button will Delete the preset
    * Y button will Create a new preset
    * LB and RB will switch between presets.