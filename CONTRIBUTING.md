# Contributing

## The rules are the product

`tests/test_licensing.py` is the specification. If you believe a counting rule
is wrong, the useful contribution is a failing test plus a citation — a link to
the vendor's subscription guide, or the wording from a contract with the
identifying parts removed.

Rules change over time and differ between agreements. A rule with no citation
is folklore, and folklore is what this tool exists to replace.

## Wanted

- Other vendors' models (SUSE Rancher Prime, Tanzu, EKS-A) behind the same
  `SubscriptionModel` abstraction
- Better demand collection: VPA `recommendation` CRD support, KEDA-scaled
  workloads, batch/CronJob demand which is bursty and badly served by a p95
- A `corepair diff` that compares two snapshots, so drift between waves is
  visible

## Never

Do not commit real prices, real quotes, real customer topologies, or a snapshot
from a cluster you do not own. PRs containing them will be closed.
