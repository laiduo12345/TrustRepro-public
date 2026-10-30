import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Button, Input, Select, Skeleton } from "antd";
import {
  AppstoreOutlined,
  ArrowRightOutlined,
  ClockCircleOutlined,
  DownloadOutlined,
  ExperimentOutlined,
  FileTextOutlined,
  MessageOutlined,
  PauseOutlined,
  PlayCircleOutlined,
  RightOutlined,
  SearchOutlined,
  WalletOutlined,
} from "@ant-design/icons";
import { api, root, statuses, useIntent } from "./client";
import type { Report } from "./client";
import { PageHeader, Status } from "./ui";
import { useHeading } from "./shell-context";
import { RunWorkspace } from "./runs";
import { ReportPanel, ReportRecovery } from "./reports";
import {
  ApprovedSnapshot,
  Back,
  BudgetPanel,
  LoadError,
  Pager,
  Stats,
  TimeSort,
  useDebounced,
  WPanel,
} from "./workspace-ui";
import {
  changed,
  downloadBodies,
  duration,
  groupMemory,
  money,
  projectLink,
  query,
  reportLink,
  runLink,
  time,
  useListState,
  useRead,
  useScrollMemory,
} from "./workspace-client";
import type {
  Page,
  ProjectSummary,
  ReportSummary,
  RunOverview,
  RunSummary,
} from "./workspace-client";

const activeStates = [
  "RUNNING",
  "QUEUED",
  "thinking",
  "executing",
  "waiting_job",
  "recovering",
  "queued",
  "compacting",
];
const sum = (stats: Record<string, number>, keys: string[]) =>
  keys.reduce((n, key) => n + (stats[key] || 0), 0);
const names = { projects: "项目工作台", runs: "运行中心", reports: "报告中心" };
function Identity({
  title,
  sub,
  to,
  kind,
}: {
  title: string;
  sub?: string;
  to: string;
  kind: "projects" | "runs" | "reports";
}) {
  return (
    <div className="table-identity-v2">
      <span className="table-symbol">
        {kind === "projects" ? (
          <ExperimentOutlined />
        ) : kind === "runs" ? (
          <PlayCircleOutlined />
        ) : (
          <FileTextOutlined />
        )}
      </span>
      <div className="identity-text">
        <Link to={to} title={title}>
          {title}
        </Link>
        {sub && <small>{sub}</small>}
      </div>
    </div>
  );
}
function StateStats({
  kind,
  stats,
}: {
  kind: keyof typeof names;
  stats: Record<string, number>;
}) {
  const working = sum(stats, activeStates),
    paused = sum(stats, ["paused", "PAUSED"]);
  return (
    <Stats
      items={
        kind === "projects"
          ? [
              ["项目", stats.total || 0, <AppstoreOutlined />],
              ["待确认", stats.ready || 0, <ClockCircleOutlined />],
              ["执行中", working, <PlayCircleOutlined />],
              ["已暂停", paused, <PauseOutlined />],
            ]
          : kind === "runs"
            ? [
                ["运行", stats.total || 0, <PlayCircleOutlined />],
                ["执行中", working, <PlayCircleOutlined />],
                ["已暂停", paused, <PauseOutlined />],
                [
                  "已完成",
                  sum(stats, ["COMPLETED", "completed"]),
                  <AppstoreOutlined />,
                ],
              ]
            : [
                ["最新报告", stats.total || 0, <FileTextOutlined />],
                ["产物包就绪", stats.READY || 0, <DownloadOutlined />],
                ["等待重试", stats.FAILED || 0, <ClockCircleOutlined />],
                ["历史版本", stats.history || 0, <FileTextOutlined />],
              ]
      }
    />
  );
}

