import { useState, useRef, useEffect } from "react";
import { Button, Input, InputNumber, Select } from "antd";
import { CheckOutlined, ExperimentOutlined } from "@ant-design/icons";
import { api, pretty, uuid } from "./client";
import type { Card, Chat, Json, Field } from "./client";
import { Status, ErrorBox, Raw, Details, Markdown } from "./ui";
import { ApprovedSnapshot } from "./workspace-ui";

export function CardPanel({
  card,
  chatId,
  onChange,
}: {
  card: Card;
  chatId: string;
  onChange: (c: Chat) => void;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [allocation, setAllocation] = useState(card.allocation);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const current = useRef(card);
  const latest = useRef({ values, allocation });
  latest.current = { values, allocation };
  const saved = useRef(JSON.stringify(latest.current));
  const pending = useRef<Promise<void> | null>(null);
  const receipt = useRef<{ signature: string; key: string } | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => {
    if (card.revision > current.current.revision) {
      if (saved.current !== JSON.stringify(latest.current)) {
        setError(
          "方案已在其他位置更新；你的输入已保留，请核对最新方案后重新编辑。",
        );
      } else {
        current.current = card;
        setValues({});
        setAllocation(card.allocation);
        saved.current = JSON.stringify({
          values: {},
          allocation: card.allocation,
        });
      }
    }
  }, [card]);
  useEffect(() => {
    if (
      card.status !== "ready" ||
      busy ||
      saved.current === JSON.stringify(latest.current)
    )
      return;
    timer.current = setTimeout(() => {
      void persist().catch(() => {});
    }, 600);
    return () => clearTimeout(timer.current);
  }, [values, allocation, busy, card.status]);
  function initial(field: Field) {
    let value: Json = { parameters: card.parameters };
    for (const part of field.binding
      .split("/")
      .slice(1)
      .map((p) => p.replace(/~1/g, "/").replace(/~0/g, "~")))
      value =
        value && typeof value === "object"
          ? Array.isArray(value)
            ? value[Number(part)]
            : value[part]
          : null;
    return field.shape.type === "enum"
      ? JSON.stringify(value)
      : typeof value === "string"
        ? value
        : pretty(value);
  }
  async function persist(): Promise<void> {
    if (pending.current) {
      await pending.current;
      return persist();
    }
    const draft = latest.current;
    const signature = JSON.stringify(draft);
    if (signature === saved.current) return;
    pending.current = (async () => {
      setSaving(true);
      try {
        const parsed = Object.fromEntries(
          card.fields
            .filter((f) => f.key in draft.values)
            .map((f) => [
              f.key,
              f.shape.type === "string"
                ? draft.values[f.key]
                : JSON.parse(draft.values[f.key]),
            ]),
        );
        if (!receipt.current || receipt.current.signature !== signature)
          receipt.current = { signature, key: uuid() };
        const changed = await api<Chat>(
          `/conversations/${chatId}/cards/${card.id}`,
          {
            expectedVersion: current.current.version,
            expectedRevision: current.current.revision,
            clientRequestId: receipt.current.key,
            values: parsed,
            allocation: draft.allocation,
          },
          "PUT",
        );
        current.current = changed.card!;
        saved.current = signature;
        receipt.current = null;
        setError("");
        onChange(changed);
      } catch (e) {
        setError(
          e instanceof SyntaxError
            ? "参数尚未填写完整，请输入有效 JSON。"
            : String(e),
        );
        throw e;
      } finally {
        setSaving(false);
      }
    })();
    try {
      await pending.current;
    } finally {
      pending.current = null;
    }
    if (JSON.stringify(latest.current) !== saved.current) await persist();
  }
  async function save() {
    clearTimeout(timer.current);
    setBusy(true);
    try {
      await persist();
      onChange(
        await api<Chat>(
          `/conversations/${chatId}/cards/${current.current.id}/confirm`,
          {
            expectedVersion: current.current.version,
            expectedRevision: current.current.revision,
          },
        ),
      );
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  if (card.status !== "ready")
    return <ApprovedSnapshot plan={card} confirmed />;
  return (
    <article
      className="chat-task-card approval-card task-card-v2"
      onBlur={() => {
        if (!busy && card.status === "ready") void persist().catch(() => {});
      }}
    >
      <div className="chat-card-heading approval-header">
        <h2>
          <ExperimentOutlined /> {card.title}
        </h2>
        <Status value={card.status} />
      </div>
      <div className="approval-body">
        <span className="approval-label">目标与科学范围</span>
        <p className="task-goal approval-goal">{card.goal}</p>
        {card.status === "ready" && (
          <>
            <div className="task-adjustments">
              <h3 className="task-section-title">实验参数</h3>
              <div className="fields task-fields">
                {card.fields.map((f) => (
                  <label key={f.key}>
                    {f.label}
                    {f.shape.type === "enum" ? (
                      <Select
                        disabled={busy}
                        aria-label={f.label}
                        value={values[f.key] ?? initial(f)}
                        onChange={(value) =>
                          setValues({ ...values, [f.key]: value })
                        }
                        options={f.shape.choices?.map((choice) => ({
                          value: JSON.stringify(choice),
                          label:
                            typeof choice === "string"
                              ? choice
                              : pretty(choice),
                        }))}
                      />
                    ) : (
                      <Input
                        disabled={busy}
                        aria-label={f.label}
                        value={values[f.key] ?? initial(f)}
                        onChange={(e) =>
                          setValues({ ...values, [f.key]: e.target.value })
                        }
                      />
                    )}
                  </label>
                ))}
              </div>
            </div>
            <div className="task-adjustments">
              <h3 className="task-section-title">资源与累计预算</h3>
              <div className="fields allocation-fields task-resources">
                {(["maxCost", "maxHours"] as const).map((k) => (
                  <label key={k}>
                    {k === "maxCost"
                      ? "费用上限（元）"
                      : "账户活跃时间（小时）"}
                    <InputNumber
                      disabled={busy}
                      min={0}
                      step={0.1}
                      value={allocation[k]}
                      onChange={(value) =>
                        setAllocation({
                          ...allocation,
                          [k]: value ?? 0,
                        })
                      }
                    />
                  </label>
                ))}
                {(["cpus", "memoryGb", "diskGb"] as const).map((k) => (
                  <label key={k}>
                    {
                      {
                        cpus: "CPU",
                        memoryGb: "内存（GB）",
                        diskGb: "磁盘（GB）",
                      }[k]
                    }
                    <InputNumber
                      disabled={busy}
                      min={1}
                      value={allocation.resources[k]}
                      onChange={(value) =>
                        setAllocation({
                          ...allocation,
                          resources: {
                            ...allocation.resources,
                            [k]: value ?? 1,
                          },
                        })
                      }
                    />
                  </label>
                ))}
              </div>
            </div>
            {card.deliverables.length > 0 && (
              <>
                <h3 className="task-section-title">交付内容</h3>
                <div className="task-deliveries">
                  {card.deliverables.map((item, index) => (
                    <span className="chip" key={index}>
                      {item}
                    </span>
                  ))}
                </div>
              </>
            )}
            <Details title="技术方案">
              <Markdown>{card.methodology}</Markdown>
              <Raw value={card.parameters} />
              {card.limitations.length > 0 && (
                <>
                  <h3>必要限制</h3>
                  <ul>
                    {card.limitations.map((s, i) => (
                      <li key={i}>{s}</li>
                    ))}
                  </ul>
                </>
              )}
            </Details>
            <div className="chat-card-actions approval-footer">
              <span className="autosave-state" aria-live="polite">
                {saving ? "正在保存" : ""}
              </span>
              <Button
                type="primary"
                loading={busy}
                icon={<CheckOutlined />}
                onClick={() => void save()}
              >
                确认并开始
              </Button>
            </div>
          </>
        )}
        <ErrorBox error={error} />
      </div>
    </article>
  );
}
