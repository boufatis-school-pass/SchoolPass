# SchoolPass 4.9.0 — Modern administrative interface

## Visual redesign
- Rebuilt the dashboard hierarchy: institutional header, welcome panel, six color-coded KPI cards, activity chart and alerts, quick actions, then recent permits.
- KPI cards use distinct blue, teal, gold, violet, amber and rose accents and respond visually to hover and click.
- Added a shared rounded button renderer with normal, hover, pressed and disabled states. The button label remains a native child element so Arabic captions are not painted over.
- Standardized primary, secondary, informational, success, warning, and danger actions.
- Reworked the permit form header, guardian information, absence-proof sections, expulsion details, and action footer into rounded cards.
- Improved sidebar expanded and active states, including a clean Arabic-only "رخص الدخول" submenu.
- Improved contrast for editable fields, dropdowns, multiline notes, autocomplete lists, and selected list items in both light and dark themes.
- Extended the same theme to login panels, student-history notes, permit-history grids, and the statistics chart; success and danger actions retain readable contrast in dark mode.
- Unified list, table, scrollbar, and status styling.

## Data and compatibility
- Database schema remains version 9. No database reset or schema migration is introduced.
- Existing student, guardian, teacher, permit, account, audit and backup data remain in place.
- Existing permit rules, student search and teacher selection workflows are retained.

## Release verification
- The Windows 10/11 x64 installer is not considered ready until release preflight, UI smoke tests, the full test suite, Windows build and publish steps all pass.
- CI can verify source-level and automated interface regressions; the final rendered appearance should still be confirmed on the institution's Windows display and scaling settings.
