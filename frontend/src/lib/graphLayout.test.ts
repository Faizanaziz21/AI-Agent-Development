import { describe, expect, it } from "vitest";

import { clusterLayout } from "./graphLayout";

const W = 180;
const H = 48;

function overlaps(a: { x: number; y: number }, b: { x: number; y: number }) {
  return Math.abs(a.x - b.x) < W && Math.abs(a.y - b.y) < H;
}

describe("clusterLayout", () => {
  it("places every node without overlaps", () => {
    const companies = Array.from({ length: 60 }, (_, i) => `c${i}`);
    const edges = companies.flatMap((c, i) => [
      { source: c, target: `ind${i % 6}` },
      ...(i % 2 ? [{ source: c, target: "intune" }] : []),
    ]);
    const ids = [...companies, ...Array.from({ length: 6 }, (_, i) => `ind${i}`), "intune", "orphan1", "orphan2"];
    const pos = clusterLayout(ids, edges, W, H);

    expect(pos.size).toBe(ids.length);
    const points = ids.map((id) => pos.get(id)!);
    for (let i = 0; i < points.length; i++) for (let j = i + 1; j < points.length; j++) expect(overlaps(points[i], points[j])).toBe(false);
  });

  it("groups members around their most specific hub", () => {
    const edges = [
      { source: "a", target: "industry" },
      { source: "b", target: "industry" },
      { source: "a", target: "popular" },
      { source: "b", target: "popular" },
      { source: "c", target: "popular" },
    ];
    const pos = clusterLayout(["a", "b", "c", "industry", "popular"], edges, W, H);
    const dist = (p: string, q: string) => Math.hypot(pos.get(p)!.x - pos.get(q)!.x, pos.get(p)!.y - pos.get(q)!.y);

    expect(dist("a", "industry")).toBeLessThan(dist("a", "popular"));
    expect(dist("c", "popular")).toBeLessThan(dist("c", "industry"));
  });

  it("grids a graph with no edges instead of stacking it in one column", () => {
    const ids = Array.from({ length: 132 }, (_, i) => `n${i}`);
    const pos = clusterLayout(ids, [], W, H);
    const xs = new Set([...pos.values()].map((p) => p.x));
    const ys = new Set([...pos.values()].map((p) => p.y));

    expect(xs.size).toBeGreaterThan(5);
    expect(ys.size).toBeLessThan(ids.length / 5);
  });
});
