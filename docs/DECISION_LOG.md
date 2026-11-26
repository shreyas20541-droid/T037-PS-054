# Engineering and Domain Decision Log

| # | Decision | Why |
|---:|---|---|
| 1 | Use SQLite as the durable ledger store and wrap each mutation group in a connection transaction. | Keep command effects atomic and queryable without an external database service. |
| 2 | Store balances as dated lots rather than one mutable wallet/card total. | Preserve source, expiry, remaining value, and reversal provenance per tranche. |
| 3 | Process physical command-file order and never special-case planted line numbers. | Outcomes must follow the actual prior ledger state and work on unseen input packs. |
| 4 | Round calculations to two decimal places and serialize money with two decimals. | Match the Rs/paisa CSV contract and avoid displaying floating-point tails. |
| 5 | Prioritize PROMO lots, then sort by expiry and creation time. | Consume promotional value first while otherwise following FEFO. |
| 6 | Treat expiry dates as inclusive for redemption and use calendar dates for day-window rules. | Match the specification's day-N convention and allow redemption through the printed expiry date. |
| 7 | Partially approve restricted-bill redemptions at 70% of bill total. | Preserve the allowed portion while leaving the remainder to another tender. |
| 8 | Restore same-day reversals to their exact source lots; create dated reversal lots for later reversals. | Same-day activity can be undone in place, while later refunds retain the original expiry without reviving an expired tranche. |
| 9 | Claw back the full original load amount on an eligible chargeback, representing spent value as a negative lot. | Recover the liability and retain an explicit debt until later loads offset it. |
| 10 | Block wallet redemptions during negative debt, but allow top-ups to offset that debt first. | Let customers cure a chargeback balance without permitting spending before the debt is cleared. |
| 11 | Enforce MIN_KYC holding and monthly gross-load caps separately; apply FULL_KYC's higher holding cap. | Keep tier-specific limits tied to both current exposure and monthly inflow. |
| 12 | Count card activation, reactivation grace, chargeback windows, and lost-card quiet periods in calendar days. | Make the boundary dates deterministic, including valid day 180, day 90, and day 120 cases. |
| 13 | Permit one post-expiry reactivation with a 10% fee and a six-month new expiry. | Restore most value during the defined grace period without indefinite repeated extensions. |
| 14 | Freeze lost cards immediately; after seven calendar days transfer remaining value less a fee capped at Rs 50. | Add a quiet period for loss claims and preserve the original expiry on replacement value. |
| 15 | Calculate month-end liability from active lots and publish reconciliation and breakage rows from ledger state. | Give finance an auditable close while recognizing only positive value that has passed the expiry grace period. |
| 16 | Use calendar anniversaries for gift-card three-year validity and paid-load one-year expiry. | Match the stated business durations across leap years rather than approximating them as 365 days. |
| 17 | Treat day 180 as the last valid activation day; auto-void and record an original-tender refund from day 181. | Preserve the stated activation boundary and make the unactivated-card refund auditable. |
| 18 | Write a month-end reminder snapshot for active lots expiring in the following 30 days, and exclude negative wallet balances from liability. | Make expiry follow-up reproducible and keep the month-end liability consistent with the brief's negative-wallet exclusion. |
| 19 | Apply daily wallet velocity limits to the stored-value amount after any bill restriction cap. | Enforce each KYC tier's spend ceiling against actual wallet spend rather than the unapproved request amount. |
| 20 | Require exact month-end liability equality after rounding ledger amounts to paisa. | A one-paisa difference is a reconciliation failure, not a tolerance-sized success. |