import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Button, Form, InputNumber, Modal, Select, Table, Tag } from "antd";
import {
  CheckCircleOutlined,
  ExclamationCircleOutlined,
  PlusOutlined,
  SettingOutlined,
} from "@ant-design/icons";
import { api, uuid } from "./client";
import type { Allocation, Chat, ModelRole, ModelSelections } from "./client";
import { ErrorBox, PageHeader, Panel } from "./ui";
import { ProviderEditor } from "./provider-editor";
import type { Connection, Preset } from "./provider-editor";
import { DesktopStorage } from "./desktop-storage";

type Settings = {
  currency: "CNY";
  usdToCnyRate: string;
  revision: number;
  models: ModelSelections;
  allocation: Allocation;
  connections: Connection[];
  presets: Preset[];
};
const modelRoles: ModelRole[] = ["research", "execution"];
const roleLabels = { research: "研究", execution: "执行" };
function modelOptions(data: Settings | undefined, connectionId?: string) {
  return (
    data?.connections
      .find((c) => c.id === connectionId)
      ?.models.map((m) => ({ value: m.model, label: m.model })) || []
  );
}

export function SettingsPage() {
  const setup = new URLSearchParams(window.location.search).get("setup") === "1";
  const [data, setData] = useState<Settings>();
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<Connection>();
  const [busy, setBusy] = useState(false);
  const [defaultsDirty, setDefaultsDirty] = useState(false);
  const saving = useRef(false);
  const [saveFeedback, setSaveFeedback] = useState<{
    kind: "success" | "error";
    text: string;
  }>();
  const [defaults] = Form.useForm<{
    models: ModelSelections;
    allocation: Allocation;
    usdToCnyRate: string;
  }>();
  const defaultModels = Form.useWatch("models", defaults);
  async function load() {
    const value = await api<Settings>("/settings");
    setData(value);
    defaults.setFieldsValue(value);
    setDefaultsDirty(false);
  }
  useEffect(() => {
    void load().catch((e) => setError(String(e)));
  }, []);
  useEffect(() => {
    if (saveFeedback?.kind !== "success") return;
    const timer = window.setTimeout(() => setSaveFeedback(undefined), 4000);
    return () => window.clearTimeout(timer);
  }, [saveFeedback]);
  return (
    <>
      <PageHeader title={setup ? "配置模型" : "设置"} />
      <ErrorBox error={error} />
      <Panel
        title="模型提供商"
        extra={
          <Button
            icon={<PlusOutlined />}
            disabled={!data}
            onClick={() => {
              const preset = data!.presets[0];
              setEditing({
                id: "connection-" + uuid(),
                version: 0,
                provider: preset.id,
                name: preset.name,
                baseUrl: preset.baseUrl,
                auth: preset.auth,
                models: [],
                enabled: true,
                keyConfigured: false,
              });
            }}
          >
            添加提供商
          </Button>
        }
      >
        <Table<Connection>
          rowKey="id"
          dataSource={data?.connections || []}
          pagination={false}
          columns={[
            { title: "提供商", dataIndex: "name", width: 180 },
            {
              title: "已添加模型",
              render: (_, row) => (
                <div className="provider-model-tags">
                  {row.models.length ? (
                    row.models.map((m) => <Tag key={m.model}>{m.model}</Tag>)
                  ) : (
                    <span className="muted">尚未添加</span>
                  )}
                </div>
              ),
            },
            { title: "API 地址", dataIndex: "baseUrl", ellipsis: true },
            {
              title: "状态",
              width: 90,
              render: (_, row) => (row.enabled ? "已启用" : "已停用"),
            },
            {
              title: "操作",
              width: 90,
              render: (_, row) => (
                <Button type="link" onClick={() => setEditing(row)}>
                  管理
                </Button>
              ),
            },
          ]}
        />
      </Panel>
      {data && (
        <Panel title="默认任务配置">
          <div className="panel-body settings-form">
            <Form
              name="task-defaults"
              form={defaults}
              layout="vertical"
              disabled={busy}
              scrollToFirstError={{ focus: true }}
              onValuesChange={() => { setSaveFeedback(undefined); setDefaultsDirty(true); }}
              onFinishFailed={() =>
                setSaveFeedback({ kind: "error", text: "请检查标红的配置项" })
              }
              onFinish={async (values) => {
                if (saving.current) return;
                saving.current = true;
                setBusy(true);
                setError("");
                setSaveFeedback(undefined);
                try {
                  const saved = await api<Settings>(
                    "/settings",
                    { ...values, expectedRevision: data.revision },
                    "PUT",
                  );
                  setData(saved);
                  defaults.setFieldsValue(saved);
                  setDefaultsDirty(false);
                  setSaveFeedback({ kind: "success", text: "已保存" });
                  if (setup) {
                    const ready = await api<{ setupRequired: boolean }>("/desktop/status");
                    if (ready.setupRequired) {
                      setSaveFeedback({ kind: "error", text: "请补全所选提供商的密钥和模型配置" });
                    } else window.location.assign("/");
                  }
                } catch (e) {
                  setSaveFeedback({
                    kind: "error",
                    text: "保存失败：" + (e instanceof Error ? e.message : String(e)),
                  });
                } finally {
                  saving.current = false;
                  setBusy(false);
                }
              }}
            >
              <div className="fields">
                {modelRoles.map((role) => (
                  <div className="default-model-fields" key={role}>
                    <Form.Item
                      name={["models", role, "connectionId"]}
                      label={`默认${roleLabels[role]}连接`}
                      rules={[{ required: true }]}
                    >
                      <Select
                        options={data.connections
                          .filter((c) => c.enabled)
                          .map((c) => ({ value: c.id, label: c.name }))}
                        onChange={(id) =>
                          defaults.setFieldValue(
                            ["models", role, "model"],
                            data.connections.find((c) => c.id === id)?.models[0]
                              ?.model,
                          )
                        }
                      />
                    </Form.Item>
                    <Form.Item
                      name={["models", role, "model"]}
                      label={`默认${roleLabels[role]}模型`}
                      rules={[{ required: true, whitespace: true }]}
                    >
                      <Select
                        showSearch
                        optionFilterProp="label"
                        options={modelOptions(
                          data,
                          defaultModels?.[role]?.connectionId,
                        )}
                      />
                    </Form.Item>
                  </div>
                ))}
                <Form.Item
                  name={["allocation", "maxCost"]}
                  label="复现费用上限（元）"
                  rules={[{ required: true }]}
                >
                  <InputNumber min={0} />
                </Form.Item>
                <Form.Item
                  name="usdToCnyRate"
                  label="参考汇率"
                  tooltip="1 美元对应的人民币金额，仅用于新增模型的外币参考价格换算。"
                  rules={[{ required: true }]}
                >
                  <InputNumber stringMode min="0.000001" max="1000000" />
                </Form.Item>
                <Form.Item
                  name={["allocation", "maxHours"]}
                  label="复现活跃时间（小时）"
                  rules={[{ required: true }]}
                >
                  <InputNumber min={0.01} />
                </Form.Item>
                {(["cpus", "memoryGb", "diskGb"] as const).map((k) => (
                  <Form.Item
                    key={k}
                    name={["allocation", "resources", k]}
                    label={
                      {
                        cpus: "CPU",
                        memoryGb: "内存（GB）",
                        diskGb: "磁盘（GB）",
                      }[k]
                    }
                    rules={[{ required: true }]}
                  >
                    <InputNumber min={1} />
                  </Form.Item>
                ))}
                <Form.Item name={["allocation", "resources", "gpus"]} hidden>
                  <InputNumber />
                </Form.Item>
              </div>
              <div className="settings-save-actions">
                <Button
                  htmlType="submit"
                  type="primary"
                  loading={busy}
                  aria-busy={busy}
                  aria-label={busy ? "保存中" : setup ? "保存并开始使用" : "保存默认配置"}
                >
                  {busy ? "保存中" : setup ? "保存并开始使用" : "保存默认配置"}
                </Button>
                <span
                  className={`settings-save-feedback ${saveFeedback?.kind || ""}`}
                  role={saveFeedback?.kind === "error" ? "alert" : "status"}
                  aria-live={saveFeedback?.kind === "error" ? "assertive" : "polite"}
                  aria-atomic="true"
                >
                  {saveFeedback && (
                    <>
                      {saveFeedback.kind === "success" ? (
                        <CheckCircleOutlined aria-hidden="true" />
                      ) : (
                        <ExclamationCircleOutlined aria-hidden="true" />
                      )}
                      <span>{saveFeedback.text}</span>
                    </>
                  )}
                </span>
              </div>
            </Form>
          </div>
        </Panel>
      )}
      <DesktopStorage disabled={busy || defaultsDirty || !!editing} />
      {editing && data && (
        <ProviderEditor
          key={editing.id}
          connection={editing}
          presets={data.presets}
          usdToCnyRate={data.usdToCnyRate}
          close={() => setEditing(undefined)}
          removed={() => {
            setData(
              (current) =>
                current && {
                  ...current,
                  connections: current.connections.filter(
                    (c) => c.id !== editing.id,
                  ),
                },
            );
            void load().catch((e) => setError(String(e)));
          }}
          saved={(value) => {
            setEditing(value);
            setData(
              (current) =>
                current && {
                  ...current,
                  connections: current.connections.some(
                    (c) => c.id === value.id,
                  )
                    ? current.connections.map((c) =>
                        c.id === value.id ? value : c,
                      )
                    : [...current.connections, value],
                },
            );
          }}
        />
      )}
    </>
  );
}

