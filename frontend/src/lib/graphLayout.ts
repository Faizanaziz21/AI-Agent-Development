export type Point = { x: number; y: number };

/**
 * Hub-and-spoke layout for entity graphs, where layered layouts degenerate (hundreds of
 * companies pointing at a handful of industries or technologies become one enormous rank).
 * Nodes pointed at by two or more others are hubs; every other node joins the cluster of its
 * most specific hub (the one with the fewest members) and is placed on a ring around it.
 * Clusters are tiled in a grid; nodes with no hub are gridded underneath.
 */
export function clusterLayout(ids: string[], edges: { source: string; target: string }[], w: number, h: number): Map<string, Point> {
  const known = new Set(ids);
  const indeg = new Map<string, number>();
  const targets = new Map<string, string[]>();
  for (const e of edges) {
    if (!known.has(e.source) || !known.has(e.target) || e.source === e.target) continue;
    indeg.set(e.target, (indeg.get(e.target) ?? 0) + 1);
    targets.set(e.source, [...(targets.get(e.source) ?? []), e.target]);
  }
  const hubs = ids.filter((id) => (indeg.get(id) ?? 0) >= 2);
  const isHub = new Set(hubs);
  const members = new Map<string, string[]>(hubs.map((hub) => [hub, []]));
  const loose: string[] = [];
  for (const id of ids) {
    if (isHub.has(id)) continue;
    const candidates = (targets.get(id) ?? []).filter((t) => isHub.has(t));
    if (!candidates.length) {
      loose.push(id);
      continue;
    }
    const home = candidates.reduce((a, b) => ((indeg.get(b) ?? 0) < (indeg.get(a) ?? 0) ? b : a));
    members.get(home)!.push(id);
  }

  const gap = 24;
  const radius = (n: number) => (n === 0 ? 0 : Math.max(w, (n * (w + gap)) / (2 * Math.PI)));
  const clusters = hubs
    .map((hub) => ({ hub, members: members.get(hub)!, r: radius(members.get(hub)!.length) }))
    .sort((a, b) => b.members.length - a.members.length);
  const cell = clusters.length ? 2 * Math.max(...clusters.map((c) => c.r)) + w + 2 * gap : 0;
  const cols = Math.max(1, Math.ceil(Math.sqrt(clusters.length)));

  const pos = new Map<string, Point>();
  clusters.forEach((c, i) => {
    const cx = (i % cols) * cell + cell / 2;
    const cy = Math.floor(i / cols) * cell + cell / 2;
    pos.set(c.hub, { x: cx - w / 2, y: cy - h / 2 });
    c.members.forEach((id, j) => {
      const angle = (2 * Math.PI * j) / c.members.length - Math.PI / 2;
      pos.set(id, { x: cx + c.r * Math.cos(angle) - w / 2, y: cy + c.r * Math.sin(angle) - h / 2 });
    });
  });

  const top = clusters.length ? Math.ceil(clusters.length / cols) * cell : 0;
  const looseCols = Math.max(1, Math.ceil(Math.sqrt(loose.length * 1.6)));
  loose.forEach((id, i) => {
    pos.set(id, { x: (i % looseCols) * (w + gap), y: top + Math.floor(i / looseCols) * (h + gap) });
  });
  return pos;
}
