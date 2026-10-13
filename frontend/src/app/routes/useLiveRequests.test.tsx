import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useLiveRequests } from "./useLiveRequests";

class PushSource extends EventTarget {
  static instances: PushSource[] = [];
  close = vi.fn();
  constructor(public url: string) { super(); PushSource.instances.push(this); }
}
function Harness({ read, expired = () => undefined }: { read: (ids: string[]) => Promise<void>; expired?: () => void }) {
  useLiveRequests("/events", ["owned"], read, expired);
  return null;
}
beforeEach(() => { PushSource.instances = []; vi.stubGlobal("EventSource", PushSource); });
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

it("coalesces push updates and never fetches an unrelated request", async () => {
  let finish!: () => void;
  const read = vi.fn().mockImplementationOnce(() => new Promise<void>((resolve) => { finish = resolve; })).mockResolvedValue(undefined);
  const view = render(<Harness read={read} />);
  const source = PushSource.instances[0]!;
  await act(async () => { source.dispatchEvent(new Event("ready")); });
  await act(async () => {
    source.dispatchEvent(new MessageEvent("change", { data: JSON.stringify({ requestIds:["owned", "unrelated"] }) }));
    source.dispatchEvent(new MessageEvent("change", { data: JSON.stringify({ requestIds:["owned"] }) }));
  });
  expect(read).toHaveBeenCalledTimes(1);
  await act(async () => { finish(); });
  expect(read).toHaveBeenCalledTimes(2);
  expect(read.mock.calls).toEqual([[["owned"]], [["owned"]]]);
  view.unmount();
  expect(source.close).toHaveBeenCalled();
});

it("polls only during disconnection and stops when push recovers", async () => {
  vi.useFakeTimers();
  const read = vi.fn().mockResolvedValue(undefined);
  render(<Harness read={read} />);
  const source = PushSource.instances[0]!;
  await act(async () => { source.dispatchEvent(new Event("ready")); await vi.advanceTimersByTimeAsync(20_000); });
  expect(read).toHaveBeenCalledTimes(1);
  await act(async () => { source.dispatchEvent(new Event("error")); await vi.advanceTimersByTimeAsync(10_000); });
  expect(read).toHaveBeenCalledTimes(2);
  await act(async () => { source.dispatchEvent(new Event("ready")); await vi.advanceTimersByTimeAsync(20_000); });
  expect(read).toHaveBeenCalledTimes(3);
});

it("closes a revoked stream and asks the router to recheck access", async () => {
  const expired = vi.fn();
  const read = vi.fn().mockResolvedValue(undefined);
  render(<Harness read={read} expired={expired} />);
  const source = PushSource.instances[0]!;
  await act(async () => { source.dispatchEvent(new Event("expired")); });
  expect(expired).toHaveBeenCalledOnce();
  expect(source.close).toHaveBeenCalledOnce();
  expect(read).not.toHaveBeenCalled();
});
