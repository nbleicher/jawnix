# Separate acquisition and fulfillment data ownership

The Google Maps Scraper owns the durable SQLite Scraper Dataset, while Jawnix owns the PostgreSQL Lead Inventory and permanent distribution history. A Scrape Run updates only the Scraper Dataset; a subsequent Inventory Sync reads a committed dataset version without modifying it, preserving replayability and preventing fulfillment state from contaminating acquisition data.
