import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Button, Pagination, Select } from "antd";
import zhCN from "antd/locale/zh_CN";
import {
  ArrowDownOutlined,
  ArrowLeftOutlined,
  CheckOutlined,
  ExperimentOutlined,
  RightOutlined,
} from "@ant-design/icons";
import type { Json } from "./client";
import type { ApprovedPlan, Page, ProjectSummary } from "./workspace-client";
import { money } from "./workspace-client";
import { BudgetEditor } from "./budget";
import { Raw } from "./ui";

export function WPanel({
  title,
  extra,
  children,
  className = "",
  body = false,
}: {
  title?: ReactNode;
  extra?: ReactNode;
  children: ReactNode;
  className?: string;
  body?: boolean;
}) {
  return (
    <section className={`manager-panel ${className}`}>
      {(title || extra) && (
        <div className="manager-panel-head">
          {title && <h2>{title}</h2>}
          {extra}
        </div>
      )}
      {body ? <div className="detail-panel-body">{children}</div> : children}
    </section>
  );
}
export function Stats({ items }: { items: [string, ReactNode, ReactNode][] }) {
  return (
    <div className="stats-strip-v2">
      {items.map(([label, value, icon]) => (
        <div className="stat-v2" key={label}>
          <div className="stat-v2-label">
            {icon}
            <span>{label}</span>
          </div>
          <div className="stat-v2-value">{value}</div>
        </div>
      ))}
    </div>
  );
}
export function Back({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link className="back-to-list" to={to}>
      <ArrowLeftOutlined />
      {children}
    </Link>
  );
}
export function Pager({
  data,
  label,
  onChange,
  extra,
}: {
  data: Pick<Page<unknown>, "page" | "pageSize" | "total">;
  label: string;
  onChange: (page: number, size: number) => void;
  extra?: ReactNode;
}) {
  const { page, pageSize, total } = data;
  return (
    <div className="table-footer">
      <div className="table-footer-summary">
        <span>
          共 {total} {label}
          {total > 0 &&
            ` · ${(page - 1) * pageSize + 1}–${Math.min(page * pageSize, total)}`}
        </span>
        {extra}
      </div>
      <div className="table-pagination">
        <Select
          aria-label="每页条数"
          size="small"
          value={pageSize}
          onChange={(size) => onChange(1, size)}
          options={[2, 5, 10, 20, 50].map((value) => ({
            value,
            label: `${value} 条/页`,
          }))}
        />
        <Pagination
          locale={zhCN.Pagination}
          current={page}
          pageSize={pageSize}
          total={total}
          size="small"
          showSizeChanger={false}
          showQuickJumper={Math.ceil(total / pageSize) > 7}
          onChange={(next) => onChange(next, pageSize)}
        />
      </div>
    </div>
  );
}
export function TimeSort({
  label,
  order,
  onChange,
}: {
  label: string;
  order: string;
  onChange: () => void;
}) {
  return (
    <button
      type="button"
      className="time-sort"
      aria-label={`按${label}${order === "desc" ? "升序" : "降序"}`}
      onClick={onChange}
    >
      {label}
      <ArrowDownOutlined rotate={order === "asc" ? 180 : 0} />
    </button>
  );
}
export function useDebounced<T>(value: T, delay = 250) {
  const [next, setNext] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setNext(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return next;
}
export function boundValue(parameters: Json, binding: string): Json {
  let value: Json = { parameters };
  for (const part of binding
    .split("/")
    .slice(1)
    .map((p) => p.replace(/~1/g, "/").replace(/~0/g, "~"))) {
    value =
      value !== null &&
      typeof value === "object" &&
      Object.prototype.hasOwnProperty.call(value, part)
        ? Array.isArray(value)
          ? value[Number(part)]
          : value[part]
        : null;
  }
  return value;
}
export function ApprovedSnapshot({
  plan,
  confirmed = false,
  expanded = false,
}: {
  plan: ApprovedPlan;
  confirmed?: boolean;
  expanded?: boolean;
}) {
  const [open, setOpen] = useState(expanded);
  const allocation = plan.allocation;
  return (
    <details
      className="task-snapshot"
      open={open}
      onToggle={(e) => setOpen(e.currentTarget.open)}
    >
      <summary>
        <ExperimentOutlined />
        <strong>{confirmed ? "复现参数" : "批准参数"}</strong>
        {confirmed && (
          <span className="chip green">
            <CheckOutlined />
            已确认
          </span>
        )}
        <RightOutlined />
      </summary>
      <div className="snapshot-grid">
        {plan.fields.map((field) => {
          const value = boundValue(plan.parameters, field.binding);
          const serialized =
            typeof value === "string" ? value : JSON.stringify(value);
          const complex =
            value !== null &&
            typeof value === "object" &&
            serialized.length > 80;
          return (
            <div
              className={`snapshot-item ${complex ? "complex-parameter" : ""}`}
              key={field.key}
            >
              <span>{field.label}</span>
              {complex ? (
                <details className="parameter-detail">
                  <summary>
                    {Array.isArray(value)
                      ? `${value.length} 项`
                      : `${Object.keys(value).length} 个字段`}
                  </summary>
                  <Raw value={value} />
                </details>
              ) : (
                <strong title={serialized}>{serialized}</strong>
              )}
            </div>
          );
        })}
      </div>
      <div className="snapshot-footer">
        <span>
          CPU {allocation.resources.cpus} 核 · {allocation.resources.memoryGb}{" "}
          GB 内存 · {allocation.resources.diskGb} GB 磁盘
        </span>
        <span>
          {money(allocation.maxCost)} · {allocation.maxHours} 小时
        </span>
      </div>
      <details className="snapshot-more">
        <summary>范围与方案</summary>
        <p>{plan.goal}</p>
        <p>{plan.methodology}</p>
        <Raw value={plan.parameters} />
        {plan.deliverables.length > 0 && (
          <>
            <h3>交付</h3>
            <ul>
              {plan.deliverables.map((item, i) => (
                <li key={i}>{item}</li>
              ))}
            </ul>
          </>
        )}
        {plan.limitations.length > 0 && (
          <>
            <h3>必要限制</h3>
            <ul>
              {plan.limitations.map((item, i) => (
                <li key={i}>{item}</li>
              ))}
            </ul>
          </>
        )}
      </details>
    </details>
  );
}
export function BudgetPanel({
  project,
  reload,
}: {
  project: ProjectSummary;
  reload: () => void;
}) {
  const a = project.allowance;
  return (
    <WPanel title="累计额度" className="allocation-panel" body>
      {a ? (
        <>
          <div className="allocation-value">
            {money(a.spentCost)} <small>/ {money(a.maxCost)}</small>
          </div>
          <div className="allocation-track">
            <span
              className="spent"
              style={{
                width: `${a.maxCost ? Math.min(100, (a.spentCost / a.maxCost) * 100) : 0}%`,
              }}
            />
            <span
              className="reserved"
              style={{
                width: `${a.maxCost ? Math.min(100, (a.reservedCost / a.maxCost) * 100) : 0}%`,
              }}
            />
          </div>
          {[
            ["已用", a.spentCost],
            ["预留", a.reservedCost],
            ["剩余", Math.max(0, a.maxCost - a.spentCost - a.reservedCost)],
          ].map(([label, value]) => (
            <div className="allocation-line" key={label}>
              <span>{label}</span>
              <strong>{money(Number(value))}</strong>
            </div>
          ))}
          <div className="divider" />
          <div className="allocation-line">
            <span>活跃时间</span>
            <strong>
              {(a.elapsedSeconds / 60).toFixed(1)} / {a.maxHours * 60} 分钟
            </strong>
          </div>
          <BudgetEditor project={project} changed={reload} block />
        </>
      ) : (
        <span className="muted">批准后启用</span>
      )}
    </WPanel>
  );
}
export function LoadError({
  error,
  retry,
}: {
  error: string;
  retry: () => void;
}) {
  return error ? (
    <div className="read-error" role="alert">
      {error}
      <Button size="small" onClick={retry}>
        重试
      </Button>
    </div>
  ) : null;
}
