# Age Count

Counts annual rings on increment-core photos for forestry field work.
Android app (`app/`, `core/`) and the Python engine it was developed from (`engine/`, `tools/`).

## Status: prototype, not for field use yet

The counts are a starting number to check, not a validated measurement.

Measured limits (from the development notes in `docs/`):

- A 120-ring core at 29 px/mm was counted as 130 (+8%).
- Juvenile rings of about 2.6 mm need a ring-width hint; the detector misses them without one.
- Outer rings of wide cores are too thin to resolve at the pixel density of one photo.
- Auto-localisation of the core has failed on a plastic ruler and on some dark backgrounds.
- The scale from the printed card carries about a 7% error.
- Edge filters and dark-band counting each work on some cores and fail on others.

The app is designed to show its ring marks on the photo so the user can review them.
That review step is not finished.

## Layout

| Path | Purpose |
|------|---------|
| `app/`, `core/` | Android app and the Kotlin counting core |
| `engine/`, `tools/` | Python prototype, evaluation and card generators |
| `docs/` | Design notes and next steps |
| `card/` | Printable scale-card designs |
| `tests/` | Python tests |

Photos, model weights and generated outputs are not in the repository. The scripts
read photos from `~/ForestAgeCounter` (or the `AGECORE_PHOTOS` environment variable).

## Feedback welcome

Useful reports:

- A core photo that gives a count you believe is wrong, with your own count.
- The core species, approximate ring width and photo size or pixels per mm.
- Ideas for detecting ring boundaries on cores where the current methods fail.

Please open an issue rather than sending photos by other channels.

## Build

Open the project in Android Studio, or run `./gradlew assembleDebug`.
Build flavours: `android9` and `android12` (choose the one matching the device's Android version).

## Licence

No licence has been chosen yet. Until one is added, all rights are reserved by the author.
