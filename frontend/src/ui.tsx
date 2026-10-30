import { root, pretty, statuses } from "./client";
import type { Artifact, Json, Report } from "./client";
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import { MathText, rehypeSafeMath } from "./math";
import { remarkSafeMath } from "./math-syntax";
import { remarkChineseLinks } from "./markdown-links";
import { useState } from "react";
import type { ReactNode } from "react";
import { Alert, Empty } from "antd";
import { FileTextOutlined } from "@ant-design/icons";
import { imageTypes, reportBlocks } from "./report-content";

export function PageHeader({
  title,
  action,
}: {
  title: ReactNode;
  action?: ReactNode;
}) {
  return (
    <header className="page-header">
      <h1>{title}</h1>
      {action && <div className="page-actions">{action}</div>}
    </header>
  );
}
export function Panel({
  title,
  extra,
  children,
}: {
  title?: ReactNode;
  extra?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="panel">
      {title && (
        <div className="panel-heading">
          <h2>{title}</h2>
          {extra}
        </div>
      )}
      {children}
    </section>
  );
}
export function EmptyState({ title = "暂无记录" }: { title?: string }) {
  return (
    <div className="empty-state">
      <Empty image={<FileTextOutlined />} description={title} />
    </div>
  );
}
export function Details({
  title,
  children,
  className = "",
}: {
  title: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <details
      className={className}
      onToggle={(e) => setOpen(e.currentTarget.open)}
    >
      <summary>{title}</summary>
      {open && <div className="details-body">{children}</div>}
    </details>
  );
}
export function bytes(value: number) {
  return value >= 1048576
    ? `${(value / 1048576).toFixed(1)} MB`
    : value >= 1024
      ? `${(value / 1024).toFixed(1)} KB`
      : `${value} B`;
}

