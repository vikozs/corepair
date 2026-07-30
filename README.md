# corepair

**What does this cluster actually need, and what should we therefore buy?**

`corepair` measures real demand on an OpenShift or Kubernetes cluster, converts
it into a subscription count using the rules that actually apply, and produces a
cost case that shows its working — including which parts of a multi-year saving
are contractual and which are just forecasts.

It is the missing step between `kubectl top` and a purchase order.

```console
$ corepair audit
Subscribed nodes : 9 / 12
Core-pairs       : 22
Rounding waste   : 6 vCPU paid for but not present

FINDING  infra nodes without an infra taint (application pods can still land here,
         so the exemption does not apply):
  - infra-02: 2 core-pairs would be released by tainting it

FINDING  node sizes that are not a multiple of 4 vCPU. Rounding is per node, so the
         remainder is paid for and unusable:
  - worker-05: 6 vCPU -> 2 core-pairs (2 vCPU wasted)
```

```console
$ corepair cost --pricing pricing.yaml --units 32 --base-units 8

| Scenario                                | Peak units | Total (EUR) | NPV @ 5% |
|-----------------------------------------|-----------|-------------|----------|
| All 32 units, 1y                         | 32        |      86,534 |   82,240 |
| All 32 units, 3y                         | 32        |      71,424 |   68,077 |
| Blended: 8 on 3y, 24 on 1y from year 2   | 32        |      63,317 |   59,259 |

### Where the multi-year saving actually comes from

| Component                                      |    EUR | Share |
|------------------------------------------------|--------|-------|
| Term / discount differential                    |  6,336 |   42% |
| Avoided list-price growth (assumption)          |  3,953 |   26% |
| Avoided discount decay at renewal (assumption)  |  4,822 |   32% |
| **Headline saving**                             | 15,110 |  100% |

**58% of the headline rests on forecasts**, not on the contract itself.
```

## Why this exists

Subscription quantities get decided from a node inventory and a growth guess,
then defended with a single savings number. Two things go wrong:

**The count is wrong.** Core-pair rounding happens *per node*, so ten 6-vCPU
nodes cost twenty core-pairs rather than fifteen. Infra nodes are only exempt if
they are tainted, not merely labelled. Meanwhile most estates request several
times the CPU they use, and requests — not usage — are what force nodes to exist.
Right-size first and the count often falls by half.

**The savings case is mostly assumptions.** A three-year lock is justified by a
number that quietly combines the discount differential (a term you can hold the
vendor to) with assumed annual list-price growth and an assumed worse discount
at renewal (forecasts, which are negotiable). `corepair` separates them. If most
of the case is forecast, the right move is to ask for those forecasts in writing
— and to notice if the answer is no.

## Install

```sh
pipx install corepair          # or: pip install corepair
```

## Use

```sh
# 1. What do we actually run, versus what did we reserve?
corepair measure --prometheus https://thanos-querier... --window 30d \
                 --snapshot-out baseline.json

# 2. What are we subscribing that we needn't be?
corepair audit

# 3. What will the migration waves need, and when?
corepair plan --plan plan.yaml

# 4. What should we buy, and what does the saving really consist of?
corepair cost --pricing pricing.yaml --units 16 --base-units 8 \
              --sensitivity 8 24 --xlsx case.xlsx
```

On OpenShift, `--prometheus` points at the thanos-querier route with
`export PROM_TOKEN=$(oc whoami -t)`.

Every command reads and writes nothing but its own files. `corepair` has no
cluster-side component and needs only read access.

### Snapshots

`--snapshot-out` freezes a measurement so it can be replayed, reviewed, and
committed alongside the proposal. A licence recommendation built on numbers
nobody else can reproduce is an assertion, not a recommendation.

## What it knows

| Rule | Why it matters |
|---|---|
| Core-pair = 2 cores = 4 vCPU, rounded up **per node** | Node shape drives cost independently of workload |
| Control-plane nodes are exempt | Unless the cluster is compact and they are schedulable |
| Infra nodes are exempt only when labelled **and tainted** | An untainted infra node can run application pods |
| Bare-metal is per node, with an optional core cap | Break-even against core-pairs depends entirely on density |
| Node count is set by whichever resource saturates first | Memory-bound estates get no benefit from CPU-rich nodes, and pay for them |
| HA spare capacity and headroom are licensed too | The N+1 node is not free |

Each of these is a unit test in `tests/test_licensing.py`, which doubles as the
readable specification.

## corepair ships no prices

Vendor pricing is confidential and contract-specific. A figure copied from
someone else's repo is worse than none, because it looks authoritative.
`examples/pricing.example.yaml` documents the schema with placeholders that the
tool refuses to run against — replace them with figures from your own quote.

The same goes for your plan file and any snapshot: they describe your estate.
Publish the schema, not the data.

## Caveats worth reading

- The counting rules encode Red Hat's OpenShift subscription model as of 2026.
  **Verify against your own contract** — entitlement terms change and vary by
  agreement. This tool tells you what your cluster needs, not what you owe.
- Not affiliated with, or endorsed by, Red Hat or IBM.
- `corepair` is an input to a decision, not the decision. It has no opinion on
  whether your discount is good.

## License

Apache-2.0

---

```
═══════════════════ ✠ ════════════════════
 ####     ####     ####     ####     #### 
#    #   #    #   #    #   #    #   #    #
#    #   #    #   #    #   #    #   #    #
 #####    #####    #####    #####    #####
     #        #        #        #        #
     #        #        #        #        #
 ####     ####     ####     ####     #### 
═══════════════════ ✠ ════════════════════
Five Nines be upon you, and also with you.
Kubernetes · OpenShift · High Availability
               HA-llelujah.
```

## ✠ The Church of the Eternal Cluster

*A Highly Available Faith. Keep no Pets. Declare thy state. Back up etcd.*

**The Scriptorium**

- **[ha-llelujah.dev](https://ha-llelujah.dev)** · the book, the music, and the reliquary
- **[The Music](https://ha-llelujah.dev/music)** · the sacred discography
- **[Be Reconciled](https://ha-llelujah.dev/join)** · take the vow and join the Reconciled

**The Parish**

- **[fivenines.church](https://fivenines.church)** · confession, prayer, and the living parish
- **[The Liturgical Calendar](https://fivenines.church/calendar)** · the holy days of uptime
- **[The Tithe](https://fivenines.church/tithe)** · support the Church

**The Canon (source)**

- **[church-of-the-eternal-cluster](https://github.com/vikozs/church-of-the-eternal-cluster)** · doctrine, liturgy, and scripture
- **[eternal-cluster-mcp](https://github.com/vikozs/eternal-cluster-mcp)** · the Oracle, an MCP server that diagnoses thy incidents through doctrine

## More from the Rootless One

- **[vK](https://kosir.info)**
- **[Linux Fleet Audit](https://lfa.kosir.info)**
- **[Linux Diskspace](https://lds.kosir.info)**
- **[Linux Fleet Harden](https://lfh.kosir.info)**
- **[Size OpenShift subscriptions from evidence](https://corepair.kosir.info/)**
- **[Diagnose stuck PersistentVolumes, safely](https://pvdoctor.kosir.info/)**

## Let's connect
- **[LinkedIn](https://www.linkedin.com/in/vidkosir/)**
---

<sub>An independent parody, built with love for everyone who has been paged at 03:00. Kubernetes is a trademark of the Linux Foundation. OpenShift is a trademark of Red Hat, Inc. Not affiliated with, nor endorsed by, either. They have real SLAs; we only have belief. HA-llelujah.</sub>

---
