import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Button, Drawer, InputNumber } from "antd";
import {
  ArrowRightOutlined,
  DownloadOutlined,
  EyeOutlined,
  FileOutlined,
  FileTextOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import { api, root, useIntent } from "./client";
import type { Artifact, Report } from "./client";
import { Status, ErrorBox, Result, Details, bytes } from "./ui";
import { imageTypes } from "./report-content";
import { reportLink, time } from "./workspace-client";
import { WPanel } from "./workspace-ui";
import { MathText, mathHeadingText } from "./math";

export function ArtifactList({ artifacts }: { artifacts: Artifact[] }) {
  const [preview, setPreview] = useState<Artifact>();
  return (
    <>
      <div className="artifact-list">
        {artifacts.map((a) => (
          <div key={a.id} className="artifact-item">
            <FileOutlined />
            <div className="artifact-info">
              <a href={`${root}/artifacts/${a.id}`} download title={a.name}>
                {a.name}
              </a>
              <small>{bytes(a.size)}</small>
            </div>
            {imageTypes.has(a.mediaType) && (
              <Button
                type="text"
                size="small"
                icon={<EyeOutlined />}
                aria-label={`预览 ${a.name}`}
                onClick={() => setPreview(a)}
              />
            )}
          </div>
        ))}
        {!artifacts.length && <div className="project-empty">暂无产物</div>}
      </div>
      <Drawer
        title={preview?.name}
        open={!!preview}
        onClose={() => setPreview(undefined)}
        size={760}
        extra={
          preview && (
            <Button
              icon={<DownloadOutlined />}
              href={`${root}/artifacts/${preview.id}`}
              download
            >
              下载
            </Button>
          )
        }
      >
        {preview && (
          <img
            className="artifact-preview"
            src={`${root}/artifacts/${preview.id}?preview=true`}
            alt={preview.name}
          />
        )}
      </Drawer>
    </>
  );
}
export function ReportPanel({
  report,
  reload,
  compact = false,
  embedded = false,
}: {
  report: Report;
  reload: () => void;
  compact?: boolean;
  embedded?: boolean;
}) {
  const intent = useIntent();
  const paper = useRef<HTMLElement>(null);
  const [outline, setOutline] = useState<{ id: string; title: string }[]>([]);
  const [error, setError] = useState(""),
    [grant, setGrant] = useState(""),
    [busy, setBusy] = useState(false);
  async function retry(regenerate: boolean) {
    setError("");
    if (busy) return;
    setBusy(true);
    try {
      const body = {
        regenerate,
        ...(grant ? { maxHours: Number(grant) } : {}),
      };
      await api(`/reports/${report.id}/retry`, {
        ...body,
        clientRequestId: intent.key(body),
      });
      intent.done();
      reload();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    if (compact || embedded) return;
    const headings = [
      ...(paper.current?.querySelectorAll<HTMLElement>(
        ".result-block h1, .result-block h2, .result-block h3, .result-block h4, .result-block h5, .result-block h6",
      ) || []),
    ];
    const entries = headings
      .map((heading, index) => {
        const section = heading.closest(".result-block");
        const id =
          heading.parentElement === section
            ? section!.id
            : `${section!.id}-heading-${index + 1}`;
        if (heading.parentElement !== section) heading.id = id;
        return { id, title: mathHeadingText(heading) };
      })
      .filter((heading) => heading.title);
    setOutline((old) =>
      JSON.stringify(old) === JSON.stringify(entries) ? old : entries,
    );
  }, [report.result, compact, embedded]);
  const title = String(report.result.title || "复现报告");
  if (compact)
    return (
      <Link
        className="inline-card"
        to={
          report.projectId
            ? reportLink({ id: report.id, projectId: report.projectId })
            : `/reports/${report.id}`
        }
      >
        <FileTextOutlined />
        <span className="card-text">
          <strong>{title}</strong>
        </span>
        <ArrowRightOutlined />
      </Link>
    );
  return (
    <div className={embedded ? "embedded-report" : "report-viewer"}>
      {embedded && (
        <>
          <div className="report-toolbar">
            <Status value={report.exportStatus} />
            <Button
              size="small"
              icon={<DownloadOutlined />}
              href={`${root}/reports/${report.id}/download?format=markdown`}
              download
            >
              Markdown
            </Button>
            <Button
              size="small"
              icon={<DownloadOutlined />}
              href={`${root}/reports/${report.id}/download?format=json`}
              download
            >
              JSON
            </Button>
            {report.exportStatus === "READY" && (
              <Button
                size="small"
                icon={<DownloadOutlined />}
                href={`${root}/reports/${report.id}/download?format=zip`}
                download
              >
                产物包
              </Button>
            )}
            {report.exportStatus === "FAILED" && (
              <Button
                size="small"
                loading={busy}
                onClick={() => void retry(false)}
              >
                重试导出
              </Button>
            )}
            <Link
              className="icon-link"
              aria-label="打开完整报告"
              to={
                report.projectId
                  ? reportLink({ id: report.id, projectId: report.projectId })
                  : `/reports/${report.id}`
              }
            >
              <EyeOutlined />
            </Link>
          </div>
          <ErrorBox error={error || report.error || ""} />
        </>
      )}
      <div className="report-reader-grid">
        <article className="report-paper" ref={paper}>
          <div className="report-meta">
            <span>{report.runId}</span>
            <span>·</span>
            <span>v{report.version}</span>
            <span>·</span>
            <span>{time(report.createdAt)}</span>
          </div>
          <Result report={report} />
        </article>
        {!embedded && (
          <div className="detail-stack">
            {outline.length > 0 && (
              <WPanel title="报告目录" body>
                <div className="report-outline">
                  {outline.map((block) => (
                    <button
                      type="button"
                      key={block.id}
                      onClick={() =>
                        document.getElementById(block.id)?.scrollIntoView({
                          behavior: window.matchMedia(
                            "(prefers-reduced-motion: reduce)",
                          ).matches
                            ? "instant"
                            : "smooth",
                          block: "start",
                        })
                      }
                    >
                      <MathText inlineOnly>{block.title}</MathText>
                    </button>
                  ))}
                </div>
              </WPanel>
            )}
            <WPanel title="下载" body>
              <div className="download-list">
                <Button
                  icon={<DownloadOutlined />}
                  href={`${root}/reports/${report.id}/download?format=markdown`}
                  download
                >
                  Markdown 正文
                </Button>
                <Button
                  icon={<DownloadOutlined />}
                  href={`${root}/reports/${report.id}/download?format=json`}
                  download
                >
                  完整 JSON
                </Button>
                <div className="divider" />
                <div className="allocation-line">
                  <span>产物包</span>
                  <Status value={report.exportStatus} />
                </div>
                {report.exportStatus === "READY" && (
                  <Button
                    type="primary"
                    icon={<DownloadOutlined />}
                    href={`${root}/reports/${report.id}/download?format=zip`}
                    download
                  >
                    下载产物包
                  </Button>
                )}
                {report.exportStatus === "FAILED" && (
                  <Button
                    loading={busy}
                    icon={<ReloadOutlined />}
                    onClick={() => void retry(false)}
                  >
                    重试导出
                  </Button>
                )}
              </div>
              <Details title="报告操作">
                <div className="recovery-form">
                  <label>
                    账户累计活跃时间上限（小时）
                    <InputNumber<string>
                      placeholder="沿用现有额度"
                      stringMode
                      min="0"
                      value={grant}
                      onChange={(value) => setGrant(value || "")}
                    />
                  </label>
                  <div className="actions">
                    <Button loading={busy} onClick={() => void retry(true)}>
                      重新生成报告
                    </Button>
                    <Button disabled={busy} onClick={() => void retry(false)}>
                      重试导出
                    </Button>
                  </div>
                </div>
              </Details>
              <ErrorBox error={error || report.error || ""} />
            </WPanel>
            <WPanel
              title={
                <>
                  关联产物{" "}
                  <span className="chip">{report.artifacts.length}</span>
                </>
              }
            >
              <ArtifactList artifacts={report.artifacts} />
            </WPanel>
          </div>
        )}
      </div>
    </div>
  );
}
export function ReportRecovery({
  runId,
  reload,
}: {
  runId: string;
  reload: () => void;
}) {
  const [hours, setHours] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const intent = useIntent();
  async function submit() {
    if (busy) return;
    setError("");
    setBusy(true);
    try {
      const body = { ...(hours ? { maxHours: Number(hours) } : {}) };
      await api(`/runs/${runId}/report`, {
        ...body,
        clientRequestId: intent.key(body),
      });
      intent.done();
      reload();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <WPanel body>
      <Details title="生成只读报告">
        <div className="recovery-form">
          <label>
            账户累计活跃时间上限（小时）
            <InputNumber<string>
              stringMode
              min="0"
              placeholder="沿用现有额度"
              value={hours}
              onChange={(value) => setHours(value || "")}
            />
          </label>
          <Button type="primary" loading={busy} onClick={() => void submit()}>
            生成报告
          </Button>
          <ErrorBox error={error} />
        </div>
      </Details>
    </WPanel>
  );
}