export function Markdown({
  children,
  artifacts = [],
}: {
  children: string;
  artifacts?: Artifact[];
}) {
  const registered = new Set(artifacts.map((artifact) => artifact.id));
  const images = new Map(
    artifacts
      .filter((a) => imageTypes.has(a.mediaType))
      .map((a) => [`${root}/artifacts/${a.id}`, a]),
  );
  return (
    <div className="prose markdown">
      <ReactMarkdown
        remarkPlugins={[
          remarkGfm,
          remarkMath,
          [remarkSafeMath, { source: children }],
          [remarkChineseLinks, { source: children }],
        ]}
        rehypePlugins={[rehypeSafeMath]}
        urlTransform={(url) => {
          const id = url.startsWith("artifact:") ? url.slice(9) : url;
          if (id.startsWith("artifact-"))
            return registered.has(id) ? `${root}/artifacts/${id}` : "";
          return defaultUrlTransform(url);
        }}
        components={{
          img: ({ src, alt }) => {
            const artifact =
              typeof src === "string" ? images.get(src) : undefined;
            return artifact ? (
              <img
                src={`${root}/artifacts/${artifact.id}?preview=true`}
                alt={alt || artifact.name}
              />
            ) : (
              <span>{alt || "图片"}</span>
            );
          },
          a: ({ href, children }) =>
            href ? <a href={href}>{children}</a> : <span>{children}</span>,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}

export function Status({ value }: { value: string }) {
  const active = [
    "RUNNING",
    "thinking",
    "compacting",
    "executing",
    "waiting_job",
    "preparing_artifacts",
  ].includes(value);
  const tone = ["COMPLETED", "READY", "completed", "approved"].includes(value)
    ? "green"
    : ["FAILED", "INTERRUPTED", "recoverable_error"].includes(value)
      ? "red"
      : ["ready", "waiting_user", "CANCELLING", "PAUSING"].includes(value)
        ? "amber"
        : active
          ? "purple"
          : "gray";
  return (
    <span className={`status-tag ${tone}`}>
      <span className={`status-dot ${active ? "pulse" : ""}`} />
      {statuses[value] || value}
    </span>
  );
}
export function ErrorBox({ error }: { error: string }) {
  return error ? (
    <Alert type="error" showIcon title={error} className="error" />
  ) : null;
}
export function Raw({ value }: { value: unknown }) {
  return <pre className="result-json">{pretty(value)}</pre>;
}

function Table({ data }: { data: Json }) {
  const object =
    data && typeof data === "object" && !Array.isArray(data) ? data : null;
  const rows = Array.isArray(data)
    ? data
    : object && Array.isArray(object.rows)
      ? object.rows
      : [];
  if (!rows.length) return <Raw value={data} />;
  const declared =
    object && Array.isArray(object.columns) ? object.columns : null;
  const keys = [
    ...new Set(
      rows.flatMap((row) =>
        row && typeof row === "object" && !Array.isArray(row)
          ? Object.keys(row)
          : [],
      ),
    ),
  ];
  const columns = declared
    ? declared.map((column, index) => {
        if (typeof column === "string")
          return { key: column, label: column, index };
        if (column && typeof column === "object" && !Array.isArray(column)) {
          const key = column.key ?? column.dataIndex;
          return {
            key: typeof key === "string" ? key : String(index),
            label: String(column.title ?? column.label ?? key ?? index),
            index,
          };
        }
        return { key: String(index), label: String(column), index };
      })
    : keys.map((key, index) => ({ key, label: key, index }));
  if (!columns.length) return <Raw value={data} />;
  return (
    <div className="table-scroll">
      <table className="result-table">
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key}><MathText inlineOnly>{c.label}</MathText></th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              {columns.map((c) => {
                const value = Array.isArray(row)
                  ? row[c.index]
                  : row && typeof row === "object"
                    ? row[c.key]
                    : row;
                return (
                  <td key={c.key}>
                    {value == null
                      ? ""
                      : typeof value === "object"
                        ? pretty(value)
                        : typeof value === "string"
                          ? <MathText inlineOnly>{value}</MathText>
                          : String(value)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
export function Result({ report }: { report: Report }) {
  const artifacts = new Map(report.artifacts.map((a) => [a.id, a]));
  const blocks = Array.isArray(report.result.blocks)
    ? report.result.blocks
    : [];
  return (
    <div className="agent-result">
      {report.result.title && (
        <h1 className="report-title"><MathText inlineOnly>{String(report.result.title)}</MathText></h1>
      )}
      {reportBlocks(blocks).map(({ block, id, title }) => {
        if (!block || typeof block !== "object" || Array.isArray(block))
          return (
            <section id={id} className="result-block" key={id}>
              <Raw value={block} />
            </section>
          );
        const value = block as Record<string, Json>;
        const data = value.data;
        const text =
          typeof data === "string"
            ? data
            : data &&
                typeof data === "object" &&
                !Array.isArray(data) &&
                typeof data.text === "string"
              ? data.text
              : null;
        const artifactId =
          data && typeof data === "object" && !Array.isArray(data)
            ? data.artifactId
            : null;
        const artifact =
          typeof artifactId === "string" ? artifacts.get(artifactId) : null;
        const object =
          data && typeof data === "object" && !Array.isArray(data) ? data : {};
        const caption =
          typeof object.caption === "string" ? object.caption : "";
        const known =
          value.type === "text" ||
          value.type === "table" ||
          value.type === "image" ||
          value.type === "file";
        return (
          <section key={id} id={id} className="result-block">
            {title && <h3><MathText inlineOnly>{title}</MathText></h3>}
            {value.type === "text" && text !== null ? (
              <Markdown artifacts={report.artifacts}>{text}</Markdown>
            ) : value.type === "table" ? (
              <>
                <Table data={data ?? null} />
              </>
            ) : artifact &&
              (value.type === "image" || value.type === "file") ? (
              <>
                {value.type === "image" &&
                imageTypes.has(artifact.mediaType) ? (
                  <img
                    src={`${root}/artifacts/${artifact.id}?preview=true`}
                    alt={title || artifact.name}
                  />
                ) : (
                  <a href={`${root}/artifacts/${artifact.id}`} download>
                    {String(
                      object.label ||
                        object.title ||
                        value.title ||
                        artifact.name,
                    )}
                  </a>
                )}
              </>
            ) : (
              <Raw value={value} />
            )}
            {known && caption && (
              <div className="report-caption">
                <Markdown artifacts={report.artifacts}>{caption}</Markdown>
              </div>
            )}
          </section>
        );
      })}
      <Details title="完整数据">
        <Raw value={report.result} />
      </Details>
    </div>
  );
}
