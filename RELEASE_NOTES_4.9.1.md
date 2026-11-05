# SchoolPass 4.9.1 — Final visual polish

## Finishing touches
- Apply the same rounded surface system to the login identity panel and login form panel.
- Match the live student autocomplete popup background to the tinted input field instead of a separate white surface.
- Keep the updated 4.9.1 version metadata synchronized between the application, installer, release preflight and tests.

## Runtime verification
- Add a Windows Tk runtime test that checks actual ttk layouts for rounded backgrounds and retained native button captions.
- Verify rounded Entry and Combobox elements and the field background in light and dark modes.
- Existing headless smoke, RTL, dashboard, permits, guardian and data-preservation tests remain required.

## Data and compatibility
- Database schema stays at version 9. No schema migration, reset or data deletion.
- Existing student, guardian, teacher, user, permit, audit and backup data remain preserved.
- Final appearance still needs user-side visual confirmation at the actual school display resolution and Windows scaling.
