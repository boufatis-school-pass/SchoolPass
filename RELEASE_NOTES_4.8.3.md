# SchoolPass 4.8.3

## Interface refinement
- Compact the dashboard's hero, KPI cards, chart, alerts and recent-activity panel to reduce unused vertical and horizontal space.
- Give entry and combo boxes a softly tinted surface, a stronger visible outline, a clear focus border and more rounded corners in light and dark themes.
- Give the entry-permits submenu its own rounded surface and use clean Arabic-only labels to avoid punctuation and arrow reordering in mixed-direction text.
- Keep the dashboard chart visible by default and preserve the existing manual collapse control.

## Validation
- Added regression checks for field contrast, rounded surfaces, dashboard spacing and the entry-permits submenu.
- The Windows installer build and test workflow must complete before this release is considered ready.
- Arabic rendering still requires visual confirmation on Windows across the remaining dialogs and tables.
