# ADR 0011: Enforce disposition-backed eligibility holds

Invalid Phone and Do Not Contact dispositions create an immutable Lead Report
and an active Eligibility Hold. Wrong Business creates only the report.
Allocation excludes any Lead with an active hold. A later Customer disposition
does not release a hold; only an administrator's report resolution can do so.
A Suppressed resolution also creates the existing audited Lead Suppression.

This supersedes the earlier glossary statement that Lead Reports never affect
eligibility and records the product decision approved for scraper integration.
