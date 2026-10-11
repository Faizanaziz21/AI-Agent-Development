import type { EventItem } from "./types";

const TRANSITIONS: Record<string, string> = {
  TASK_CREATED: "PENDING",
  TASK_ASSIGNED: "READY",
  TASK_QUEUED: "QUEUED",
  AGENT_STARTED: "RUNNING",
  TASK_DELEGATED: "WAITING_CHILDREN",
  TASK_REVIEW_STARTED: "IN_REVIEW",
  TASK_REVISION_REQUESTED: "REVISION_REQUESTED",
  TASK_RETRY_SCHEDULED: "RETRY_SCHEDULED",
  TASK_RECOVERED: "QUEUED",
  TASK_REASSIGNED: "QUEUED",
  APPROVAL_REQUIRED: "WAITING_APPROVAL",
  APPROVAL_GRANTED: "QUEUED",
  APPROVAL_REJECTED: "QUEUED",
  APPROVAL_CHANGES_REQUESTED: "QUEUED",
  BUDGET_EXCEEDED: "WAITING_APPROVAL",
  TASK_ESCALATED: "ESCALATED",
  TASK_BLOCKED: "BLOCKED",
  TASK_COMPLETED: "COMPLETED",
  TASK_FAILED: "FAILED",
  TASK_CANCELLED: "CANCELLED",
};

const TERMINAL = new Set(["COMPLETED", "FAILED", "CANCELLED", "BLOCKED"]);

export interface ReplayFrame {
  statuses: Map<string, string>;
  actions: Map<string, string>;
  projectStatus: string;
  approvalsPending: number;
}

/** Reconstruct task and project state as of `events[0..upto]` (inclusive). */
export function replayState(events: EventItem[], upto: number): ReplayFrame {
  const statuses = new Map<string, string>();
  const actions = new Map<string, string>();
  const pendingApprovals = new Set<string>();
  let projectStatus = "PLANNING";
  for (let i = 0; i <= upto && i < events.length; i++) {
    const e = events[i];
    if (e.type === "PROJECT_STARTED") projectStatus = "RUNNING";
    else if (e.type === "PROJECT_COMPLETED") projectStatus = "COMPLETED";
    else if (e.type === "PROJECT_FAILED") projectStatus = "FAILED";
    else if (e.type === "PROJECT_CANCELLED") projectStatus = "CANCELLED";
    if (e.type === "APPROVAL_REQUIRED") pendingApprovals.add(String(e.payload?.approval_id ?? e.id));
    if (e.type.startsWith("APPROVAL_") && e.type !== "APPROVAL_REQUIRED") {
      const id = String(e.payload?.approval_id ?? "");
      if (id) pendingApprovals.delete(id);
      else pendingApprovals.clear();
    }
    if (!e.task_id) continue;
    const next = TRANSITIONS[e.type];
    const prev = statuses.get(e.task_id);
    // a late event for a finished task (e.g. a review event) must not reopen it, except a revision
    if (next && !(prev && TERMINAL.has(prev) && next !== "REVISION_REQUESTED" && next !== "QUEUED")) statuses.set(e.task_id, next);
    else if (!prev) statuses.set(e.task_id, "PENDING");
    if (e.type === "AGENT_STEP" || e.type === "TOOL_CALLED" || e.type === "TASK_COMPLETED") actions.set(e.task_id, e.message);
  }
  if (projectStatus === "RUNNING" && pendingApprovals.size > 0) projectStatus = "WAITING_APPROVAL";
  return { statuses, actions, projectStatus, approvalsPending: pendingApprovals.size };
}
