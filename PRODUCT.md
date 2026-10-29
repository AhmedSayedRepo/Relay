# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

The operator console is a web app (`frontend/`). The device agent (`android/`) is a native Android app (Kotlin, Jetpack Compose, Material 3); design work on it follows Android conventions.

## Users

- **Operators/administrators** run Relay: they enrol phones, configure what each phone collects, review receipts in the web console, and resolve exceptions.
- **Phone holders** live with the Android agent day to day. An administrator sets the phone up; afterwards business staff (often non-technical) or the administrator keep it running. People open the agent daily to check that collection and sync are working, sync manually, and fix problems (missing permission, folder access, failed upload).

## Product Purpose

Relay is a self-hosted system that collects payment evidence (InstaPay receipt images and bank SMS) from enrolled Android phones, extracts the transaction fields, and lets operators review and verify them, including matching a receipt on one phone against the bank SMS on another. Success: every real transfer arrives, is reviewed quickly, and edited or fake receipts are caught.

## Positioning

Evidence-first and privacy-controlled: nothing is collected unless local consent on the phone, Android permission, server configuration and active enrolment all allow it, and the machine never finalises critical fields without an operator decision.

## Operating Context

- Phones collect new SMS matching server rules and images from one approved folder, queue them encrypted, and sync to the self-hosted server.
- Receipts come from InstaPay screenshots or photos of phone screens; SMS come from banks (e.g. CIB IPN Inward/Outward messages).
- Operators work in the web console (review queue, duplicates, devices, reports) in English or Arabic.

## Capabilities and Constraints

- The agent shows enrolment, connection, device identity, configuration version, last sync, and per-collector state (local consent, Android access, server setting, effective state), queue counts, dropped counts, last upload and errors; actions: enrol with a one-time token, sync now, toggle SMS/image consent, grant SMS permission, choose/remove the image folder, discard queued items.
- The agent screen uses FLAG_SECURE; enrolment tokens are never saved to instance state.
- Both the web console and the Android agent must work in Arabic and English, following the device/browser language, with full right-to-left layout.
- Self-hosted; no paid cloud services.

## Brand Commitments

- Name: Relay (Android app label "Relay Agent").
- The operator web console's existing visual system (`frontend/src/styles.css`) is the product's visual identity.

## Evidence on Hand

- Real receipts and SMS exist privately for testing; they contain real names and accounts and must never be committed or published. Demonstration data in code and screenshots must be synthetic.

## Product Principles

1. Evidence over guesses: show what is true and where it came from; never present a machine suggestion as confirmed.
2. Consent is visible: a person should always see what is being collected and why it is or is not active.
3. Calm daily check: the normal state should read as "working" at a glance; problems should say exactly what to do.
4. Bilingual by default: Arabic and English are equal citizens.

## Accessibility & Inclusion

- Arabic and English with right-to-left support; non-technical staff are primary daily users, so plain language over internal terms.
