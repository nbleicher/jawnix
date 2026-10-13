import { redirect } from "react-router";
import { api, type AdminMFAStatus } from "./adminMFA";
import { clearReadPrefetch } from "../routeReadCache";

export async function managerAccessLoader() {
  try {
    return await api<{ ok: true; id: string; email: string }>("/api/manager/access");
  } catch (error) {
    if ((error as { status?: number }).status === 401) {
      const response = await fetch("/api/auth/admin-mfa", { credentials: "same-origin" });
      if (response.ok) {
        const state = await response.json() as AdminMFAStatus;
        throw redirect(state.next.replace(/^\/app/, ""));
      }
      throw redirect("/sign-in?next=%2Fapp%2Fmanager%2Fuploads");
    }
    throw error;
  }
}

export interface LeadUploadStatus {
  id: string;
  filename: string;
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
  createdAt: string;
  completedAt: string | null;
}

export function submitLeadUpload(file: File, state: string, onProgress: (percent: number) => void) {
  clearReadPrefetch();
  return new Promise<LeadUploadStatus>((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", "/api/manager/uploads");
    const cookie = document.cookie.split(";").map(value => value.trim()).find(value => value.startsWith("jawnix_csrf="));
    request.setRequestHeader("X-CSRF-Token", decodeURIComponent(cookie?.split("=", 2)[1] ?? ""));
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
    request.send(body);
  });
}
