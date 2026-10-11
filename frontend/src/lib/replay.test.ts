import { describe, expect, it } from "vitest";

import { replayState } from "./replay";
import type { EventItem } from "./types";

let n = 0;
const ev = (type: string, task_id: string | null = null, payload: Record<string, unknown> = {}, message = type): EventItem => ({
  id: `e${++n}`,
  type,
  project_id: "p1",
  task_id,
  agent_key: null,
  message,
  payload,
  created_at: "2026-10-11T12:00:00",
});

describe("replayState", () => {
  const events = [
    ev("PROJECT_STARTED"),
    ev("TASK_CREATED", "a"),
    ev("TASK_CREATED", "b"),
    ev("TASK_QUEUED", "a"),
    ev("AGENT_STARTED", "a"),
    ev("TOOL_CALLED", "a", {}, "web_search(acme)"),
    ev("APPROVAL_REQUIRED", "a", { approval_id: "ap1" }),
    ev("APPROVAL_GRANTED", "a", { approval_id: "ap1" }),
    ev("TASK_COMPLETED", "a", {}, "found 12 companies"),
    ev("TASK_REVIEW_STARTED", "a"),
    ev("TASK_REVISION_REQUESTED", "a"),
    ev("TASK_COMPLETED", "a"),
    ev("TASK_BLOCKED", "b"),
    ev("PROJECT_COMPLETED"),
  ];

  it("reconstructs task status at any point in the timeline", () => {
    expect(replayState(events, 2).statuses.get("a")).toBe("PENDING");
    expect(replayState(events, 4).statuses.get("a")).toBe("RUNNING");
    expect(replayState(events, 5).actions.get("a")).toBe("web_search(acme)");
    expect(replayState(events, 8).statuses.get("a")).toBe("COMPLETED");
  });

  it("tracks pending approvals and reflects them in project status", () => {
    const waiting = replayState(events, 6);
    expect(waiting.approvalsPending).toBe(1);
    expect(waiting.projectStatus).toBe("WAITING_APPROVAL");
    const granted = replayState(events, 7);
    expect(granted.approvalsPending).toBe(0);
    expect(granted.projectStatus).toBe("RUNNING");
  });

  it("does not reopen finished tasks on late events, but allows QA revisions", () => {
    expect(replayState(events, 9).statuses.get("a")).toBe("COMPLETED");
    expect(replayState(events, 10).statuses.get("a")).toBe("REVISION_REQUESTED");
    expect(replayState(events, 11).statuses.get("a")).toBe("COMPLETED");
  });

  it("ends with the final project and task states", () => {
    const end = replayState(events, events.length - 1);
    expect(end.projectStatus).toBe("COMPLETED");
    expect(end.statuses.get("b")).toBe("SKIPPED");
    expect(replayState(events, 999).projectStatus).toBe("COMPLETED");
  });
});
