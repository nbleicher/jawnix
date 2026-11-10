import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LEAD_UPLOADS, submitLeadUpload } from "./leadUploads";

/** A stand-in XMLHttpRequest that records what was sent and answers on demand. */
class FakeRequest {
  static last: FakeRequest;
  method = "";
  url = "";
  headers: Record<string, string> = {};
  body: FormData | null = null;
  status = 0;
  responseText = "";
  upload: { onprogress: ((event: { lengthComputable: boolean; loaded: number; total: number }) => void) | null } = { onprogress: null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;

  constructor() { FakeRequest.last = this; }
  open(method: string, url: string) { this.method = method; this.url = url; }
  setRequestHeader(name: string, value: string) { this.headers[name] = value; }
  send(body: FormData) { this.body = body; }
  answer(status: number, value: unknown) {
    this.status = status;
    this.responseText = typeof value === "string" ? value : JSON.stringify(value);
    this.onload?.();
  }
}

const file = new File(["Phone,Name\n6155550100,Example\n"], "leads.csv", { type: "text/csv" });

beforeEach(() => {
  vi.stubGlobal("XMLHttpRequest", FakeRequest);
  document.cookie = "jawnix_csrf=csrf%20token";
});
afterEach(() => {
  vi.unstubAllGlobals();
  document.cookie = "jawnix_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
});

describe("submitLeadUpload", () => {
  it("posts the file and its state to the operator's endpoint with the CSRF token", async () => {
    const progress: number[] = [];
    const pending = submitLeadUpload(file, "TN", (percent) => progress.push(percent));
    const request = FakeRequest.last;
    expect(LEAD_UPLOADS).toBe("/api/admin/lead-uploads");
    expect([request.method, request.url]).toEqual(["POST", "/api/admin/lead-uploads"]);
    expect(request.headers["X-CSRF-Token"]).toBe("csrf token");
    expect(request.body?.get("state")).toBe("TN");
    expect((request.body?.get("file") as File).name).toBe("leads.csv");
    request.upload.onprogress?.({ lengthComputable: true, loaded: 1, total: 3 });
    request.upload.onprogress?.({ lengthComputable: false, loaded: 2, total: 0 });
    expect(progress).toEqual([33]);
    request.answer(202, { id: "u1", filename: "leads.csv", status: "queued", uploadedBy: "Operator" });
    await expect(pending).resolves.toMatchObject({ id: "u1", status: "queued", uploadedBy: "Operator" });
  });

  it("rejects with the server's reason and status", async () => {
    const pending = submitLeadUpload(file, "ZZ", () => undefined);
    FakeRequest.last.answer(422, { detail: "Choose a US state for this file." });
    await expect(pending).rejects.toMatchObject({ message: "Choose a US state for this file.", status: 422 });
  });

  it("explains a lost connection and an unreadable answer", async () => {
    const lost = submitLeadUpload(file, "TN", () => undefined);
    FakeRequest.last.onerror?.();
    await expect(lost).rejects.toThrow("The upload connection failed. Try again.");
    const garbled = submitLeadUpload(file, "TN", () => undefined);
    FakeRequest.last.answer(502, "<html>Bad gateway</html>");
    await expect(garbled).rejects.toThrow("The upload response could not be read.");
  });
});
