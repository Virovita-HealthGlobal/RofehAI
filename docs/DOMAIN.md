# Domain strategy: rofehai.com and rofehai.io

**Recommendation: `rofehai.com` as the primary public site; `rofehai.io` reserved but not used as "the backend."**

## Why .com should be the public-facing domain

- `.com` is still the default expectation for a company's main site,
  especially for a health/medical-adjacent product where a person or an
  institution like TRISH will judge credibility partly by how
  conventional and trustworthy the web presence looks. `.io` reads as
  "developer tool / crypto / early-stage tech startup" to a lot of
  people outside software — not the association you want for a health
  product being evaluated by clinicians or a NASA-funded institute.
- Register `rofehai.com` and point your marketing site, investor/TRISH-facing
  materials, and primary email (e.g. `hello@rofehai.com`) at it.

## Why .io is worth reserving, but not as "the backend"

Domains don't function as backends — a TLD has no bearing on architecture;
`api.rofehai.com` and `api.rofehai.io` are technically identical in
capability. What people usually mean by ".io for the backend" is one of
two things, and it's worth being precise about which you actually want:

- **A separate technical/developer-facing site** (API docs, a status
  page, a developer portal) — if RofehAI ever exposes an API to outside
  partners (e.g., TRISH's own systems, or a future EHR integration),
  `rofehai.io` or `developers.rofehai.com` are both reasonable homes for
  that. A subdomain of your `.com` (e.g. `api.rofehai.com`,
  `developers.rofehai.com`) is simpler to manage (one DNS zone, one SSL
  setup, no split brand) and is what most companies actually do.
- **Defensive registration** — reserving `.io` purely so a competitor or
  squatter can't use it to confuse people looking for you. This is a
  legitimate and common reason to register a second TLD, and it costs
  little.

## Practical recommendation

1. Register both now (you already have this in motion).
2. Use `rofehai.com` for everything public-facing: marketing site, this
   dashboard's production deployment, email.
3. Use a subdomain of `.com` (`api.rofehai.com`) for the actual backend
   API once it's live, rather than the separate `.io` domain — simpler
   DNS/SSL, and keeps the brand in one place.
4. Park `rofehai.io` with a simple redirect to `rofehai.com` so it's not
   sitting unused, and hold it in reserve in case you later want a
   distinct developer-portal identity.

## Note on `.io`'s underlying registry

Worth being aware of, independent of RofehAI's own decision: `.io` is the
ccTLD for the British Indian Ocean Territory. The UK and Mauritius signed
a treaty on May 22, 2025 to transfer sovereignty of the Chagos
Archipelago (which comprises that territory) to Mauritius. As of this
writing, `.io` remains fully operational with no changes to registration
or resolution, and most domain-industry analysis expects any transition
— if one happens at all — to unfold gradually over years, not suddenly.
This is a low, long-tail risk, not an urgent one, but it's one more
reason `.com` is the safer choice as your permanent primary domain, with
`.io` held as a secondary/defensive registration rather than a load-bearing
one.
