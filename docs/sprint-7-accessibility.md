# Relay Sprint 7 accessibility verification

## Implemented controls

- A keyboard-visible skip link targets the main content.
- The application exposes header, navigation, main, and complementary landmarks.
- Every icon-only action has an accessible name; decorative glyphs are hidden from assistive technology.
- Inputs, selects, dialogs, theme controls, language controls, and navigation actions have explicit labels or visible names.
- Dialogs use native dialog semantics, receive focus through the browser, and close with Escape.
- Visible focus uses a high-contrast outline in both themes.
- Statuses combine text and shape; color is not the sole signal.
- Dense desktop tables become labeled cards at narrow widths without dropping essential financial or reference data.
- Document `lang` and `dir` change together for English and Arabic.
- References, IDs, amounts, telephone-like values, hashes, and timestamps use bidi isolation.
- Motion is restrained and the reduced-motion media query removes transitions and animations.
- Loading, empty, authentication, service, export, and generic errors include useful text and next actions.

## Executed browser review

The built Sprint 7 UI was reviewed in installed Chrome against a controlled same-origin mock API at desktop, narrow 375 px, and 720 px 200%-proxy widths in English/Arabic and light/dark combinations.

| Check | Result |
|---|---|
| Landmarks | header, nav, main, aside present |
| Named interactive controls | 0 unnamed actions found |
| Form labels | 0 unlabeled input/select/textarea controls found |
| Image alternatives | 0 missing `alt` attributes found |
| Minimum control size scan | 0 undersized buttons/inputs/selects/textareas found |
| 375 px RTL overflow | viewport and document width both 375 px |
| 720 px zoom proxy overflow | no horizontal document overflow |
| Initial focus order | skip link, brand/home, navigation, preferences |
| Dialog keyboard behavior | opens and closes with Escape |
| Reduced motion | computed animation name `none` |
| Console/page errors | none during the evidence routes |

Measured representative contrast ratios (foreground to background):

| Theme | Body | Muted text | Eyebrow | Primary button | Rail text |
|---|---:|---:|---:|---:|---:|
| Light | 13.29:1 | 5.98:1 | 5.43:1 | 8.13:1 | 7.41:1 |
| Dark | 15.87:1 | 8.23:1 | 6.93:1 | 9.42:1 | 11.11:1 |

Detailed machine-readable observations are in `sprint-7-visual-evidence/browser-accessibility-evidence.json`.

## Manual keyboard checklist executed

Using browser keyboard input, the review confirmed the skip link appears on focus, focus progresses in a logical page order, language and theme controls are reachable, navigation and contextual Help actions are reachable, the Help dialog opens, Help search can be operated, and Escape closes the dialog. Visible focus and RTL navigation were visually inspected in representative screenshots.

## Boundaries

This is a practical browser and code review, not a claim of full WCAG 2.1 AA conformance. A dedicated screen-reader session (for example NVDA with Chrome), high-contrast/forced-colors review, platform zoom testing at every supported browser, and testing by operators with disabilities remain unverified and belong in the controlled pilot/accessibility follow-up.
