---
version: 1
slug: "relay-agent-presentation-mainactivity-kt-d137ca4a"
primary_target: "android/app/src/main/java/com/relay/agent/presentation/MainActivity.kt"
related_targets: []
---

# Android agent home screen

Scope: the single screen of the Relay Android agent (`android/app/.../presentation/MainActivity.kt`). Mode: Operate. Phone, portrait; light and dark; Arabic (RTL) and English.

Audience and job: admin sets the phone up; staff and admin open it daily to check that collection and sync work, sync manually, and fix blockers (permission, folder, server setting, upload errors).

Constraints: keep every existing function (enrol, sync, SMS/image consent, SMS permission, choose/remove folder, discard queues, device details); FLAG_SECURE; enrolment token never saved to instance state; counts are totals, no per-day history exists, so never label them "today".

## Direction contract

THESIS: The daily glance is numbers: how much is waiting, how much was dropped, when it last synced. It refuses the category default of a raw "label: yes/no" diagnostic dump and a settings-style wall of toggles.

OWN-WORLD: Relay operator palette as Material 3 roles: deep teal primary (#103e46 / dark #67c8b4 signal), copper tertiary (#a95028) reserved for "waiting" and attention, success teal, warning amber, danger red, tinted teal-grey surfaces. Roboto on the Material type scale; large display numerals in tabular figures. Rounded 16dp cards, tonal elevation.

STORY: Open the app, read three counts and "Synced 3 min ago", see each collector's state chip and the one thing blocking it, tap the fix, or tap Sync now.

FIRST VIEWPORT: Top app bar "Relay Agent" with sync action. Row of three stat tiles: SMS waiting, images waiting, dropped (copper tile when waiting > 0). Sync line under them. Then SMS card and Receipt-images card, each with state chip, the first unmet condition in plain words with its action button, consent switch. Device details collapsed at the bottom. Not enrolled: tiles replaced by the enrolment card.

FORM: Numbers first, position 7 of 7 on the ranked structural list (dealt; user locked it). seed:88779d9be712628e

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
