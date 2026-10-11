import dagre from "@dagrejs/dagre";
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import clsx from "clsx";
import { RotateCcw, ShieldCheck, Split } from "lucide-react";
import { memo, useEffect, useMemo } from "react";

import { titleCase, usd } from "../lib/format";
import { clusterLayout } from "../lib/graphLayout";
import type { AgentGraphNode, GraphEdge, GraphNode } from "../lib/types";
import { statusStyle } from "./ui";

const NODE_W = 248;
const NODE_H = 104;
const AGENT_W = 210;
const AGENT_H = 84;

const EDGE_STYLE: Record<string, { stroke: string; dash?: string; label?: string }> = {
  dependency: { stroke: "#94a3b8" },
  delegation: { stroke: "#6366f1", dash: "6 4", label: "delegates" },
  review: { stroke: "#d946ef", dash: "3 3", label: "reviews" },
  plan: { stroke: "#8b5cf6", label: "plans" },
};

export function layout<T extends Record<string, unknown>>(nodes: Node<T>[], edges: { source: string; target: string }[], w: number, h: number, dir: "LR" | "TB" = "LR"): Node<T>[] {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: dir, nodesep: 28, ranksep: 70, marginx: 20, marginy: 20 });
  g.setDefaultEdgeLabel(() => ({}));
  nodes.forEach((n) => g.setNode(n.id, { width: w, height: h }));
  edges.forEach((e) => g.setEdge(e.source, e.target));
  dagre.layout(g);
  return nodes.map((n) => {
    const p = g.node(n.id);
    return { ...n, position: { x: p.x - w / 2, y: p.y - h / 2 } };
  });
}

type TaskNodeData = GraphNode & { selected?: boolean } & Record<string, unknown>;

const TaskNode = memo(function TaskNode({ data }: NodeProps<Node<TaskNodeData>>) {
  const st = statusStyle(data.status);
  const Icon = data.kind === "review" ? ShieldCheck : data.kind === "subtask" ? Split : null;
  return (
    <div
      className={clsx("cursor-pointer rounded-xl border-2 px-3 py-2 shadow-sm transition-shadow hover:shadow-md", st.node, data.selected && "ring-2 ring-brand-500 ring-offset-2")}
      style={{ width: NODE_W, height: NODE_H }}
    >
      <Handle type="target" position={Position.Left} className="!h-2 !w-2 !border-0 !bg-slate-300" />
      <div className="flex items-center justify-between gap-2">
        <span className="flex min-w-0 items-center gap-1.5">
          <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: data.color }} />
          <span className="truncate text-[11px] font-semibold text-slate-600">{data.agent_instance || data.agent_name}</span>
        </span>
        <span className={clsx("flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-semibold ring-1 ring-inset", st.badge)}>
          <span className={clsx("h-1.5 w-1.5 rounded-full", st.dot)} />
          {titleCase(data.status)}
        </span>
      </div>
      <div className="mt-1 flex items-start gap-1">
        {Icon && <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-400" />}
        <div className="line-clamp-2 text-[12.5px] font-medium leading-snug text-slate-900">{data.title}</div>
      </div>
      <div className="mt-1 truncate text-[10.5px] text-slate-500" title={data.current_action || data.output_summary}>
        {data.status === "COMPLETED" ? data.output_summary : data.current_action || data.capability}
      </div>
      <div className="mt-0.5 flex items-center gap-2 text-[10px] text-slate-400">
        <span>{usd(data.spent_usd)}</span>
        {data.quality_score !== null && <span className="font-medium text-emerald-600">QA {(data.quality_score * 100).toFixed(0)}</span>}
        {data.revision > 0 && (
          <span className="flex items-center gap-0.5 font-medium text-orange-600">
            <RotateCcw className="h-2.5 w-2.5" />rev {data.revision}
          </span>
        )}
        {data.attempt > 1 && <span className="font-medium text-orange-600">attempt {data.attempt}</span>}
      </div>
      <Handle type="source" position={Position.Right} className="!h-2 !w-2 !border-0 !bg-slate-300" />
    </div>
  );
});

