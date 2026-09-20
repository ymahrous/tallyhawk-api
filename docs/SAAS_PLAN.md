# Tallyhawk SaaS Plan — Phase 0 Decisions

## Payment Provider
Stripe — chosen for built-in subscription management, webhooks, and a
hosted billing portal (avoids building custom invoice/payment UI).

## Pricing Tiers

### Free
- Up to 20 documents/month
- Core extraction (vendor, amount, date)
- No third-party integrations
- No analytics dashboard
- No tax export

### Pro
- Unlimited (or high-cap, e.g. 500/month) document processing
- QuickBooks/Xero/FreshBooks sync
- Email inbox ingestion
- Expense categorization + tax report export
- Spend analytics dashboard
- Duplicate/fraud detection
- Priority processing queue

## Billing Cycle
Monthly billing, with an annual plan offered at a discount (e.g. 2 months free).

## Product Direction
Targeting solo freelancers and small business owners — not team-based
accounts. Approval workflows and multi-seat roles are explicitly out of
scope for this roadmap.

## Out of Scope (for now)
- Team/multi-user accounts with approval chains
- Enterprise SSO
- White-label/reseller options