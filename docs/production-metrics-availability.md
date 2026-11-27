# Production metric availability

The production page displays a metric only when the selected calendar window
contains measured samples. A measured zero duration is retained. A zero sample
count, an absent field or an unimplemented producer does not create a card.
Percentiles can still be unavailable for a measured metric when its population
is too small; that does not hide its measured mean or sample count.

This rule covers request timings, one-way network timings, Hive timings,
prefix-cache source summaries, timing columns and model timing charts. The
unimplemented quick/normal/final reply placeholders are removed. Zero failure
counts remain meaningful counts; an empty latency population has no timing
distribution to display.

Request data freshness is based on the dates of positive-sample request records.
The page labels a request sample more than one calendar day old as historical
and displays its actual last sample day. File collection time is labelled
separately. Re-reading a retained file cannot make its measurements current.
Current Hive and prefix-cache samples retain their independent dates.

This change neither enables production instrumentation nor deploys a server
version. The dashboard preserves existing historical samples and filters only
their presentation. Missing historical timestamps cannot be reconstructed by
rerunning collection.
