# Design System: Modern Trading Dashboard

## 1. Visual Theme & Atmosphere
Reference: Stitch project 16070217059742388694, Live Terminal screen
2f4bbafbb776415d937f687a83f39543. A dense, read-only prediction-market terminal.
Density 9, variance 3, motion 2. Use sharp panels, fine rules and clear numeric hierarchy.
Adapt the prototype to existing Streamlit controls and actual stored data.

## 2. Color Palette & Roles
- Obsidian #0f131c: canvas.
- Slate #181c24: sidebar and metric surfaces.
- Hairline #31353e: panel boundaries.
- Primary text #dfe2ee; secondary text #a8b5c7.
- Emerald #36c99a: the sole interactive accent and bid data.
- Rose #ef7185: semantic ask data, preserving the prototype's bid/ask distinction.
No gradients or neon glow. Never communicate bid/ask by color alone.

## 3. Typography Rules
Space Grotesk with local sans-serif fallback for headings; JetBrains Mono with
Consolas fallback for numbers. No remote font dependency. Tabular digits, compact
headlines, four-decimal quotes and two-decimal share quantities. Missing metrics
display an em dash, never an invented zero.

## 4. Component Stylings
Use native accessible Streamlit controls. Numeric strips use flat surfaces and
top rules. Depth charts distinguish bids and asks with labels and color. Tables
use cumulative depth bars. Keep raw JSON available in collapsed expanders.
Show actionable empty/error states and explicit data age; freshness does not
claim WebSocket connectivity. No execution controls in this read-only product.

## 5. Layout Principles
Maximum width 1600px. Sidebar holds market, outcome and display depth controls.
Tabs separate live and historical workflows. Wide screens use a 3:1 depth/chart
summary split and paired order books. Below 768px collapse columns to one.
Controls have 44px tap targets. No decorative hero or overlapping content.

## 6. Motion & Interaction
Refresh data using existing Streamlit fragments. Keep numeric positions stable.
Avoid perpetual motion in a market-reading interface; no fake heartbeat or
fabricated activity. Preserve keyboard focus outlines and native interactions.

## 7. Anti-Patterns
No invented market data, trade volume, connection status or latency. No emoji,
pure black, serif typography, glow, oversized marketing cards or gradient text.
Historical trends remain explicitly Yes; selected snapshot details follow Outcome.