const AgentNode = memo(function AgentNode({ data }: NodeProps<Node<AgentGraphNode & Record<string, unknown>>>) {
  return (
    <div className={clsx("rounded-xl border-2 bg-white px-3 py-2 shadow-sm", data.active > 0 && "node-running")} style={{ width: AGENT_W, height: AGENT_H, borderColor: data.color }}>
      <Handle type="target" position={Position.Left} className="!h-2 !w-2 !border-0 !bg-slate-300" />
      <div className="flex items-center gap-2">
        <span className="flex h-7 w-7 items-center justify-center rounded-lg text-xs font-bold text-white" style={{ background: data.color }}>
          {data.name.slice(0, 2).toUpperCase()}
        </span>
        <div className="min-w-0">
          <div className="truncate text-[12.5px] font-semibold text-slate-900">{data.name}</div>
          <div className="truncate text-[10.5px] text-slate-500">{data.role}</div>
        </div>
      </div>
      <div className="mt-2 flex items-center gap-3 text-[10.5px] text-slate-500">
        <span>
          <b className="text-slate-700">{data.completed}</b>/{data.tasks} tasks
        </span>
        {data.active > 0 && <span className="font-semibold text-blue-600">{data.active} active</span>}
        <span className="ml-auto">{usd(data.spent_usd)}</span>
      </div>
      <Handle type="source" position={Position.Right} className="!h-2 !w-2 !border-0 !bg-slate-300" />
    </div>
  );
});

const nodeTypes = { task: TaskNode, agent: AgentNode };

function toEdges(edges: GraphEdge[], animatedTargets: Set<string>): Edge[] {
  return edges.map((e) => {
    const style = EDGE_STYLE[e.kind] ?? EDGE_STYLE.dependency;
    return {
      id: e.id,
      source: e.source,
      target: e.target,
      animated: animatedTargets.has(e.target) || e.kind === "delegation",
      label: e.count && e.count > 1 ? `${style.label ?? ""} ×${e.count}`.trim() : style.label,
      labelStyle: { fontSize: 10, fill: style.stroke, fontWeight: 600 },
      labelBgStyle: { fill: "#f8fafc" },
      style: { stroke: style.stroke, strokeWidth: 1.6, strokeDasharray: style.dash },
      markerEnd: { type: MarkerType.ArrowClosed, color: style.stroke, width: 16, height: 16 },
    };
  });
}

function FitOnChange({ count }: { count: number }) {
  const rf = useReactFlow();
  useEffect(() => {
    const t = setTimeout(() => rf.fitView({ padding: 0.15, duration: 300 }), 50);
    return () => clearTimeout(t);
  }, [count, rf]);
  return null;
}

interface TaskDagProps {
  nodes: GraphNode[];
  edges: GraphEdge[];
  selected?: string | null;
  onSelect?: (id: string) => void;
  height: number | string;
}

function TaskDagInner({ nodes, edges, selected, onSelect, height }: TaskDagProps) {
  const structure = nodes.map((n) => n.id).join(",") + "|" + edges.map((e) => e.id).join(",");
  const positioned = useMemo(() => {
    const base: Node<TaskNodeData>[] = nodes.map((n) => ({ id: n.id, type: "task", position: { x: 0, y: 0 }, data: n as TaskNodeData }));
    return layout(base, edges, NODE_W, NODE_H);
    // re-layout only when the graph structure changes, not on every status tick
  }, [structure]);
  const byId = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);
  const flowNodes = positioned.map((n) => ({ ...n, data: { ...(byId.get(n.id) ?? n.data), selected: n.id === selected } }));
  const running = new Set(nodes.filter((n) => n.status === "RUNNING").map((n) => n.id));

  return (
    <div style={{ height }} className="rounded-xl border border-slate-200 bg-slate-50/50">
      <ReactFlow
        nodes={flowNodes}
        edges={toEdges(edges, running)}
        nodeTypes={nodeTypes}
        onNodeClick={(_, n) => onSelect?.(n.id)}
        nodesDraggable={false}
        nodesConnectable={false}
        fitView
        minZoom={0.15}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={20} color="#e2e8f0" />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable nodeColor={(n) => (n.data as unknown as GraphNode).color} maskColor="rgb(241 245 249 / 0.7)" />
        <FitOnChange count={nodes.length} />
      </ReactFlow>
    </div>
  );
}

export function TaskDag(props: TaskDagProps) {
  return (
    <ReactFlowProvider>
      <TaskDagInner {...props} />
    </ReactFlowProvider>
  );
}

