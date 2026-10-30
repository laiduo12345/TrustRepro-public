import { useState } from "react";
import { Button, InputNumber, Modal } from "antd";
import { api, useIntent } from "./client";
import type { Project } from "./client";
import { ErrorBox } from "./ui";
import { money } from "./money";

export function BudgetEditor({
  project,
  changed,
  block = false,
}: {
  project: Project;
  changed: () => void;
  block?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [cost, setCost] = useState(0);
  const [hours, setHours] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const intent = useIntent();
  const allowance = project.allowance;
  if (!allowance) return <span className="muted">批准后启用</span>;
  return (
    <>
      <Button
        type={block ? "default" : "link"}
        block={block}
        onClick={() => {
          setCost(allowance.maxCost);
          setHours(allowance.maxHours);
          setError("");
          setOpen(true);
        }}
      >
        追加额度
      </Button>
      <Modal
        title="项目额度"
        open={open}
        okText="更新额度"
        confirmLoading={busy}
        onCancel={() => setOpen(false)}
        onOk={async () => {
          setBusy(true);
          setError("");
          const body = { currency: "CNY", maxCost: cost, maxHours: hours };
          try {
            await api(
              `/projects/${project.id}/budget`,
              { ...body, clientRequestId: intent.key(body) },
              "PUT",
            );
            intent.done();
            setOpen(false);
            changed();
          } catch (e) {
            setError(String(e));
          } finally {
            setBusy(false);
          }
        }}
      >
        <div className="budget-popover">
          <div className="info-row">
            <span>已用费用</span>
            <strong>{money(allowance.spentCost, 4)}</strong>
          </div>
          <div className="info-row">
            <span>未知费用预留</span>
            <span>{money(allowance.reservedCost, 4)}</span>
          </div>
          <div className="info-row">
            <span>活跃时间</span>
            <span>{(allowance.elapsedSeconds / 60).toFixed(1)} 分钟</span>
          </div>
          <div className="info-row">
            <span>剩余费用／时间</span>
            <span>
              {money(Math.max(
                0,
                allowance.maxCost -
                  allowance.spentCost -
                  allowance.reservedCost,
              ), 3)}{" "}
              / {(allowance.remainingSeconds / 60).toFixed(1)} 分钟
            </span>
          </div>
        </div>
        <div className="recovery-form">
          <label>累计费用上限（元）</label>
          <InputNumber
            value={cost}
            min={allowance.maxCost}
            onChange={(v) => setCost(v ?? allowance.maxCost)}
          />
          <label>累计活跃时间上限（小时）</label>
          <InputNumber
            value={hours}
            min={allowance.maxHours}
            onChange={(v) => setHours(v ?? allowance.maxHours)}
          />
        </div>
        <ErrorBox error={error} />
      </Modal>
    </>
  );
}
