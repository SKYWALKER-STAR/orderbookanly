# Design System: Modern Trading Dashboard

## 1. Visual Theme & Atmosphere
Reference: Stitch project 16070217059742388694, Live Terminal screen
2f4bbafbb776415d937f687a83f39543. A dense, read-only prediction-market terminal.
Density 9, variance 3, motion 2. Use sharp panels, fine rules and clear numeric hierarchy.
The user-supplied code.html and screen.png are the visual reference. Use a compact
eight-metric strip, market banner, shallow depth chart, vertically stacked asks,
spread separator and bids, followed by Redis Stream and an expanded Raw Inspector.
Adapt the prototype to existing Streamlit controls and actual stored data.

## 2. Color Palette & Roles
- Obsidian #0f131c: canvas.
- Slate #181c24: sidebar and metric surfaces.
- Hairline #31353e: panel boundaries.
- Primary text #dfe2ee; secondary text #a8b5c7.
- Emerald #00f59b: interactive accent and bid data, matching the supplied prototype.
- Rose #ffb3b5: semantic ask data.
- Cyan #7fe5ff: spread and metadata.
No gradients or neon glow. Never communicate bid/ask by color alone.

## 3. Typography Rules
Space Grotesk with local sans-serif fallback for headings; JetBrains Mono with
Consolas fallback for numbers. Google Fonts is optional with local fallbacks. Tabular digits, compact
headlines, four-decimal quotes and two-decimal share quantities. Missing metrics
display an em dash, never an invented zero.

## 4. Component Stylings
Use native accessible Streamlit controls. Numeric strips use flat surfaces.
Depth charts distinguish bids and asks with labels and color. Tables use cumulative
depth bars aligned to the right. Best quotes sit next to the spread separator.
Show upstream raw JSON expanded with native copy support; stored analysis is collapsible.
Show actionable empty/error states and explicit data age; freshness does not
claim WebSocket connectivity. No execution controls in this read-only product.

## 5. Layout Principles
Maximum width 1800px. Sidebar holds navigation and display depth. Market and outcome
controls sit in the header. Only the selected view queries its data source. Desktop
metrics use eight columns, then four on narrower screens and two on mobile. The
depth chart, ladder, delta feed and raw inspector stack vertically as in the supplied
screenshot. No decorative hero or overlapping content.

## 6. Motion & Interaction
Refresh data using existing Streamlit fragments. Keep numeric positions stable.
Avoid perpetual motion in a market-reading interface; no fake heartbeat or
fabricated activity. Preserve keyboard focus outlines and native interactions.

## 7. Anti-Patterns
No invented market data, trade volume, connection status or latency. No emoji,
pure black, serif typography, glow, oversized marketing cards or gradient text.
Historical trends remain explicitly Yes; selected snapshot details follow Outcome.
