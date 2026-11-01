# Daily observations for the frozen paper tests

Implemented 31 October 2026 for the requested 1 November handoff. Paper only. Claude reviews the work and builds the UI. No computer use, server startup or real orders were needed.

The strict and loose experiments keep their existing thresholds, selection, size and fill calculations. `panic.py`, `validate.py` and `Agent.live_ready` are unchanged. The environment file was not accessed by this work.

## Pre-open brief

`PaperDaily.brief` runs from an independent background loop every 30 seconds. On weekdays it saves the first observation from 09:10 IST to `data/briefs/<day>.json`. Calls and restarts reuse the same dated file. A start at or after 09:15 saves a missed observation; later prices cannot stand in for the pre-open snapshot.

Global context comes from Yahoo daily S&P 500, Nasdaq, Brent futures, gold futures and USDINR. US cash closes can be used once 16:00 New York time has passed. For continuously traded instruments the latest completed prior exchange-local date is used conservatively. Responses are cached with session dates and retrieval times; offline fetches quietly retain dated cached context or omit missing cues. These cues are context, never signals added to the frozen rules.

The NSE pre-open stock snapshot supplies indicative prices and previous closes; the NIFTY index snapshot supplies a positive `indicativeClose` only. Regular-session open and last price are never substituted for it. A same-day pre-open source timestamp is required. Stale prices yield unknown qualification. An unknown results calendar never becomes a clean empty exclusion list. Public endpoint schemas and availability still need observation on the first real weekday; there was no live network smoke test in this cycle.

The brief records the eight biggest observed gap-downs, with reasons for exclusion from the forward universe, the stock gap threshold or a nearby results meeting. Eligibility is preliminary: the first five-minute lock and price-basis checks do not exist at 09:10. Both NIFTY gates are shown separately. This is an observation, not a trade instruction. The forward universe remains the existing wider-stock list, which differs from the historical bhavcopy universe.

`GET /api/brief` returns `{"brief": object-or-null}`. It reads files without creating or starting an agent. No dated production brief was fabricated on the Saturday implementation date.

## End-of-day review

`PaperDaily.review` saves `data/reviews/<day>.json` from 15:35 IST on weekdays. It reads strict and loose runner journals separately and reports:

- Process: eligible count, full candidate ranking, observation timestamp, picks, skips and recorded deviations. New runner log fields preserve all ranks and previously silent first-bar/locked skips.
- Execution: recorded NSE opening reference, exact 09:20 bar reference and timestamp, paper fill, basis-point differences, bid/ask, recorded sizes and quote timestamps, spread where available, and picks missing completed fills. Unknown values remain null. A recorded paper fill is not an actual execution or a measurement of actual slippage.
- Outcome: completed net rupees and per-trade results, entry exposure of recorded completed trades, and drawdown from initial Rs10,000 using recorded closed-trade daily sums. Incomplete and unobserved days are not reported as zero-profit days. The deduplicated recorded closed subtotal remains visible separately. The history comparison uses H20's exactly reconciled H17 two-pick account: Rs146.82 per qualifying strict morning and Rs112.73 per loose morning, including empty mornings. It is explicitly a daily-price proxy. A non-panic abstention has no panic-benchmark difference.

The current runner begins observing completed five-minute bars from 09:25. An earlier 09:20 open is consequently a retrospective paper reference, even though the measurement starts from that bar. The review flags this timing gap; it does not repair execution or move the decision time. Exact attainable entry prices, depth, MIS acceptance and circuit status remain H16 work. Missing observations and historical daily stops remain material limitations.

Duplicate exit events count once per day and symbol, and multiple morning decisions are flagged. Malformed journal lines are counted as a coverage problem. A day with no runner log remains unknown. An observed panic morning with no fundable picks can be a genuine zero-trade day. JSON is written atomically before the journal; a subsequent call repairs a journal write interrupted after the JSON save.

The journal gets one short entry under `## Paper days`, newest first, retaining its introduction and other sections. Rule changes are left to the existing scheduled research review. `GET /api/reviews` returns `{"reviews": [...]}`, latest ten dates first; an empty list means no saved review. The UI contract is in HANDOFF.md.

## Isolation and validation

`Agent.boot` starts the two independent observer tasks alongside the existing panic loop. Each observer runs its blocking reads in a worker thread and catches/logs its failures; a failure does not stop either runner. Lifespan shutdown cancels and awaits the background tasks. The observers never call a broker, select a replacement rule or write a trade. The existing runner's stored day-end reason is normalized to the standard `flatten`; its frozen returned trade and arithmetic are unchanged.

There are 26 added tests for fake sources, scheduling, weekend/late-start handling, dated files, once-per-day behavior across restarts, unknown and stale data, independent versions, fill comparisons, missing fills, duplicate events, drawdown, journal order/recovery, read-only API responses, loop failure isolation and task shutdown. They also check full calendar boundaries, quiet windows, whole-share allocations, the two-pick H17 reconciliation, initial-capital drawdown and standard stored trade reasons. No network is used in tests. The full suite has 172 passing tests; see `data/workflow_audit/unit_tests.log`.

`scripts/verify_paper_cycle.py` independently checks every saved horizon against original H17 rows and each H20 trade's rank, quantity, stop, fees, net and daily portfolio aggregation. It also checks frozen source hashes, the unchanged guard function, H17 input hashes and final artifact hashes. See `data/workflow_audit/validation.json`. No return search or extra H20 scoring run was performed. Only finite test/research commands were started; unrelated existing user processes were left alone.

Lesson: a factual daily log improves measurement only when it preserves what was missing and when information became available. It cannot turn an opening-price reconstruction into executable evidence. Next: H16's prospective decision-time quote, eligibility and failed-fill evidence, followed by a scheduled review; H19 remains behind that prerequisite.
