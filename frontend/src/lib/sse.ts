import { useEffect, useRef, useState } from "react";

import { streamUrl } from "./api";
import type { EventItem } from "./types";

export type StreamState = "connecting" | "live" | "reconnecting" | "closed";

/** Subscribe to an AgentOS server-sent event stream. The server replays a backlog on connect, so
 * reconnects are deduplicated by event id. `onEvent` fires for every new event (e.g. to invalidate queries). */
export function useEventStream(path: string | null, onEvent?: (e: EventItem) => void, limit = 500) {
  const [events, setEvents] = useState<EventItem[]>([]);
  const [state, setState] = useState<StreamState>("connecting");
  const seen = useRef(new Set<string>());
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    if (!path) return;
    seen.current = new Set();
    setEvents([]);
    setState("connecting");
    const es = new EventSource(streamUrl(path));
    es.onopen = () => setState("live");
    es.onerror = () => setState(es.readyState === EventSource.CLOSED ? "closed" : "reconnecting");
    es.addEventListener("event", (msg) => {
      const e = JSON.parse((msg as MessageEvent<string>).data) as EventItem;
      if (seen.current.has(e.id)) return;
      seen.current.add(e.id);
      setEvents((prev) => {
        const next = [...prev, e];
        return next.length > limit ? next.slice(next.length - limit) : next;
      });
      handler.current?.(e);
    });
    return () => {
      es.close();
      setState("closed");
    };
  }, [path, limit]);

  return { events, state };
}