export function AgentGraph({ nodes, edges, height = 480 }: { nodes: AgentGraphNode[]; edges: GraphEdge[]; height?: number }) {
  const flowEdges = toEdges(edges, new Set(nodes.filter((n) => n.active > 0).map((n) => n.id)));
  const flowNodes = layout(
    nodes.map((n) => ({ id: n.id, type: "agent", position: { x: 0, y: 0 }, data: n as AgentGraphNode & Record<string, unknown> })),
    edges,
    AGENT_W,
    AGENT_H,
  );
  return (
    <ReactFlowProvider>
      <div style={{ height }} className="rounded-xl border border-slate-200 bg-slate-50/50">
        <ReactFlow nodes={flowNodes} edges={flowEdges} nodeTypes={nodeTypes} nodesDraggable={false} nodesConnectable={false} fitView proOptions={{ hideAttribution: true }}>
          <Background gap={20} color="#e2e8f0" />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
    </ReactFlowProvider>
  );
}

const ENTITY_COLORS: Record<string, string> = {
  company: "#4f46e5",
  person: "#0891b2",
  contact: "#0891b2",
  product: "#16a34a",
  customer: "#d97706",
  ticket: "#e11d48",
  industry: "#7c3aed",
  technology: "#0d9488",
};

export function EntityGraph({ entities, relationships, height = 520, selected, onSelect }: {
  entities: { id: string; name: string; type: string; trust_level: string }[];
  relationships: { id: string; source_entity_id: string; target_entity_id: string; type: string }[];
  height?: number;
  selected?: string | null;
  onSelect?: (id: string) => void;
}) {
  const { rels, positions, home } = useMemo(() => {
    const ids = new Set(entities.map((e) => e.id));
    const rels = relationships.filter((r) => ids.has(r.source_entity_id) && ids.has(r.target_entity_id));
    const edges = rels.map((r) => ({ source: r.source_entity_id, target: r.target_entity_id }));
    return { rels, ...clusterLayout([...ids], edges, 180, 48) };
  }, [entities, relationships]);
  const nodes: Node[] = entities.map((e) => {
    const color = ENTITY_COLORS[e.type] ?? "#64748b";
    return {
      id: e.id,
      position: positions.get(e.id) ?? { x: 0, y: 0 },
      data: { label: `${e.name}\n${e.type}` },
      style: {
        width: 180,
        fontSize: 11,
        whiteSpace: "pre-line" as const,
        borderRadius: 10,
        border: `2px solid ${color}`,
        borderStyle: e.trust_level === "unverified" ? "dashed" : "solid",
        background: "white",
        color: "#0f172a",
        boxShadow: e.id === selected ? `0 0 0 3px ${color}55` : undefined,
      },
    };
  });
  // Small graphs show everything; large ones show cluster spokes plus the selected node's links.
  const small = rels.length <= 40;
  const flowEdges: Edge[] = rels
    .filter((r) => small || home.get(r.source_entity_id) === r.target_entity_id || r.source_entity_id === selected || r.target_entity_id === selected)
    .map((r) => {
      const focused = !small && (r.source_entity_id === selected || r.target_entity_id === selected);
      return {
        id: r.id,
        source: r.source_entity_id,
        target: r.target_entity_id,
        type: "straight",
        label: small || focused ? titleCase(r.type) : undefined,
        labelStyle: { fontSize: 10, fill: "#64748b" },
        style: { stroke: focused ? "#6366f1" : "#94a3b8", strokeOpacity: small || focused ? 1 : 0.5 },
        markerEnd: { type: MarkerType.ArrowClosed, color: focused ? "#6366f1" : "#94a3b8" },
      };
    });
  return (
    <ReactFlowProvider>
      <div style={{ height }} className="rounded-xl border border-slate-200 bg-slate-50/50">
        <ReactFlow nodes={nodes} edges={flowEdges} onNodeClick={(_, n) => onSelect?.(n.id)} nodesDraggable={false} nodesConnectable={false} fitView minZoom={0.1} proOptions={{ hideAttribution: true }}>
          <Background gap={20} color="#e2e8f0" />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
    </ReactFlowProvider>
  );
}

export function EdgeLegend() {
  return (
    <div className="flex flex-wrap items-center gap-4 text-xs text-slate-500">
      {Object.entries(EDGE_STYLE)
        .filter(([k]) => k !== "plan")
        .map(([k, s]) => (
          <span key={k} className="flex items-center gap-1.5">
            <svg width="26" height="6" aria-hidden>
              <line x1="0" y1="3" x2="26" y2="3" stroke={s.stroke} strokeWidth="2" strokeDasharray={s.dash} />
            </svg>
            {titleCase(k)}
          </span>
        ))}
    </div>
  );
}
