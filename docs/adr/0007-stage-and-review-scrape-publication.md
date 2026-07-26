# Stage and review scrape publication

Scrape Runs build staged dataset versions and publish atomically only after validation; failures leave the last successful Scraper Dataset unchanged. Versioned anomaly thresholds hold suspicious runs for administrator confirmation, while a durable Nightly Review preserves the evidence and sends one consolidated Telegram summary with Confirm or Deny actions.
