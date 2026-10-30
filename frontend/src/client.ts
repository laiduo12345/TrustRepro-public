import { useRef } from "react";
export type Json =
  | null
  | boolean
  | number
  | string
  | Json[]
  | { [key: string]: Json };
export type Artifact = {
  id: string;
  name: string;
  size: number;
  mediaType: string;
};
export type Report = {
  id: string;
  projectId?: string;
  createdAt: number;
  runId: string;
  version: number;
  result: Record<string, Json>;
  artifacts: Artifact[];
  exportStatus: string;
  error?: string;
};
export type Job = {
  id: string;
  kind: string;
  status: string;
  arguments: Record<string, Json>;
  result?: Json;
  error?: string;
  startedAt?: number;
  finishedAt?: number;
  createdAt?: number;
  progress?: Record<string, Json>;
};
export type Run = {
  id: string;
  createdAt: number;
  projectId: string;
  status: string;
  error?: string;
  jobs: Job[];
  automation?: Automation;
};
export type Question = {
  id: string;
  blocking: boolean;
  questions: {
    id: string;
    header: string;
    question: string;
    options?: { label: string; description: string }[];
  }[];
};
export type Automation = {
  state: string;
  reason?: string;
  retryAt?: number;
  recoveryCount: number;
  questions: Question[];
  allowedActions: string[];
};
export type Field = {
  key: string;
  label: string;
  binding: string;
  shape: { type: string; choices?: Json[] };
};
export type Allocation = {
  currency?: "CNY";
  maxCost: number;
  maxHours: number;
  resources: { cpus: number; memoryGb: number; diskGb: number; gpus: number };
};
export type Card = {
  id: string;
  version: number;
  revision: number;
  title: string;
  goal: string;
  methodology: string;
  parameters: Json;
  fields: Field[];
  deliverables: string[];
  limitations: string[];
  allocation: Allocation;
  status: string;
};
export type Message = {
  id: string;
  role: string;
  content: string;
  status: string;
  createdAt: number;
  blocks?: MessageBlock[];
};
export type NativeBlock = {
  type: "native";
  sessionId: string;
  turnId: string | null;
  itemId: string;
  model: string;
  turnStatus: "streaming" | "completed" | "interrupted";
};
export type ReasoningBlock = {
  type: "reasoning";
  text: string[];
  summary: string[];
  status: "streaming" | "completed" | "interrupted";
  startedAt: number | null;
  finishedAt: number | null;
  intervals: { startedAt: number; finishedAt: number | null }[];
};
export type MessageBlock = NativeBlock | ReasoningBlock | { type: string; [key: string]: unknown };
export type MessageDelta = Omit<Message, "id"> & { messageId: string; offset: number };
export type ConversationEvent = { id: number; kind: string; payload: MessageDelta | Record<string, unknown> };
export type ModelRole = "research" | "execution";
export type ModelSelection = { connectionId: string; model: string };
export type ModelSelections = Record<ModelRole, ModelSelection>;
export type ModelBindings = Record<
  ModelRole,
  Partial<ModelSelection> & {
    version?: number;
    canChange: boolean;
  }
>;
export type Chat = {
  composerState: "idle" | "working" | "pausing" | "paused";
  proposalState?: string;
  modelBindings: ModelBindings;
  automation?: Automation;
  questions?: Question[];
  eventCursor: number;
  id: string;
  title: string;
  projectId?: string;
  status: string;
  messages: Message[];
  card?: Card;
  runs: Run[];
  reports: Report[];
  allowance: Allowance | null;
  error?: string;
};
export type Allowance = Allocation & {
  spentCost: number;
  reservedCost: number;
  elapsedSeconds: number;
  remainingSeconds: number;
};
export type Project = {
  allowance: Allowance | null;
  id: string;
  title: string;
  goal: string;
  conversationId: string;
};
export type Workspace = { projects: Project[]; runs: Run[]; reports: Report[] };
export const root = "/api/v1";
export async function api<T>(
  path: string,
  body?: unknown,
  method = "POST",
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(
    root + path,
    body === undefined
      ? { signal }
      : {
          method,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
          signal,
        },
  );
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(
      typeof error.message === "string"
        ? error.message
        : typeof error.detail === "string"
          ? error.detail
          : `请求失败 (${response.status})`,
    );
  }
  return response.json();
}
export const uuid = () => crypto.randomUUID();
export const pretty = (value: unknown) => JSON.stringify(value, null, 2);
export const statuses: Record<string, string> = {
  queued: "排队中",
  running: "运行中",
  thinking: "处理中",
  compacting: "整理上下文",
  executing: "执行中",
  waiting_user: "等待输入",
  ready: "等待确认",
  completed: "已完成",
  waiting_job: "等待作业",
  preparing_artifacts: "生成产物包",
  recoverable_error: "可继续处理",
  recovering: "正在自动恢复",
  blocked: "需要处理",
  paused: "已暂停",
  cancelled: "已取消",
  RETRYING: "正在自动重试",
  BUDGET_LIMITED: "等待追加额度",
  approved: "已批准",
  replaced: "已替换",
  QUEUED: "排队中",
  RUNNING: "运行中",
  COMPLETED: "已完成",
  FAILED: "失败",
  PAUSED: "已暂停",
  CANCELLED: "已取消",
  CANCELLING: "正在停止",
  PAUSING: "正在暂停",
  WAITING: "准备导出",
  READY: "可下载",
  INTERRUPTED: "已中断",
};

// An intent keeps its key through a lost receipt; a successful action ends it.
export function useIntent() {
  const pending = useRef<{ key: string; body: string } | null>(null);
  return {
    key(body: unknown) {
      const value = JSON.stringify(body);
      if (!pending.current || pending.current.body !== value)
        pending.current = { key: uuid(), body: value };
      return pending.current.key;
    },
    done() {
      pending.current = null;
    },
  };
}