function Center({ kind }: { kind: keyof typeof names }) {
  const [state, set] = useListState(kind);
  const q = useDebounced(state.q);
  const path = `/${kind}?${query({ page: state.page, pageSize: state.pageSize, order: state.order, q, status: state.status, ...(kind !== "projects" ? { projectId: state.projectId } : {}), ...(kind === "reports" ? { includeHistory: state.includeHistory } : {}) })}`;
  const { data, error, reload } =
    useRead<Page<ProjectSummary | RunSummary | ReportSummary>>(path);
  const [selected, select] = useState(new Set<string>());
  const [actionError, setActionError] = useState("");
  const [busy, setBusy] = useState(false);
  const intent = useIntent();
  const [projectSearch, setProjectSearch] = useState("");
  const projectQuery = useDebounced(projectSearch);
  const projects = useRead<Page<ProjectSummary>>(
    kind !== "projects"
      ? `/projects?pageSize=50&q=${encodeURIComponent(projectQuery)}`
      : undefined,
  );
  const selectedProject = useRead<ProjectSummary>(
    kind !== "projects" && state.projectId
      ? `/projects/${state.projectId}`
      : undefined,
  );
  useHeading(names[kind], kind);
  useScrollMemory(kind, !!data);
  useEffect(() => {
    select(new Set());
  }, [state.q, state.status, state.projectId, state.includeHistory]);
  useEffect(() => {
    if (data && data.page !== state.page) set({ page: data.page }, false);
  }, [data?.page, state.page]);
  const pageReports =
    kind === "reports" ? ((data?.items || []) as ReportSummary[]) : [];
  const checkedCount = pageReports.filter((r) => selected.has(r.id)).length;
  const allSelect = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (allSelect.current)
      allSelect.current.indeterminate =
        checkedCount > 0 && checkedCount < pageReports.length;
  }, [checkedCount, pageReports.length]);
  async function retry(report: ReportSummary) {
    if (busy) return;
    setBusy(true);
    setActionError("");
    try {
      await api(`/reports/${report.id}/retry`, {
        regenerate: false,
        clientRequestId: intent.key({ id: report.id }),
      });
      intent.done();
      changed();
    } catch (e) {
      setActionError(String(e));
    } finally {
      setBusy(false);
    }
  }
  const options = [
    ...new Map(
      [
        ...(projects.data?.items || []),
        ...(selectedProject.data ? [selectedProject.data] : []),
      ].map((p) => [p.id, p]),
    ).values(),
  ];
  return (
    <>
      <PageHeader title={names[kind]} />
      <LoadError error={error || actionError} retry={reload} />
      <>
        {data ? (
          <StateStats kind={kind} stats={data.stats} />
        ) : (
          <div className="stats-loading">
            <Skeleton active paragraph={false} />
          </div>
        )}
        <WPanel
          className="center-list-panel"
          extra={
            <div className="manager-tools">
              <Input
                className="manager-search-input"
                aria-label={`搜索${kind === "projects" ? "项目" : kind === "runs" ? "运行" : "报告"}`}
                prefix={<SearchOutlined />}
                placeholder={`搜索${kind === "projects" ? "项目" : kind === "runs" ? "运行" : "报告"}`}
                allowClear
                value={state.q}
                onChange={(e) => set({ q: e.target.value })}
              />
              {kind !== "projects" && (
                <Select
                  aria-label="项目筛选"
                  value={state.projectId || "all"}
                  showSearch={{
                    onSearch: setProjectSearch,
                    filterOption: false,
                  }}
                  onChange={(value) =>
                    set({ projectId: value === "all" ? "" : value })
                  }
                  options={[
                    { value: "all", label: "全部项目" },
                    ...options.map((p) => ({ value: p.id, label: p.title })),
                  ]}
                />
              )}
              <Select
                aria-label="状态筛选"
                value={state.status}
                onChange={(status) => set({ status })}
                options={[
                  { value: "all", label: "全部状态" },
                  ...[
                    ...new Set([
                      ...Object.keys(data?.stats || {}),
                      ...(state.status !== "all" ? [state.status] : []),
                    ]),
                  ]
                    .filter((s) => !["total", "history"].includes(s))
                    .map((s) => ({ value: s, label: statuses[s] || s })),
                ]}
              />
              {kind === "reports" &&
                ((data?.stats.history || 0) > 0 || state.includeHistory) && (
                  <Button
                    className="history-toggle"
                    aria-pressed={state.includeHistory}
                    icon={<ClockCircleOutlined />}
                    onClick={() =>
                      set({ includeHistory: !state.includeHistory })
                    }
                  >
                    历史版本
                  </Button>
                )}
            </div>
          }
        >
          <div className="manager-table-scroll">
            <table className="manager-table" aria-label={names[kind] + "列表"}>
              <thead>
                <tr>
                  {kind === "reports" && (
                    <th className="select-cell">
                      <input
                        ref={allSelect}
                        type="checkbox"
                        className="report-select"
                        aria-label="选择本页报告"
                        checked={
                          !!pageReports.length &&
                          checkedCount === pageReports.length
                        }
                        onChange={(e) => {
                          const next = new Set(selected);
                          pageReports.forEach((r) =>
                            e.target.checked
                              ? next.add(r.id)
                              : next.delete(r.id),
                          );
                          select(next);
                        }}
                      />
                    </th>
                  )}
                  <th
                    style={{
                      width:
                        kind === "runs"
                          ? "28%"
                          : kind === "projects"
                            ? "33%"
                            : "35%",
                    }}
                  >
                    {kind === "projects"
                      ? "项目"
                      : kind === "runs"
                        ? "项目 / 运行"
                        : "报告 / 项目"}
                  </th>
                  <th>{kind === "reports" ? "版本" : "状态"}</th>
                  <th style={{ width: kind === "runs" ? "29%" : "17%" }}>
                    {kind === "projects"
                      ? "运行 / 报告"
                      : kind === "runs"
                        ? "当前 / 最近命令"
                        : "产物包"}
                  </th>
                  {kind !== "reports" && (
                    <th>{kind === "projects" ? "可用额度" : "活跃时间"}</th>
                  )}
                  <th
                    style={{ width: "15%" }}
                    aria-sort={
                      state.order === "desc" ? "descending" : "ascending"
                    }
                  >
                    <TimeSort
                      label={
                        kind === "projects"
                          ? "更新时间"
                          : kind === "runs"
                            ? "创建时间"
                            : "生成时间"
                      }
                      order={state.order}
                      onChange={() =>
                        set({ order: state.order === "desc" ? "asc" : "desc" })
                      }
                    />
                  </th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {!data && !error && (
                  <tr>
                    <td colSpan={6}>
                      <Skeleton active />
                    </td>
                  </tr>
                )}
                {data?.items.length === 0 && (
                  <tr>
                    <td colSpan={6} className="table-empty">
                      暂无匹配的
                      {kind === "projects"
                        ? "项目"
                        : kind === "runs"
                          ? "运行"
                          : "报告"}
                    </td>
                  </tr>
                )}
                {kind === "projects"
                  ? ((data?.items || []) as ProjectSummary[]).map((p) => (
                      <tr key={p.id}>
                        <td>
                          <Identity
                            title={p.title}
                            to={projectLink(p.id)}
                            kind={kind}
                          />
                        </td>
                        <td>
                          <Status value={p.status} />
                        </td>
                        <td>
                          <div className="project-associations">
                            <Link to={`${projectLink(p.id)}?hasReports=false`}>
                              运行 <span>{p.runCount}</span>
                            </Link>
                            <Link
                              to={`${projectLink(p.id)}?hasReports=true`}
                              title="含历史版本"
                            >
                              报告 <span>{p.reportCount}</span>
                            </Link>
                          </div>
                        </td>
                        <td className="table-cost">
                          {p.allowance
                            ? money(
                                Math.max(
                                  0,
                                  p.allowance.maxCost -
                                    p.allowance.spentCost -
                                    p.allowance.reservedCost,
                                ),
                              )
                            : "—"}
                        </td>
                        <td className="table-date">{time(p.updatedAt)}</td>
                        <td>
                          <Link className="table-action" to={projectLink(p.id)}>
                            打开项目
                          </Link>
                        </td>
                      </tr>
                    ))
                  : kind === "runs"
                    ? ((data?.items || []) as RunSummary[]).map((r) => (
                        <tr key={r.id}>
                          <td>
                            <Identity
                              title={r.title}
                              sub={`${r.projectTitle} · ${r.id}`}
                              to={runLink(r)}
                              kind={kind}
                            />
                          </td>
                          <td>
                            <Status value={r.displayStatus} />
                          </td>
                          <td>
                            <code
                              className="command-preview"
                              title={r.currentJob?.command || undefined}
                            >
                              {r.currentJob?.command || "—"}
                            </code>
                            <small className="context-meta">
                              {r.completedJobs} / {r.jobCount}
                            </small>
                          </td>
                          <td className="mono">
                            {duration(r.usage.elapsedSeconds)}
                          </td>
                          <td className="table-date">{time(r.createdAt)}</td>
                          <td>
                            <Link className="table-action" to={runLink(r)}>
                              查看输出
                            </Link>
                          </td>
                        </tr>
                      ))
                    : pageReports.map((r) => (
                        <tr
                          key={r.id}
                          className={selected.has(r.id) ? "is-selected" : ""}
                        >
                          <td className="select-cell">
                            <input
                              className="report-select"
                              type="checkbox"
                              aria-label={`选择 ${r.title} v${r.version}`}
                              checked={selected.has(r.id)}
                              onChange={(e) => {
                                const next = new Set(selected);
                                e.target.checked
                                  ? next.add(r.id)
                                  : next.delete(r.id);
                                select(next);
                              }}
                            />
                          </td>
                          <td>
                            <Identity
                              title={r.title}
                              sub={`${r.projectTitle} · ${r.runId}`}
                              to={reportLink(r)}
                              kind="reports"
                            />
                          </td>
                          <td className="mono">v{r.version}</td>
                          <td>
                            <Status value={r.exportStatus} />
                          </td>
                          <td className="table-date">{time(r.createdAt)}</td>
                          <td>
                            <Link className="table-action" to={reportLink(r)}>
                              阅读
                            </Link>
                            {r.exportStatus === "READY" ? (
                              <a
                                className="table-action"
                                href={`${root}/reports/${r.id}/download?format=zip`}
                                download
                              >
                                下载
                              </a>
                            ) : r.exportStatus === "FAILED" ? (
                              <button
                                type="button"
                                className="table-action"
                                disabled={busy}
                                onClick={() => void retry(r)}
                              >
                                重试导出
                              </button>
                            ) : null}
                          </td>
                        </tr>
                      ))}
              </tbody>
            </table>
          </div>
          {data && (
            <Pager
              data={data}
              label={
                kind === "projects"
                  ? "个项目"
                  : kind === "runs"
                    ? "次运行"
                    : "个报告"
              }
              onChange={(page, pageSize) => set({ page, pageSize }, false)}
              extra={
                kind === "reports" && (
                  <Button
                    type="text"
                    disabled={!selected.size || busy}
                    icon={<DownloadOutlined />}
                    onClick={async () => {
                      setBusy(true);
                      setActionError("");
                      try {
                        await downloadBodies([...selected]);
                      } catch (e) {
                        setActionError(String(e));
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    导出所选正文
                    {selected.size > 0 ? `（${selected.size}）` : ""}
                  </Button>
                )
              }
            />
          )}
        </WPanel>
      </>
    </>
  );
}

export function LinkedReport({ report }: { report: ReportSummary }) {
  return (
    <div className="linked-report-row">
      <FileTextOutlined />
      <div className="identity-text">
        <Link to={reportLink(report)} title={report.title}>
          {report.title}
        </Link>
        <small>
          v{report.version} · {time(report.createdAt)}
        </small>
      </div>
      <Status value={report.exportStatus} />
      <div className="linked-report-actions">
        <Link className="table-action" to={reportLink(report)}>
          阅读
        </Link>
        {report.exportStatus === "READY" && (
          <a
            className="icon-link"
            href={`${root}/reports/${report.id}/download?format=zip`}
            download
            aria-label={`下载 ${report.title} v${report.version} 产物包`}
          >
            <DownloadOutlined />
          </a>
        )}
      </div>
    </div>
  );
}
function HistoryReports({
  run,
  latest,
}: {
  run: RunSummary;
  latest: ReportSummary;
}) {
  const [open, setOpen] = useState(
    groupMemory.get("history:" + run.id) || false,
  );
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(10);
  const data = useRead<Page<ReportSummary>>(
    open
      ? `/reports?${query({ runId: run.id, projectId: run.projectId, includeHistory: true, beforeVersion: latest.version, page, pageSize: size })}`
      : undefined,
  );
  useEffect(() => {
    if (data.data && data.data.page !== page) setPage(data.data.page);
  }, [data.data?.page, page]);
  return (
    <details
      className="project-report-history"
      open={open}
      onToggle={(e) => {
        setOpen(e.currentTarget.open);
        groupMemory.set("history:" + run.id, e.currentTarget.open);
      }}
    >
      <summary>
        <RightOutlined />
        历史版本（{run.reportCount - 1}）
      </summary>
      {open && (
        <>
          <LoadError error={data.error} retry={data.reload} />
          {data.data?.items.map((report) => (
            <LinkedReport key={report.id} report={report} />
          ))}
          {data.data && (
            <Pager
              data={data.data}
              label="个版本"
              onChange={(next, capacity) => {
                setPage(next);
                setSize(capacity);
              }}
            />
          )}
        </>
      )}
    </details>
  );
}
function RunGroup({ run, index }: { run: RunSummary; index: number }) {
  const [open, setOpen] = useState(groupMemory.get(run.id) ?? index === 0);
  const overview = useRead<RunOverview>(
    open ? `/runs/${run.id}/overview` : undefined,
  );
  return (
    <details
      className="project-run-group"
      open={open}
      onToggle={(e) => {
        setOpen(e.currentTarget.open);
        groupMemory.set(run.id, e.currentTarget.open);
      }}
    >
      <summary>
        <span className="run-group-chevron">
          <RightOutlined />
        </span>
        <div className="run-group-identity">
          <strong>{run.title}</strong>
          <small>
            <span>{run.id}</span>
            <time>{time(run.createdAt)}</time>
          </small>
        </div>
        <Status value={run.displayStatus} />
        <span className="run-report-count">报告 {run.reportCount}</span>
      </summary>
      {open && (
        <div className="run-group-body">
          <div className="run-group-meta">
            <span>
              作业
              <strong>
                {run.completedJobs} / {run.jobCount}
              </strong>
            </span>
            <span>
              活跃时间<strong>{duration(run.usage.elapsedSeconds)}</strong>
            </span>
            <span>
              费用<strong>{money(run.usage.spentCost)}</strong>
            </span>
            <Link className="table-action" to={runLink(run)}>
              查看运行 <ArrowRightOutlined />
            </Link>
          </div>
          <LoadError error={overview.error} retry={overview.reload} />
          {overview.data ? (
            <ApprovedSnapshot plan={overview.data.approvedPlan} />
          ) : (
            !overview.error && <Skeleton active paragraph={false} />
          )}
          {run.latestReport ? (
            <div className="linked-reports">
              <LinkedReport report={run.latestReport} />
              {run.reportCount > 1 && (
                <HistoryReports run={run} latest={run.latestReport} />
              )}
            </div>
          ) : (
            <div className="run-report-empty">
              <FileTextOutlined />
              暂无报告
            </div>
          )}
        </div>
      )}
    </details>
  );
}

function ProjectPage({ id }: { id: string }) {
  const [state, set] = useListState("project:" + id),
    q = useDebounced(state.q);
  const project = useRead<ProjectSummary>(`/projects/${id}`);
  const runs = useRead<Page<RunSummary>>(
    `/projects/${id}/runs?${query({ page: state.page, pageSize: state.pageSize, order: state.order, q, status: state.status, hasReports: state.hasReports })}`,
  );
  const p = project.data;
  useHeading(p?.title || "项目", "projects", "项目工作台");
  useScrollMemory(id, !!runs.data);
  useEffect(() => {
    if (runs.data && runs.data.page !== state.page)
      set({ page: runs.data.page }, false);
  }, [runs.data?.page, state.page]);
  return (
    <>
      <Back to="/projects">项目工作台</Back>
      <PageHeader
        title={p?.title || "项目"}
        action={
          p && (
            <>
              <Link to={`/chats/${p.conversationId}`}>
                <Button icon={<MessageOutlined />}>项目会话</Button>
              </Link>
              {p.status === "ready" && (
                <Link to={`/chats/${p.conversationId}#task-card`}>
                  <Button type="primary">
                    确认参数 <ArrowRightOutlined />
                  </Button>
                </Link>
              )}
            </>
          )
        }
      />
      <LoadError error={project.error} retry={project.reload} />
      {!p && !project.error && <Skeleton active />}
      {p && (
        <>
          <Stats
            items={[
              ["状态", <Status value={p.status} />, <ExperimentOutlined />],
              ["运行", p.runCount, <PlayCircleOutlined />],
              ["报告", p.reportCount, <FileTextOutlined />],
              [
                "活跃时间",
                <>
                  {((p.allowance?.elapsedSeconds || 0) / 60).toFixed(1)}
                  <small>分钟</small>
                </>,
                <ClockCircleOutlined />,
              ],
            ]}
          />
          <div className="detail-grid project-grid">
            <div className="detail-stack">
              <WPanel
                title="运行与报告"
                className="project-runs-panel"
                extra={
                  <div className="manager-tools">
                    <Input
                      className="manager-search-input"
                      aria-label="搜索项目运行或报告"
                      placeholder="搜索运行或报告"
                      prefix={<SearchOutlined />}
                      value={state.q}
                      allowClear
                      onChange={(e) => set({ q: e.target.value })}
                    />
                    <Select
                      aria-label="项目运行筛选"
                      value={state.hasReports ? "with_reports" : state.status}
                      onChange={(value) =>
                        set({
                          hasReports: value === "with_reports",
                          status: value === "with_reports" ? "all" : value,
                        })
                      }
                      options={[
                        { value: "all", label: "全部运行" },
                        { value: "with_reports", label: "有报告" },
                        ...Object.keys(runs.data?.stats || {})
                          .filter((s) => s !== "total")
                          .map((s) => ({ value: s, label: statuses[s] || s })),
                      ]}
                    />
                    <TimeSort
                      label="创建时间"
                      order={state.order}
                      onChange={() =>
                        set({ order: state.order === "desc" ? "asc" : "desc" })
                      }
                    />
                  </div>
                }
              >
                <LoadError error={runs.error} retry={runs.reload} />
                {!runs.data && !runs.error && (
                  <div className="detail-panel-body">
                    <Skeleton active />
                  </div>
                )}
                {runs.data && (
                  <>
                    {runs.data.items.map((run, index) => (
                      <RunGroup key={run.id} run={run} index={index} />
                    ))}
                    {!runs.data.items.length && (
                      <div className="project-empty">暂无匹配的运行</div>
                    )}
                    <Pager
                      data={runs.data}
                      label="次运行"
                      onChange={(page, pageSize) =>
                        set({ page, pageSize }, false)
                      }
                    />
                  </>
                )}
              </WPanel>
            </div>
            <div className="detail-stack">
              <BudgetPanel project={p} reload={changed} />
              <WPanel title="项目信息" body>
                <div className="scope-line">
                  <span>创建时间</span>
                  <strong>{time(p.createdAt)}</strong>
                </div>
                <div className="scope-line">
                  <span>更新时间</span>
                  <strong>{time(p.updatedAt)}</strong>
                </div>
              </WPanel>
            </div>
          </div>
        </>
      )}
    </>
  );
}

function RunPage({ id, owner }: { id: string; owner?: string }) {
  const {
    data: run,
    error,
    reload,
  } = useRead<RunOverview>(`/runs/${id}/overview`);
  const project = useRead<ProjectSummary>(
    run ? `/projects/${run.projectId}` : undefined,
  );
  const reports = useRead<Page<ReportSummary>>(
    run ? `/reports?runId=${id}` : undefined,
  );
  const intent = useIntent(),
    [failure, setFailure] = useState(""),
    [busy, setBusy] = useState(false);
  useHeading(run?.title || "运行", "projects", run?.projectTitle);
  const navigate = useNavigate();
  async function control(action: string) {
    setBusy(true);
    setFailure("");
    try {
      await api(`/runs/${id}/actions`, {
        action,
        clientRequestId: intent.key({ action, id }),
      });
      intent.done();
      changed();
    } catch (e) {
      setFailure(String(e));
    } finally {
      setBusy(false);
    }
  }
  if (run && owner && run.projectId !== owner)
    return (
      <LoadError
        error="运行不属于当前项目"
        retry={() => navigate(projectLink(owner))}
      />
    );
  return (
    <>
      <Back to={run ? projectLink(run.projectId) : "/runs"}>
        {run?.projectTitle || "运行中心"}
      </Back>
      <PageHeader
        title={run?.title || "运行"}
        action={
          run && (
            <Link to={`/chats/${run.conversationId}`}>
              <Button type="primary" icon={<MessageOutlined />}>
                进入会话
              </Button>
            </Link>
          )
        }
      />
      <LoadError error={error || failure} retry={reload} />
      {!run && !error && <Skeleton active />}
      {run && (
        <>
          <Stats
            items={[
              [
                "状态",
                <Status value={run.displayStatus} />,
                <PlayCircleOutlined />,
              ],
              [
                "命令作业",
                <>
                  {run.completedCommands ?? run.completedJobs}
                  <small>/ {run.commandCount ?? run.jobCount}</small>
                </>,
                <AppstoreOutlined />,
              ],
              [
                "活跃时间",
                <>
                  {(run.usage.elapsedSeconds / 60).toFixed(1)}
                  <small>分钟</small>
                </>,
                <ClockCircleOutlined />,
              ],
              ["估算费用", money(run.usage.spentCost), <WalletOutlined />],
            ]}
          />
          <div className="detail-grid">
            <div className="detail-stack">
              <WPanel title="命令作业">
                <RunWorkspace run={run} onChange={changed} />
              </WPanel>
              <WPanel title="公开进展" body>
                <div className="public-progress">
                  <div className="public-progress-row">
                    <span className="dot" />
                    <div>
                      <time>{time(run.approvedAt)}</time>
                      <strong>参数已确认</strong>
                    </div>
                  </div>
                  {run.currentJob && (
                    <div className="public-progress-row">
                      <span className="dot" />
                      <div>
                        <time>
                          {time(
                            run.currentJob.finishedAt ||
                              run.currentJob.createdAt,
                          )}
                        </time>
                        <strong>
                          命令
                          {statuses[run.currentJob.status] ||
                            run.currentJob.status}
                        </strong>
                        <code
                          className="command-preview"
                          title={run.currentJob.command}
                        >
                          {run.currentJob.command}
                        </code>
                      </div>
                    </div>
                  )}
                  <div className="public-progress-row">
                    <span className="dot" />
                    <div>
                      {run.finishedAt && <time>{time(run.finishedAt)}</time>}
                      <strong>
                        {statuses[run.displayStatus] || run.displayStatus}
                      </strong>
                      {run.automation?.reason && <p>{run.automation.reason}</p>}
                    </div>
                  </div>
                </div>
              </WPanel>
              <LoadError error={reports.error} retry={reports.reload} />
              {reports.data?.items.map((report) => (
                <div className="linked-reports" key={report.id}>
                  <LinkedReport report={report} />
                </div>
              ))}
              {run.reportCount === 0 &&
                ["PAUSED", "FAILED", "COMPLETED"].includes(run.status) && (
                  <ReportRecovery runId={run.id} reload={changed} />
                )}
              {!!run.automation?.allowedActions.length && (
                <details className="manager-panel extra-actions">
                  <summary>运行操作</summary>
                  <div className="detail-panel-body">
                    {run.automation.allowedActions.map((action) => (
                      <Button
                        key={action}
                        loading={busy}
                        danger={action === "cancel"}
                        onClick={() => void control(action)}
                      >
                        {(
                          {
                            pause: "暂停",
                            resume: "继续",
                            cancel: "取消运行",
                          } as Record<string, string>
                        )[action] || action}
                      </Button>
                    ))}
                  </div>
                </details>
              )}
            </div>
            <div className="detail-stack">
              <WPanel title="批准参数" body>
                <ApprovedSnapshot plan={run.approvedPlan} expanded />
              </WPanel>
              <LoadError error={project.error} retry={project.reload} />
              {project.data && (
                <BudgetPanel project={project.data} reload={changed} />
              )}
            </div>
          </div>
        </>
      )}
    </>
  );
}
function ReportPage({ id, owner }: { id: string; owner?: string }) {
  const report = useRead<Report>(`/reports/${id}`),
    r = report.data;
  const run = useRead<RunOverview>(r ? `/runs/${r.runId}/overview` : undefined),
    parent = run.data;
  const [versionQuery, setVersionQuery] = useState({ q: "", page: 1 });
  const versionSearch = useDebounced(versionQuery.q);
  const versionKey = `${r?.runId}:${versionSearch}`;
  const versions = useRead<Page<ReportSummary>>(
    r
      ? `/reports?${query({ runId: r.runId, includeHistory: true, q: versionSearch, page: versionQuery.page, pageSize: 50 })}`
      : undefined,
  );
  const [loadedVersions, setLoadedVersions] = useState<{
    key: string;
    items: ReportSummary[];
  }>({ key: "", items: [] });
  useEffect(() => {
    if (!versions.data) return;
    const items = versions.data.items;
    setLoadedVersions((old) => ({
      key: versionKey,
      items: [
        ...new Map(
          [
            ...(old.key === versionKey && versionQuery.page > 1
              ? old.items
              : []),
            ...items,
          ].map((v) => [v.id, v]),
        ).values(),
      ],
    }));
  }, [versions.data, versionKey, versionQuery.page]);
  const navigate = useNavigate();
  useHeading(
    r ? String(r.result.title || "复现报告") : "报告",
    "projects",
    parent?.projectTitle,
  );
  if (r && owner && r.projectId !== owner)
    return (
      <LoadError
        error="报告不属于当前项目"
        retry={() => navigate(projectLink(owner))}
      />
    );
  const choices = [
    ...new Map(
      [
        ...(loadedVersions.key === versionKey
          ? loadedVersions.items
          : versions.data?.items || []),
        ...(r
          ? [{ id: r.id, version: r.version, createdAt: r.createdAt }]
          : []),
      ].map((v) => [v.id, v]),
    ).values(),
  ].sort((a, b) => b.version - a.version);
  return (
    <>
      <Back to={parent ? projectLink(parent.projectId) : "/reports"}>
        {parent?.projectTitle || "报告中心"}
      </Back>
      <PageHeader
        title="复现报告"
        action={
          r && (
            <>
              {(parent?.reportCount || versions.data?.total || choices.length) >
                1 && (
                <Select
                  className="report-version-select"
                  aria-label="报告版本"
                  value={r.id}
                  showSearch={{
                    onSearch: (q) => setVersionQuery({ q, page: 1 }),
                    filterOption: false,
                  }}
                  loading={!versions.data}
                  onPopupScroll={(event) => {
                    const area = event.currentTarget;
                    if (
                      versions.data &&
                      area.scrollTop + area.clientHeight >=
                        area.scrollHeight - 24 &&
                      versions.data.page * versions.data.pageSize <
                        versions.data.total
                    )
                      setVersionQuery((old) => ({
                        ...old,
                        page: old.page + 1,
                      }));
                  }}
                  onChange={(value) =>
                    navigate(
                      r.projectId
                        ? `${projectLink(r.projectId)}/reports/${value}`
                        : `/reports/${value}`,
                    )
                  }
                  options={choices.map((v) => ({
                    value: v.id,
                    label: `v${v.version} · ${time(v.createdAt)}`,
                  }))}
                />
              )}
              {parent && (
                <>
                  <Link to={runLink(parent)}>
                    <Button icon={<PlayCircleOutlined />}>对应运行</Button>
                  </Link>
                  <Link to={`/chats/${parent.conversationId}`}>
                    <Button icon={<MessageOutlined />}>项目会话</Button>
                  </Link>
                </>
              )}
            </>
          )
        }
      />
      <LoadError
        error={report.error || run.error || versions.error}
        retry={() => {
          report.reload();
          run.reload();
          versions.reload();
        }}
      />
      {!r && !report.error && <Skeleton active />}
      {r && <ReportPanel report={r} reload={changed} />}
    </>
  );
}
export function Pages({ path }: { path: string }) {
  const parts = path.split("/").filter(Boolean);
  if (parts[0] === "projects" && parts[1] && parts[2] === "runs" && parts[3])
    return <RunPage id={parts[3]} owner={parts[1]} />;
  if (parts[0] === "projects" && parts[1] && parts[2] === "reports" && parts[3])
    return <ReportPage id={parts[3]} owner={parts[1]} />;
  if (parts[0] === "projects" && parts[1] && parts.length === 2)
    return <ProjectPage id={parts[1]} />;
  if (parts[0] === "runs" && parts[1]) return <RunPage id={parts[1]} />;
  if (parts[0] === "reports" && parts[1]) return <ReportPage id={parts[1]} />;
  if (path === "/projects" || path === "/runs" || path === "/reports")
    return <Center kind={parts[0] as keyof typeof names} />;
  return <div className="project-empty">页面不存在</div>;
}
