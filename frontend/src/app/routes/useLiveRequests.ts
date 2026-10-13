import { useEffect, useRef } from "react";

/** Request-scoped updates, with polling only while the push connection is down. */
export function useLiveRequests(
  streamPath: string,
  requestIds: string[],
  onChanges: (ids: string[]) => Promise<void>,
  onExpired: () => void,
) {
  const callbacks = useRef({ onChanges, onExpired });
  useEffect(() => { callbacks.current = { onChanges, onExpired }; }, [onChanges, onExpired]);
  const scope = [...requestIds].sort().join(",");

  useEffect(() => {
    if (!scope) return;
    const watched = new Set(scope.split(","));
    const pending = new Set<string>();
    let active = true;
    let inFlight = false;
    let connected = false;
    let source: EventSource | undefined;
    let poll: ReturnType<typeof setInterval> | undefined;

    function stopPolling() {
      if (poll !== undefined) clearInterval(poll);
      poll = undefined;
    }

    async function drain() {
      if (!active || inFlight || document.visibilityState === "hidden") return;
      inFlight = true;
      try {
        while (active && pending.size) {
          const ids = [...pending];
          pending.clear();
          await callbacks.current.onChanges(ids);
        }
      } catch (error) {
        const status = (error as { status?: number }).status;
        if (status === 401 || status === 403) expire();
        else startPolling();
      } finally {
        inFlight = false;
      }
    }

    function enqueue(ids: string[]) {
      for (const id of ids) if (watched.has(id)) pending.add(id);
      void drain();
    }

    function startPolling() {
      connected = false;
      if (poll === undefined) poll = setInterval(() => enqueue([...watched]), 10_000);
    }

    function expire() {
      active = false;
      source?.close();
      stopPolling();
      callbacks.current.onExpired();
    }

    function onVisible() {
      if (document.visibilityState !== "hidden") enqueue([...watched]);
    }

    if (typeof EventSource === "undefined") startPolling();
    else {
      source = new EventSource(streamPath);
      source.addEventListener("ready", () => {
        // Reconcile the gap between the loader read and subscription, and any
        // missed changes while the listener was reconnecting.
        if (!connected) enqueue([...watched]);
        connected = true;
        stopPolling();
      });
      source.addEventListener("fallback", startPolling);
      source.addEventListener("error", startPolling);
      source.addEventListener("expired", expire);
      source.addEventListener("change", (event) => {
        try {
          const value = JSON.parse((event as MessageEvent<string>).data) as { requestIds?: unknown };
          if (Array.isArray(value.requestIds)) enqueue(value.requestIds.filter((id): id is string => typeof id === "string"));
        } catch { /* Ignore a malformed event and retain the last confirmed data. */ }
      });
    }
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      active = false;
      stopPolling();
      source?.close();
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [streamPath, scope]);
}
