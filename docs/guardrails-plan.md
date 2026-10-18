# Guardrails

Implemented on `dev`; Android/device validation is pending.

## Agreed behavior

- Ask approval only for actions that cost money. Free calls, messages, posts, and
  deletions can proceed without initial approval. If cost is unclear, ask about it.
- Recheck the app, transaction details, total, and payment target after approval.
  Changed details or an expired approval require a new question.
- Keep Android's payment check and reject stale screens or nonexistent targets.
- For unclear results, wait briefly, reread the screen, and inspect a screenshot
  when available. Allow one retry of a reversible action. For possible duplicate
  calls, messages, orders, or other effects, inspect first and ask before retrying
  if uncertainty remains. Coordinate fallback counts as a retry.
- Before reporting uncertain completion, inspect the result and available image.
  If evidence remains inconclusive, ask whether the task finished. Record a user
  confirmation separately from screen evidence; a "no" routes back to recovery.
- Default to manual secret entry. A secret explicitly spoken for the current field
  stays in Android memory behind a one-use, app/field-bound reference. Values do
  not enter backend logs, model context, or saved task state. References expire
  after two minutes and are invalidated on cancellation; screen reads are masked
  and screenshots are blocked while secret content may be visible.
- Keep existing task/image limits, echo cancellation, fallback voice behavior,
  and stale recording protection. Additional background-speech filtering is deferred.

## Integration

Deploy the backend and Android updates together. The secret-entry wire format is
documented in [OVERLAY.md](../android-client/OVERLAY.md#guardrail-protocol-changes).
Older clients retain manual entry. Latest upstream voice behavior is preserved:
"Got it." remains, and step statuses appear as text.

Payment detection uses visible labels and charges. It cannot infer undisclosed
carrier fees or arbitrary app pricing; validate the relevant app controls on-device.

## Remaining validation

No new test files are retained, per request. Android has not been built here and no
toolchain was downloaded. On the other device, check:

- Slow Amazon search recovery and a single outgoing call or free message.
- Changed checkout totals/payees after approval and unknown order outcomes.
- Unverified completion, unavailable screenshots, and Stop/Pause during recovery.
- Spoken OTP entry, wrong/stale fields, one-use expiry, and manual entry fallback.
- No secret values in phone logs, backend notebooks, or subsequent observations.