export function ModelSelectors({
  chat,
  draft,
  disabled,
  onDraftChange,
  changed,
}: {
  chat?: Chat;
  draft?: ModelSelections;
  disabled: boolean;
  onDraftChange: (value: ModelSelections) => void;
  changed: (chat: Chat) => void;
}) {
  const [data, setData] = useState<Settings>();
  const [role, setRole] = useState<ModelRole>();
  const [connection, setConnection] = useState<string>();
  const [model, setModel] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let disposed = false;
    void api<Settings>("/settings")
      .then((value) => {
        if (!disposed) setData(value);
      })
      .catch((e) => {
        if (!disposed) setError(String(e));
      });
    return () => {
      disposed = true;
    };
  }, []);
  const choices = chat?.modelBindings || draft || data?.models;
  useEffect(() => {
    if (!chat && !draft && data) onDraftChange(data.models);
  }, [chat, draft, data, onDraftChange]);
  return (
    <div className="model-selectors">
      {modelRoles.map((item) => (
        <Button
          type="text"
          size="small"
          className="model-choice"
          key={item}
          aria-label={`${roleLabels[item]}模型：${choices?.[item]?.model || "选择模型"}`}
          title={`${roleLabels[item]}模型：${choices?.[item]?.model || "选择模型"}`}
          icon={<SettingOutlined />}
          disabled={
            disabled || !data || (chat && !chat.modelBindings[item].canChange)
          }
          onClick={() => {
            setRole(item);
            setConnection(choices?.[item]?.connectionId);
            setModel(choices?.[item]?.model || "");
            setError("");
          }}
        >
          {roleLabels[item]} · {choices?.[item]?.model || "选择模型"}
        </Button>
      ))}
      {!role && error && <ErrorBox error={error} />}
      <Modal
        title={role ? `${roleLabels[role]}模型` : "模型"}
        open={!!role}
        confirmLoading={busy}
        okText="应用"
        onCancel={() => !busy && setRole(undefined)}
        onOk={async () => {
          if (!role || !data) return;
          if (
            !connection ||
            !modelOptions(data, connection).some((m) => m.value === model)
          ) {
            setError("请选择提供商及已添加的模型");
            return;
          }
          setBusy(true);
          setError("");
          try {
            const selection = { connectionId: connection, model: model.trim() };
            if (chat) {
              changed(
                await api<Chat>(
                  `/conversations/${chat.id}/model`,
                  { role, ...selection },
                  "PUT",
                ),
              );
            } else {
              onDraftChange({ ...(draft || data.models), [role]: selection });
            }
            setRole(undefined);
          } catch (e) {
            setError(String(e));
          } finally {
            setBusy(false);
          }
        }}
      >
        <div className="recovery-form">
          <label htmlFor="role-model-connection">提供商</label>
          <Select
            id="role-model-connection"
            aria-label="模型连接"
            value={connection}
            options={data?.connections
              .filter((c) => c.enabled)
              .map((c) => ({ value: c.id, label: c.name }))}
            onChange={(id) => {
              setConnection(id);
              setModel(
                data?.connections.find((c) => c.id === id)?.models[0]?.model ||
                  "",
              );
            }}
          />
          <label htmlFor="role-model-id">模型 ID</label>
          <Select
            showSearch
            optionFilterProp="label"
            id="role-model-id"
            aria-label="模型 ID"
            value={model}
            options={modelOptions(data, connection)}
            onChange={setModel}
            placeholder="选择已添加的模型"
          />
        </div>
        <p className="provider-note">
          <Link to="/settings">管理提供商与模型</Link>
        </p>
        <ErrorBox error={error} />
      </Modal>
    </div>
  );
}
