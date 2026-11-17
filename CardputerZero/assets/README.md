# LAB5 visual assets

The approved flashing animation layers live in `../anime/`:

- `body.png` (or `base.png`) — stationary character layer;
- `left_hand.png` — transparent left-hand/cable layer;
- `right_hand.png` — transparent right-hand/cable layer.

All three layers must use the same canvas and coordinates. The runtime scales them once, preserves
their authored attachment points, then moves both hand layers according to actual flash progress.

`intro_uf.png` is shown at startup for 2.5 seconds. It is fitted without cropping to the 320×170
display and can be dismissed early with any key. The package builder stores an optimized copy.

Future success/error sprite sequences may be added here in their own directories. Use transparent
PNG frames authored for the 320×170 screen and include a `manifest.json` with frame order and timing.
