# How local analytics work

Analytics runs in your browser on the prompts returned by your local scan. It
does not record keystrokes, watch application usage, or contact another service.

## Activity span estimate

Activity adds the gaps between consecutive recorded prompts when the gap is at
most the idle cutoff (30 minutes by default). Each gap belongs to the preceding
prompt's project. A single global timeline prevents overlapping tasks from
counting twice; both endpoints must match the filters, and filtered-out prompts
remain boundaries. Simultaneous submissions in different projects are ambiguous
and excluded. Isolated prompts add no time.

These spans include reading and waiting, and miss work outside the spans. They
are a useful comparison of recorded activity, not measured working hours.

## Typing time estimate

Typing time is the recorded word count divided by an adjustable speed (40 WPM
by default). It includes pasted, dictated, code, and recovered text, and excludes
edits; it is the estimated time to type that text once. Do not add it to activity
time. The browser remembers the typing speed and idle cutoff locally.

## Text counts and dates

Words follow the app's whitespace-based counting. Characters count Unicode code
points. Approximate sentence counts exclude fenced/inline code and URLs, use
English sentence segmentation where available, and count separate-line fragments;
other languages can differ.

Missing or future timestamps are excluded from time, charts, and streaks but
remain in text totals. The dashboard's methodology section reports this coverage.
Dates and hourly patterns use your browser's local timezone. Daily charts show
the latest 60 calendar days in the selection.

## Filters and exports

The dashboard follows search, tool, project, session, and entry-type filters.
Inclusive date filters narrow the analytics selection further. **All time**
clears only the analytics date range; server scan filters still apply.

The downloadable JSON report contains aggregates, project/session labels,
filters, and calculation settings. It omits original prompt text, but labels
and filters can still contain private information.

The activity image omits prompt text, file paths, session titles, and search
terms. Project names are hidden by default and can be enabled for an export.
It is rendered locally, with light and dark themes and the selected time range.
Downloading or copying it does not upload it to a service. Native sharing, where
supported, opens your device's share sheet for you to choose a destination.
