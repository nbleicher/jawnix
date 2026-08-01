# Proxy Options (D2 / O-2) — lowest cost for Google Maps at scale

**Goal:** cheapest proxy setup that keeps the block/empty rate low at ~250k records/day.

## The cost driver you must understand first
Residential proxies bill **per GB**, and a headless **browser** loading Google Maps (map tiles, JS, images) is **bandwidth-heavy** — so your bill = `$/GB × GB/day`, and **GB/day is unknown until measured**. That single fact flips the cheapest answer:

- **Per-GB (residential):** cost scales with traffic → can balloon with a browser.
- **Per-IP / unmetered (datacenter & static-ISP):** ~fixed cost regardless of GB → usually far cheaper for browser scraping.

Two levers cut cost hard: (1) **datacenter/ISP proxies** (Maps tolerates them better than social sites), and (2) the scraper's **`fast_mode`** (stealth HTTP instead of a full browser) — roughly an order-of-magnitude less bandwidth, which makes per-GB residential viable. **Measure GB/day on box1** (same calibration window as O-3) before committing to a per-GB plan.

## Current pricing (June 2026)

| Provider | Cheapest residential $/GB | Datacenter / ISP (per-IP, ~unmetered) | Verdict for you |
|---|---|---|---|
| **Evomi** | **$0.49/GB** (sub) · $0.99 PAYG | DC $0.35/GB · static ISP **$1.00/IP** | **Cheapest credible residential** by far |
| **Webshare** | $1.40/GB (50% promo; ~$2.80 list) | DC from **~$0.02–0.03/IP** · static ISP from **$0.30/IP**, high/unltd bandwidth · **10 free proxies** | **Cheapest per-IP / unmetered** + free tier for the smoke test |
| **Decodo** (ex-Smartproxy) | $3 → $1.5/GB at 1 TB | — | Mid; only at huge volume |
| **IPRoyal** | $7 → $1.75/GB; non-expiring PAYG | — | OK for tiny PAYG; not cheapest at scale |
| **Bright Data** | $8.40 → ~$3.30/GB | — | Premium; **overkill/expensive** for Maps |
| **Oxylabs** | ~$8–10/GB (some PAYG ~$4) | — | Premium; **overkill/expensive** for Maps |

## Recommendation (lowest cost)
1. **Start with per-IP / unmetered, not per-GB.** For browser-based Maps, **Webshare** (datacenter ~$0.02–0.03/IP, or static-ISP from $0.30/IP with high bandwidth) makes proxy cost ~fixed regardless of traffic. Its **10 free proxies** are perfect for the box1 smoke test (GO_LIVE 3.2).
2. **Keep a cheap rotating-residential fallback** for if datacenter block-rate is too high: **Evomi at $0.49/GB** is the cheapest credible residential — about 1/3 of Decodo and ~1/7 of Bright Data/Oxylabs.
3. **Skip Bright Data & Oxylabs** on a budget — they're enterprise-priced for the hardest targets; Maps doesn't need them.
4. **Decide with data, not vibes:** run the box1 proxy A/B (FIRST_BOT_SETUP §6) — Arm A datacenter/ISP, Arm B Evomi residential — and compare empty-rate (K5) + measured GB/day. Pick the cheapest arm that holds empty-rate ≤ ~10%.

**Net:** Webshare per-IP as primary (near-fixed cost, free trial), Evomi $0.49/GB residential as fallback. That's the lowest-cost path that still has a block-rate safety net. Final pick comes from the A/B + your measured GB/day.

> Plug-in: set the chosen endpoints in `worker/.env` → `PROXIES=` (comma-separated). The A/B = two worker groups, one per `.env`.

Sources: [Webshare pricing](https://www.saasultra.com/webshare-pricing/) · [Evomi](https://evomi.com/product/residential-proxies) · [IPRoyal/Decodo](https://use-apify.com/blog/iproyal-vs-smartproxy-2026) · [Bright Data/Oxylabs](https://aimultiple.com/proxy-pricing)
