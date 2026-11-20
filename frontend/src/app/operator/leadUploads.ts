import { csrfToken } from "../api";
import { clearReadPrefetch } from "../routeReadCache";

/**
 * The operator's CSV uploads: /api/admin/lead-uploads.
 *
 * Every upload adds to one DB2 or DB3 inventory; a DB3 upload carries the
 * date its rows were scrubbed. Uploads made by the retired managed
 * administrators still name them in `uploadedBy`.
 */

export const LEAD_UPLOADS = "/api/admin/lead-uploads";

export interface LeadUploadStatus {
  id: string;
  filename: string;
  /** "db2" or "db3". */
  database: "db2" | "db3";
  /** The scrub date every DB3 upload carries; null for DB2. */
  scrubbedOn: string | null;
  state: string;
  status: "uploading" | "queued" | "processing" | "complete" | "failed";
  bytes: number;
  processedRows: number;
  validRows: number;
  rejectedRows: number;
  importedRows: number;
  updatedRows: number;
  duplicateRows: number;
  error: string;
  /** "Operator", or the managed administrator who uploaded it before retirement. */
  uploadedBy?: string;
  createdAt: string;
  completedAt: string | null;
}

/**
 * Send one CSV with the state it belongs to. XMLHttpRequest rather than fetch
 * so a large file reports upload progress.
 */
export function submitLeadUpload(
  file: File,
  state: string,
  onProgress: (percent: number) => void,
  options?: { database?: "db2" | "db3"; scrubbedOn?: string },
) {
  clearReadPrefetch();
  return new Promise<LeadUploadStatus>((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", LEAD_UPLOADS);
    request.setRequestHeader("X-CSRF-Token", csrfToken());
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round(100 * event.loaded / event.total));
    };
    request.onerror = () => reject(new Error("The upload connection failed. Try again."));
    request.onabort = () => reject(new Error("The upload was interrupted. Try again."));
    request.onload = () => {
      clearReadPrefetch();
      let value: LeadUploadStatus & { detail?: string };
      try { value = JSON.parse(request.responseText) as typeof value; }
      catch { reject(new Error("The upload response could not be read.")); return; }
      if (request.status < 200 || request.status >= 300) {
        reject(Object.assign(new Error(value.detail || "The CSV could not be uploaded."), { status:request.status }));
      } else resolve(value);
    };
    const body = new FormData();
    body.append("file", file);
    body.append("state", state);
    body.append("database", options?.database ?? "db2");
    if (options?.scrubbedOn) body.append("scrubbedOn", options.scrubbedOn);
    request.send(body);
  });
}
