import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, root } from "./client";
import type { Allowance, Automation, Card, Job } from "./client";

export type ProjectSummary = {
  id: string;
  title: string;
  goal: string;
  conversationId: string;
  status: string;
  createdAt: number | null;
  updatedAt: number;
  runCount: number;
  reportCount: number;
  allowance: Allowance | null;
};
export type ReportSummary = {
  id: string;
  projectId: string;
  projectTitle: string;
  runId: string;
  title: string;
  version: number;
  createdAt: number;
  exportStatus: string;
  artifactCount: number;
};
export type JobSummary = {
  id: string;
  runId: string;
  kind: string;
  status: string;
  command: string | null;
  createdAt: number;
  startedAt: number | null;
  finishedAt: number | null;
};
export type ApprovedPlan = Pick<
  Card,
  | "title"
  | "goal"
  | "methodology"
  | "parameters"
  | "fields"
  | "allocation"
  | "deliverables"
  | "limitations"
>;
export type RunSummary = {
  id: string;
  projectId: string;
  projectTitle: string;
  conversationId: string;
  planId: string;
  title: string;
  status: string;
  displayStatus: string;
  error: string | null;
  createdAt: number;
  finishedAt: number | null;
  automation: Automation | null;
  jobCount: number;
  completedJobs: number;
  commandCount: number;
  completedCommands: number;
  currentJob: {
    id: string;
    command: string;
    status: string;
    createdAt?: number;
    finishedAt?: number;
  } | null;
  reportCount: number;
  latestReport: ReportSummary | null;
  usage: { spentCost: number; reservedCost: number; elapsedSeconds: number };
};
export type RunOverview = RunSummary & {
  approvedPlan: ApprovedPlan;
  approvedAt: number;
};
export type Page<T> = {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
  stats: Record<string, number>;
};
export type OutputWindow = {
  stdout: string;
  stderr: string;
  exitCode: number | null;
  truncated: boolean;
  status: string;
};
export type Source = {
  id: string;
  title?: string;
  kind?: string;
  url?: string;
};

export function changed() {
  window.dispatchEvent(new Event("trustrepro:changed"));
}
export function query(
  values: Record<string, string | number | boolean | undefined | null>,
) {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "")
      params.set(key, String(value));
  });
  return params.toString();
}

// One in-flight read per mounted query; switching queries aborts and fences old responses.
export function useRead<T>(path: string | undefined, interval = 4000) {
  const [value, setValue] = useState<{
    path?: string;
    data?: T;
    error: string;
  }>({ error: "" });
  const reload = useRef<() => void>(() => {});
  useEffect(() => {
    if (!path) return;
    let disposed = false,
      pending = false,
      dirty = false;
    const controller = new AbortController();
    const load = async () => {
      dirty = true;
      if (pending) return;
      pending = true;
      while (dirty && !disposed) {
        dirty = false;
        try {
          const data = await api<T>(path, undefined, "GET", controller.signal);
          if (!disposed) setValue({ path, data, error: "" });
        } catch (error) {
          if (!disposed)
            setValue((old) => ({
              path,
              data: old.path === path ? old.data : undefined,
              error: String(error),
            }));
        }
      }
      pending = false;
    };
    reload.current = () => void load();
    void load();
    const timer = interval
      ? window.setInterval(() => void load(), interval)
      : undefined;
    const update = () => void load();
    window.addEventListener("trustrepro:changed", update);
    return () => {
      disposed = true;
      controller.abort();
      window.clearInterval(timer);
      window.removeEventListener("trustrepro:changed", update);
    };
  }, [path, interval]);
  return {
    data: value.path === path ? value.data : undefined,
    error: value.path === path ? value.error : "",
    reload: () => reload.current(),
  };
}

type ListState = {
  page: number;
  pageSize: number;
  order: "asc" | "desc";
  q: string;
  status: string;
  projectId: string;
  includeHistory: boolean;
  hasReports: boolean;
};
const listMemory = new Map<string, string>();
export const groupMemory = new Map<string, boolean>();
export const scrollMemory = new Map<string, number>();
export function useScrollMemory(key: string, ready: boolean) {
  const saved = useRef(scrollMemory.get(key) || 0);
  const restored = useRef(false);
  useLayoutEffect(() => {
    if (!ready || restored.current) return;
    const area = document.querySelector<HTMLElement>(".management-page");
    if (!area) return;
    let observer: ResizeObserver | undefined;
    const restore = () => {
      area.scrollTop = saved.current;
      if (Math.abs(area.scrollTop - saved.current) < 2) observer?.disconnect();
    };
    observer = new ResizeObserver(restore);
    observer.observe(area.firstElementChild || area);
    restore();
    restored.current = true;
    const stop = () => observer?.disconnect();
    area.addEventListener("wheel", stop, { passive: true });
    area.addEventListener("pointerdown", stop);
    area.addEventListener("keydown", stop);
    return () => {
      observer?.disconnect();
      area.removeEventListener("wheel", stop);
      area.removeEventListener("pointerdown", stop);
      area.removeEventListener("keydown", stop);
    };
  }, [ready, key]);
  useEffect(() => {
    const area = document.querySelector<HTMLElement>(".management-page");
    const remember = () => {
      if (area && restored.current) scrollMemory.set(key, area.scrollTop);
    };
    area?.addEventListener("scroll", remember);
    return () => area?.removeEventListener("scroll", remember);
  }, [key]);
}
export function useListState(key: string) {
  const [params, setParams] = useSearchParams();
  const raw = params.toString() || listMemory.get(key) || "";
  const p = new URLSearchParams(raw);
  const integer = (field: string, fallback: number) =>
    Math.max(1, Math.floor(Number(p.get(field)) || fallback));
  const state: ListState = {
    page: integer("page", 1),
    pageSize: Math.min(50, integer("pageSize", 10)),
    order: p.get("order") === "asc" ? "asc" : "desc",
    q: p.get("q") || "",
    status: p.get("status") || "all",
    projectId: p.get("projectId") || "",
    includeHistory: p.get("includeHistory") === "true",
    hasReports: p.get("hasReports") === "true",
  };
  function set(values: Partial<ListState>, reset = true) {
    const next = { ...state, ...(reset ? { page: 1 } : {}), ...values };
    const serialized = query(next);
    listMemory.set(key, serialized);
    setParams(serialized, { replace: true });
  }
  useEffect(() => {
    listMemory.set(key, raw);
  }, [key, raw]);
  return [state, set] as const;
}

export async function downloadBodies(ids: string[]) {
  const response = await fetch(root + "/reports/batch-download", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reportIds: ids }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.message || "下载失败");
  }
  const href = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.download = "report-bodies.zip";
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(href), 1000);
}
export const projectLink = (id: string) => `/projects/${id}`;
export const runLink = (run: { id: string; projectId: string }) =>
  `${projectLink(run.projectId)}/runs/${run.id}`;
export const reportLink = (report: { id: string; projectId: string }) =>
  `${projectLink(report.projectId)}/reports/${report.id}`;
export const time = (value?: number | null) =>
  value == null
    ? "—"
    : new Intl.DateTimeFormat("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      })
        .format(value)
        .replace("/", "-");
export { money } from "./money";
export const duration = (seconds: number) =>
  `${Math.floor(seconds / 60)} 分 ${String(Math.floor(seconds % 60)).padStart(2, "0")} 秒`;
export const asJob = (value: JobSummary): Job => ({
  ...value,
  startedAt: value.startedAt ?? undefined,
  finishedAt: value.finishedAt ?? undefined,
  arguments: { command: value.command },
});
