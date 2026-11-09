import type { ControlAction } from "./actions";

/**
 * Acquisition decisions with their confirmation copy, shared by the
 * Acquisition page and the side panels on Today so both state the same
 * consequence for the same endpoint.
 */

export const ANOMALY_CONFIRM: ControlAction = {
  name: "confirm",
  label: "Confirm",
  consequence:
    "Publishes the held staged dataset over the active Scraper Dataset and queues an Inventory Sync. The exact same command Telegram uses runs this.",
  destructive: false,
  requiresReason: true,
};

export const ANOMALY_DENY: ControlAction = {
  name: "deny",
  label: "Deny",
  consequence:
    "Discards the held staged dataset. The last successful Scraper Dataset stays authoritative and nothing is published.",
  destructive: true,
  requiresReason: true,
};

export const EXCLUSION_CONFIRM: ControlAction = {
  name: "confirm",
  label: "Confirm globally",
  consequence:
    "Makes every accepted phone in this Customer upload globally ineligible. The current pool impact is recalculated at decision time.",
  destructive: false,
  requiresReason: true,
};

export const EXCLUSION_DENY: ControlAction = {
  name: "deny",
  label: "Deny global effect",
  consequence:
    "Keeps this list scoped to the uploading Customer. Its phones will not become globally ineligible.",
  destructive: true,
  requiresReason: true,
};

export const NICHE_CONFIRM: ControlAction = {
  name: "confirm",
  label: "Confirm Niche",
  consequence:
    "Confirming a Niche makes this Source Segment comparable against its same-niche peers, which is what lets Source Recommendations become eligible. Correct the Niche here if the proposal is wrong.",
  destructive: false,
  requiresReason: true,
};

export const NICHE_DENY: ControlAction = {
  name: "deny",
  label: "Deny proposal",
  consequence:
    "Removes this proposal from the review queue without confirming a Niche. The Source Segment remains ineligible for automated recommendations until a later proposal is reviewed.",
  destructive: true,
  requiresReason: true,
};
