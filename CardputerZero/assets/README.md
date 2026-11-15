# LAB5 visual assets

The approved flashing animation layers live in `../anime/`:

- `body.png` (or `base.png`) — stationary character layer;
- `left_hand.png` — transparent left-hand/cable layer;
- `right_hand.png` — transparent right-hand/cable layer.

All three layers must use the same canvas and coordinates. The runtime scales them once, preserves
their authored attachment points, then moves both hand layers according to actual flash progress.

Future success/error sprite sequences may be added here in their own directories. Use transparent
PNG frames authored for the 320×170 screen and include a `manifest.json` with frame order and timing.
