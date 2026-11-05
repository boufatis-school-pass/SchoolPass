# SchoolPass 4.8.2

## UI fixes
- Restore visible text on themed buttons by avoiding custom image surfaces that could cover ttk button labels on some Windows/Tk configurations.
- Show the dashboard statistics and charts by default, while preserving the option to collapse them manually.
- Apply the shared window configuration to the permit-issuance form for consistent sizing and placement.
- Add regression checks for dashboard chart visibility, permit-form window configuration, and RTL widget adapters.

## Verification
- Windows x64 installer build and automated tests are run by GitHub Actions.
- Arabic rendering still requires visual confirmation on the target Windows installation.
