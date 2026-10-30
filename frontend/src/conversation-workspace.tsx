import { useState } from "react";
import { Button, Select, Skeleton } from "antd";
import {
  AppstoreOutlined,
  BookOutlined,
  CloseOutlined,
  ExpandOutlined,
  FileOutlined,
  FileTextOutlined,
  FolderOutlined,
  ShrinkOutlined,
} from "@ant-design/icons";
import { Link } from "react-router-dom";
import type { Artifact, Chat } from "./client";
import { ArtifactList, ReportPanel } from "./reports";
import { RunWorkspace } from "./runs";
import { changed, projectLink, time, useRead } from "./workspace-client";
import type { RunOverview, Source } from "./workspace-client";
import { LoadError } from "./workspace-ui";

export type WorkspaceTab = "sources" | "execution" | "artifacts" | "report";
export function ConversationWorkspace({
  chat,
  open,
  focus,
  tab,
  onTab,
  onClose,
  onFocus,
}: {
  chat?: Chat;
  open: boolean;
  focus: boolean;
  tab: WorkspaceTab;
  onTab: (tab: WorkspaceTab) => void;
  onClose: () => void;
  onFocus: () => void;
}) {
  const [runId, setRunId] = useState(""),
    [reportId, setReportId] = useState("");
  const runs = [...(chat?.runs || [])].sort(
    (a, b) => b.createdAt - a.createdAt || a.id.localeCompare(b.id),
  );
  const current = runs.find((run) => run.id === runId) || runs[0];
  const projectId = chat?.projectId || current?.projectId;
  const sources = useRead<Source[]>(
    open && tab === "sources" && chat
      ? `/conversations/${chat.id}/sources`
      : undefined,
  );
  const overview = useRead<RunOverview>(
    open && current && tab === "execution"
      ? `/runs/${current.id}/overview`
      : undefined,
  );
  const artifacts = useRead<Artifact[]>(
    open && tab === "artifacts" && projectId
      ? `/projects/${projectId}/artifacts${current ? `?runId=${current.id}` : ""}`
      : undefined,
  );
  const reports = [...(chat?.reports || [])]
    .filter((report) => report.runId === current?.id)
    .sort((a, b) => b.version - a.version);
  const report = reports.find((r) => r.id === reportId) || reports[0];
  const tabs: [WorkspaceTab, string, React.ReactNode][] = [
    ["sources", "资料", <BookOutlined />],
    ["execution", "执行", <AppstoreOutlined />],
    ["artifacts", "产物", <FolderOutlined />],
    ["report", "报告", <FileTextOutlined />],
  ];
  return (
    <aside
      className="workspace"
      aria-label="当前会话工作区"
      aria-hidden={!open}
      inert={!open}
    >
      <div className="workspace-header">
        <AppstoreOutlined />
        <strong>工作区</strong>
        <span className="spacer" />
        {projectId && (
          <Link
            className="icon-link"
            to={projectLink(projectId)}
            aria-label="打开项目"
          >
            <FolderOutlined />
          </Link>
        )}
        <Button
          type="text"
          size="small"
          icon={focus ? <ShrinkOutlined /> : <ExpandOutlined />}
          aria-label={focus ? "还原工作区" : "展开工作区"}
          onClick={onFocus}
        />
        <Button
          type="text"
          size="small"
          icon={<CloseOutlined />}
          aria-label="关闭工作区"
          onClick={onClose}
        />
      </div>
      <div className="workspace-tabs" role="tablist" aria-label="工作区内容">
        {tabs.map(([key, name, icon]) => (
          <button
            type="button"
            role="tab"
            className="workspace-tab"
            key={key}
            aria-selected={tab === key}
            tabIndex={tab === key ? 0 : -1}
            onClick={() => onTab(key)}
            onKeyDown={(e) => {
              if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) {
                e.preventDefault();
                const i = tabs.findIndex((t) => t[0] === key);
                const next =
                  e.key === "Home"
                    ? 0
                    : e.key === "End"
                      ? tabs.length - 1
                      : (i + (e.key === "ArrowRight" ? 1 : -1) + tabs.length) %
                        tabs.length;
                onTab(tabs[next][0]);
                (
                  e.currentTarget.parentElement?.children[next] as HTMLElement
                )?.focus();
              }
            }}
          >
            {icon}
            {name}
          </button>
        ))}
      </div>
      <div
        className="workspace-body"
        tabIndex={0}
        role="region"
        aria-label={`${{ sources: "资料", execution: "执行", artifacts: "产物", report: "报告" }[tab]}详情`}
      >
        <div className="workspace-inner">
          {open && (
            <>
              {tab !== "sources" && runs.length > 1 && (
                <Select
                  className="workspace-run-select"
                  aria-label="工作区运行"
                  value={current?.id}
                  onChange={(value) => {
                    setRunId(value);
                    setReportId("");
                  }}
                  options={runs.map((run) => ({
                    value: run.id,
                    label: `${time(run.createdAt)} · ${run.id}`,
                  }))}
                />
              )}
              {tab === "sources" && (
                <>
                  <LoadError error={sources.error} retry={sources.reload} />
                  {!sources.data && chat && !sources.error && (
                    <Skeleton active />
                  )}
                  {sources.data?.map((source) => (
                    <div className="resource-card" key={source.id}>
                      <div className="resource-title">
                        <BookOutlined />
                        <div>
                          <h3>
                            {source.url && /^https?:\/\//i.test(source.url) ? (
                              <a
                                href={source.url}
                                target="_blank"
                                rel="noreferrer"
                              >
                                {source.title || source.url}
                              </a>
                            ) : (
                              source.title || source.id
                            )}
                          </h3>
                        </div>
                      </div>
                    </div>
                  ))}
                  {(!chat || sources.data?.length === 0) && (
                    <div className="empty-workspace">
                      <BookOutlined />
                      <span>暂无资料</span>
                    </div>
                  )}
                </>
              )}
              {tab === "execution" && (
                <>
                  <LoadError error={overview.error} retry={overview.reload} />
                  {overview.data && (
                    <RunWorkspace
                      key={overview.data.id}
                      run={overview.data}
                      onChange={changed}
                    />
                  )}
                  {!current && (
                    <div className="empty-workspace">
                      <AppstoreOutlined />
                      <span>暂无运行</span>
                    </div>
                  )}
                  {current && !overview.data && !overview.error && (
                    <Skeleton active />
                  )}
                </>
              )}
              {tab === "artifacts" && (
                <>
                  <LoadError error={artifacts.error} retry={artifacts.reload} />
                  {artifacts.data ? (
                    <ArtifactList artifacts={artifacts.data} />
                  ) : !projectId ? (
                    <div className="empty-workspace">
                      <FileOutlined />
                      <span>暂无产物</span>
                    </div>
                  ) : (
                    !artifacts.error && <Skeleton active />
                  )}
                </>
              )}
              {tab === "report" && (
                <>
                  {reports.length > 1 && (
                    <Select
                      className="workspace-run-select"
                      aria-label="工作区报告版本"
                      value={report?.id}
                      onChange={setReportId}
                      options={reports.map((r) => ({
                        value: r.id,
                        label: `v${r.version} · ${time(r.createdAt)}`,
                      }))}
                    />
                  )}
                  {report ? (
                    <ReportPanel
                      key={report.id}
                      report={report}
                      reload={changed}
                      embedded
                    />
                  ) : (
                    <div className="empty-workspace">
                      <FileTextOutlined />
                      <span>暂无报告</span>
                    </div>
                  )}
                </>
              )}
            </>
          )}
        </div>
      </div>
    </aside>
  );
}
