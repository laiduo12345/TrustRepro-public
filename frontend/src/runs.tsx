import { Fragment, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Button, Input, Segmented, Skeleton } from "antd";
import {
  ArrowRightOutlined,
  CheckCircleOutlined,
  CodeOutlined,
  CopyOutlined,
  FileOutlined,
  PlayCircleOutlined,
  StopOutlined,
} from "@ant-design/icons";
import { api } from "./client";
import type { Job, Run } from "./client";
import { ErrorBox, Raw, Details, Status } from "./ui";
import { duration, query, runLink, useRead } from "./workspace-client";
import type {
  JobSummary,
  OutputWindow,
  Page,
  RunOverview,
  RunSummary,
} from "./workspace-client";
import { LoadError, Pager } from "./workspace-ui";

const kinds: Record<string, string> = {
  command: "命令",
  write_file: "写入文件",
  apply_patch: "编辑文件",
  publish: "保存产物",
  import: "导入产物",
  export: "生成产物包",
};
function highlight(text: string, search: string) {
  if (!search) return text;
  const lower = text.toLocaleLowerCase(),
    needle = search.toLocaleLowerCase();
  const nodes = [];
  let start = 0,
    index = 0;
  while ((index = lower.indexOf(needle, start)) !== -1) {
    nodes.push(
      <Fragment key={start}>
        {text.slice(start, index)}
        <mark>{text.slice(index, index + search.length)}</mark>
      </Fragment>,
    );
    start = index + search.length;
  }
  nodes.push(text.slice(start));
  return nodes;
}
function SelectedJob({ id, onChange }: { id: string; onChange?: () => void }) {
  const { data: job, error, reload } = useRead<Job>(`/jobs/${id}`);
  const output = useRead<OutputWindow>(
    job?.kind === "command" ? `/jobs/${id}/output` : undefined,
    job?.status === "RUNNING" ? 1000 : 4000,
  );
  const [search, setSearch] = useState(""),
    [failure, setFailure] = useState("");
  const command = job ? String(job.arguments.command || "") : "";
  const text = output.data
    ? `$ ${command}\n${output.data.stdout}${output.data.stderr ? "\n" + output.data.stderr : ""}`
    : `$ ${command}`;
  const count = search.trim()
    ? text.toLocaleLowerCase().split(search.trim().toLocaleLowerCase()).length -
      1
    : "";
  return (
    <div className="selected-job">
      <LoadError
        error={error || output.error}
        retry={() => {
          reload();
          output.reload();
        }}
      />
      {!job && !error && <Skeleton active />}
      {job && (
        <>
          {job.kind === "command" ? (
            <div className="terminal">
              <div className="terminal-toolbar">
                <CodeOutlined />
                <span>命令输出</span>
                <div className="log-tools">
                  <Input
                    className="log-search"
                    aria-label="查找命令输出"
                    placeholder="查找输出"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                  />
                  <span className="log-match-count" role="status">
                    {count}
                  </span>
                  <Button
                    type="text"
                    size="small"
                    aria-label="复制当前作业输出"
                    icon={<CopyOutlined />}
                    onClick={async () => {
                      try {
                        await navigator.clipboard.writeText(text);
                        setFailure("");
                      } catch {
                        setFailure("复制失败，请选择输出文本复制");
                      }
                    }}
                  />
                </div>
              </div>
              <pre className="terminal-output" tabIndex={0}>
                {highlight(text, search.trim())}
              </pre>
              <div className="terminal-footer">
                <span>
                  {output.data?.exitCode != null ? (
                    `退出码 ${output.data.exitCode}`
                  ) : (
                    <Status value={job.status} />
                  )}
                </span>
                <span>
                  {output.data?.truncated
                    ? "最近 64 KB"
                    : job.startedAt && job.finishedAt
                      ? duration((job.finishedAt - job.startedAt) / 1000)
                      : ""}
                </span>
              </div>
            </div>
          ) : (
            <div className="job-result">
              <strong>{kinds[job.kind] || job.kind}</strong>
              <Raw value={job.result ?? null} />
            </div>
          )}
          <ErrorBox error={failure || job.error || ""} />
          <Details title="完整作业数据">
            <Raw value={{ arguments: job.arguments, result: job.result }} />
          </Details>
          {["RUNNING", "QUEUED", "RETRYING"].includes(job.status) && (
            <Button
              size="small"
              danger
              icon={<StopOutlined />}
              onClick={async () => {
                try {
                  await api(`/jobs/${id}/cancel`, {});
                  onChange?.();
                  reload();
                } catch (e) {
                  setFailure(String(e));
                }
              }}
            >
              停止作业
            </Button>
          )}
        </>
      )}
    </div>
  );
}
export function RunWorkspace({
  run,
  onChange,
}: {
  run: RunSummary;
  onChange?: () => void;
}) {
  const [kind, setKind] = useState("all"),
    [page, setPage] = useState(1),
    [size, setSize] = useState(10);
  const [selection, select] = useState<string>();
  const data = useRead<Page<JobSummary>>(
    `/runs/${run.id}/jobs?${query({ kind, page, pageSize: size })}`,
  );
  useEffect(() => {
    if (data.data && data.data.page !== page) {
      setPage(data.data.page);
      select(undefined);
    }
  }, [data.data?.page, page]);
  const id =
    selection ||
    (page === 1 &&
    kind !== "files" &&
    data.data?.items.some((job) => job.id === run.currentJob?.id)
      ? run.currentJob?.id
      : undefined) ||
    data.data?.items[0]?.id;
  return (
    <div className="detail-panel-body run-workspace">
      <div className="jobs-toolbar">
        <Segmented
          aria-label="作业类型"
          size="small"
          value={kind}
          onChange={(value) => {
            setKind(value);
            setPage(1);
            select(undefined);
          }}
          options={[
            { value: "all", label: "全部" },
            { value: "command", label: "命令" },
            { value: "files", label: "文件与产物" },
          ]}
        />
      </div>
      <LoadError error={data.error} retry={data.reload} />
      <div className="detail-jobs">
        <div className="jobs-list">
          {data.data?.items.map((job) => (
            <button
              type="button"
              className={`job-item ${id === job.id ? "selected" : ""}`}
              key={job.id}
              onClick={() => select(job.id)}
              aria-pressed={id === job.id}
            >
              {job.status === "COMPLETED" ? (
                <CheckCircleOutlined />
              ) : job.kind === "command" ? (
                <CodeOutlined />
              ) : (
                <FileOutlined />
              )}
              <span
                className="job-name"
                title={job.command || kinds[job.kind] || job.kind}
              >
                {job.command || kinds[job.kind] || job.kind}
              </span>
              <span className="job-state">
                <Status value={job.status} />
              </span>
            </button>
          ))}
          {data.data && !data.data.items.length && (
            <div className="project-empty">暂无作业</div>
          )}
        </div>
        {id ? (
          <SelectedJob key={id} id={id} onChange={onChange} />
        ) : (
          <div className="project-empty">暂无输出</div>
        )}
      </div>
      {data.data && (
        <Pager
          data={data.data}
          label="个作业"
          onChange={(next, capacity) => {
            setPage(next);
            setSize(capacity);
            select(undefined);
          }}
        />
      )}
    </div>
  );
}
function ExpandedRun({ id, onChange }: { id: string; onChange?: () => void }) {
  const data = useRead<RunOverview>(`/runs/${id}/overview`);
  return (
    <>
      <LoadError error={data.error} retry={data.reload} />
      {data.data && <RunWorkspace run={data.data} onChange={onChange} />}
    </>
  );
}
export function RunPanel({
  run,
  onChange,
  compact = false,
}: {
  run: Run;
  onChange?: () => void;
  compact?: boolean;
}) {
  if (!compact) return <ExpandedRun id={run.id} onChange={onChange} />;
  return (
    <Link className="inline-card" to={runLink(run)}>
      <PlayCircleOutlined />
      <span className="card-text">
        <strong>本次运行</strong>
      </span>
      <Status value={run.automation?.state || run.status} />
      <ArrowRightOutlined />
    </Link>
  );
}
