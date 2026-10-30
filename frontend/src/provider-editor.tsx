import { useEffect, useState } from "react";
import {
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Switch,
} from "antd";
import { DeleteOutlined, PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import { api, useIntent } from "./client";
import { ErrorBox } from "./ui";
import { defaultModelPrice } from "./model-pricing";
import { DEFAULT_USD_TO_CNY, money } from "./money";

export type ProviderModel = {
  model: string;
  inputRate?: string;
  outputRate?: string;
};
export type Connection = {
  provider: string;
  id: string;
  name: string;
  version: number;
  baseUrl: string;
  models: ProviderModel[];
  auth: string;
  enabled: boolean;
  keyConfigured: boolean;
};
export type Preset = {
  id: string;
  name: string;
  baseUrl: string;
  auth: string;
  models?: string[];
  note?: string;
  docsUrl?: string;
};
type Draft = Omit<Connection, "id" | "version" | "keyConfigured"> & {
  apiKey?: string;
};

export function ProviderEditor({
  connection,
  presets,
  saved,
  close,
  removed,
  usdToCnyRate = DEFAULT_USD_TO_CNY,
}: {
  connection: Connection;
  presets: Preset[];
  saved: (value: Connection) => void;
  close: () => void;
  removed?: () => void;
  usdToCnyRate?: string | number;
}) {
  const [form] = Form.useForm<Draft>();
  const provider = Form.useWatch("provider", form);
  const baseUrl = Form.useWatch("baseUrl", form);
  const auth = Form.useWatch("auth", form);
  const apiKey = Form.useWatch("apiKey", form);
  const models: ProviderModel[] =
    Form.useWatch("models", { form, preserve: true }) || [];
  const preset = presets.find((p) => p.id === provider);
  const keepsKey =
    connection.keyConfigured &&
    connection.provider === provider &&
    connection.baseUrl === baseUrl?.replace(/\/+$/, "") &&
    connection.auth === auth;
  const [catalog, setCatalog] = useState<ProviderModel[]>([]);
  const [chosen, setChosen] = useState<string[]>([]);
  const [manual, setManual] = useState("");
  const [discovery, setDiscovery] = useState<{
    loading?: boolean;
    error?: string;
    count?: number;
  }>({});
  const [retry, setRetry] = useState(0);
  const [error, setError] = useState("");
  const [diagnostic, setDiagnostic] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const testIntent = useIntent();
  useEffect(() => {
    let disposed = false;
    setCatalog([]);
    setChosen([]);
    setDiscovery({});
    if (
      !baseUrl ||
      !/^https?:\/\/[^/\s]+/.test(baseUrl) ||
      (auth !== "none" && !apiKey?.trim() && !keepsKey)
    )
      return;
    setDiscovery({ loading: true });
    const timer = setTimeout(() => {
      void api<{ models: ProviderModel[] }>("/settings/models/discover", {
        provider,
        baseUrl,
        auth,
        apiKey: apiKey || undefined,
        connectionId: connection.version ? connection.id : undefined,
        expectedVersion: connection.version,
      })
        .then((value) => {
          if (disposed) return;
          setCatalog(value.models);
          setDiscovery({ count: value.models.length });
        })
        .catch((e) => {
          if (!disposed) setDiscovery({ error: String(e) });
        });
    }, 800);
    return () => {
      disposed = true;
      clearTimeout(timer);
    };
  }, [
    provider,
    baseUrl,
    auth,
    apiKey,
    keepsKey,
    connection.id,
    connection.version,
    retry,
  ]);

  function add(values: string[]) {
    const current: ProviderModel[] = form.getFieldValue("models") || [];
    const known = new Set(current.map((m) => m.model));
    const extra = values.map((m) => m.trim()).filter((m) => m && !known.has(m));
    form.setFieldValue("models", [
      ...current,
      ...[...new Set(extra)].map((model) => ({
        ...(catalog.find((m) => m.model === model) || { model }),
        ...defaultModelPrice(model, usdToCnyRate),
      })),
    ]);
    setChosen([]);
    setManual("");
  }
  async function save(testModel?: string) {
    try {
      await form.validateFields();
    } catch {
      return;
    }
    setBusy(true);
    setError("");
    setDiagnostic("");
    try {
      const value = form.getFieldsValue(true);
      const updated = await api<Connection>(
        `/settings/connections/${connection.id}`,
        {
          ...value,
          apiKey: value.apiKey || undefined,
          expectedVersion: connection.version,
        },
        "PUT",
      );
      saved(updated);
      form.setFieldValue("apiKey", undefined);
      if (testModel) {
        const result = await api<{
          message: string;
          calls: { estimatedCost?: string }[];
        }>(`/settings/connections/${updated.id}/test`, {
          model: testModel,
          clientRequestId: testIntent.key({
            id: updated.id,
            version: updated.version,
            model: testModel,
          }),
        });
        setDiagnostic(
          `${testModel}：${result.message}；已观察费用 ${money(result.calls.reduce((sum, call) => sum + Number(call.estimatedCost || 0), 0), 6)}`,
        );
        testIntent.done();
      } else close();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title={connection.version ? "管理提供商" : "添加提供商"}
      open
      width={840}
      onCancel={() => !busy && close()}
      footer={
        <Space>
          <Button disabled={busy} onClick={close}>
            取消
          </Button>
          <Button type="primary" loading={busy} onClick={() => void save()}>
            保存提供商
          </Button>
        </Space>
      }
    >
      <ErrorBox error={error} />
      <Form
        form={form}
        name="provider"
        layout="vertical"
        initialValues={connection}
        disabled={busy}
      >
        <div className="fields">
          <Form.Item name="provider" label="Provider">
            <Select
              options={presets.map((p) => ({ value: p.id, label: p.name }))}
              onChange={(id) => {
                const p = presets.find((p) => p.id === id);
                if (p)
                  form.setFieldsValue({
                    provider: p.id,
                    name: p.name,
                    baseUrl: p.baseUrl,
                    auth: p.auth,
                    apiKey: undefined,
                    models: [],
                  });
                setManual("");
                setDiagnostic("");
              }}
            />
          </Form.Item>
          <Form.Item
            name="name"
            label="提供商名称"
            rules={[{ required: true, whitespace: true }]}
          >
            <Input />
          </Form.Item>
        </div>
        <Form.Item
          name="baseUrl"
          label="API 地址"
          rules={[{ required: true }, { type: "url" }]}
        >
          <Input placeholder="https://api.example.com/v1" />
        </Form.Item>
        <div className="provider-auth-fields">
          <Form.Item name="apiKey" label="API Key">
            <Input.Password
              autoComplete="new-password"
              placeholder={
                keepsKey
                  ? "已配置，留空保持原密钥"
                  : "输入 API Key 后自动获取模型"
              }
            />
          </Form.Item>
          <Form.Item name="auth" label="鉴权方式">
            <Select
              options={[
                { value: "bearer", label: "Bearer API Key" },
                { value: "api-key", label: "api-key" },
                { value: "none", label: "无鉴权" },
              ]}
            />
          </Form.Item>
        </div>
        {preset?.docsUrl && (
          <p className="provider-note">
            {preset.note}{" "}
            <a href={preset.docsUrl} target="_blank" rel="noreferrer">
              官方文档
            </a>
          </p>
        )}
        <section className="provider-models">
          <div className="provider-models-heading">
            <strong>
              模型 <span className="muted">{models.length}</span>
            </strong>
            <Button
              size="small"
              icon={<ReloadOutlined />}
              loading={!!discovery.loading}
              disabled={
                busy || !baseUrl || (auth !== "none" && !apiKey && !keepsKey)
              }
              onClick={() => setRetry((n) => n + 1)}
            >
              获取模型
            </Button>
          </div>
          <div role="status" className="provider-note">
            {discovery.loading
              ? "正在获取模型列表…"
              : discovery.error ||
                (discovery.count !== undefined
                  ? discovery.count
                    ? `获取到 ${discovery.count} 个模型，选择后加入。`
                    : "该 API 未返回可用模型，可手动添加。"
                  : "填写 API 后自动获取，也可手动添加。")}
          </div>
          {catalog.length > 0 && (
            <div className="provider-add-models">
              <Select
                mode="multiple"
                aria-label="可用模型"
                placeholder="搜索并选择模型"
                value={chosen}
                maxTagCount="responsive"
                optionFilterProp="label"
                options={catalog.map((m) => ({
                  value: m.model,
                  label: m.model,
                  disabled: models.some((added) => added.model === m.model),
                }))}
                onChange={setChosen}
              />
              <Button disabled={!chosen.length} onClick={() => add(chosen)}>
                添加所选
              </Button>
            </div>
          )}
          <div className="provider-add-models">
            <Input
              aria-label="手动模型 ID"
              value={manual}
              placeholder="手动输入模型 ID"
              maxLength={200}
              onChange={(e) => setManual(e.target.value)}
              onPressEnter={(e) => {
                e.preventDefault();
                if (manual.trim()) add([manual]);
              }}
            />
            <Button
              aria-label="添加"
              icon={<PlusOutlined />}
              disabled={
                !manual.trim() || models.some((m) => m.model === manual.trim())
              }
              onClick={() => add([manual])}
            >
              添加
            </Button>
          </div>
          <Form.List name="models">
            {(fields, { remove }) => (
              <>
                {!!fields.length && (
                  <div className="provider-model-row provider-model-labels">
                    <span>模型 ID</span>
                    <span>输入（元／百万 token）</span>
                    <span>输出（元／百万 token）</span>
                    <span>操作</span>
                  </div>
                )}
                {fields.map((field) => (
                  <div className="provider-model-row" key={field.key}>
                    <div
                      className="provider-model-id"
                      title={models[field.name]?.model}
                    >
                      {models[field.name]?.model}
                      <Form.Item name={[field.name, "model"]} hidden>
                        <Input />
                      </Form.Item>
                    </div>
                    <Form.Item
                      name={[field.name, "inputRate"]}
                      rules={[{ required: true, message: "请填单价" }]}
                    >
                      <InputNumber
                        aria-label={`${models[field.name]?.model} 输入单价`}
                        min={0}
                        stringMode
                        placeholder="待填写"
                      />
                    </Form.Item>
                    <Form.Item
                      name={[field.name, "outputRate"]}
                      rules={[{ required: true, message: "请填单价" }]}
                    >
                      <InputNumber
                        aria-label={`${models[field.name]?.model} 输出单价`}
                        min={0}
                        stringMode
                        placeholder="待填写"
                      />
                    </Form.Item>
                    <Space size={0}>
                      <Button
                        size="small"
                        type="text"
                        title="保存配置并测试连接，会产生少量 API 用量"
                        onClick={() => void save(models[field.name]?.model)}
                      >
                        测试
                      </Button>
                      <Button
                        size="small"
                        type="text"
                        icon={<DeleteOutlined />}
                        aria-label={`移除 ${models[field.name]?.model}`}
                        onClick={() => remove(field.name)}
                      />
                    </Space>
                  </div>
                ))}
              </>
            )}
          </Form.List>
        </section>
        <div className="provider-controls">
          <Form.Item name="enabled" label="启用提供商" valuePropName="checked">
            <Switch />
          </Form.Item>
          {connection.version > 0 && (
            <Button
              danger
              icon={<DeleteOutlined />}
              disabled={busy}
              onClick={() => setConfirmDelete(true)}
            >
              删除提供商
            </Button>
          )}
        </div>
      </Form>
      <Modal
        title={`删除 ${connection.name}？`}
        open={confirmDelete}
        onCancel={() => !busy && setConfirmDelete(false)}
        okText="确认删除"
        cancelText="取消"
        confirmLoading={busy}
        okButtonProps={{ danger: true, "aria-label": "确认删除" }}
        cancelButtonProps={{ disabled: busy }}
        onOk={async () => {
          setBusy(true);
          setError("");
          try {
            await api(
              `/settings/connections/${connection.id}`,
              {
                expectedVersion: connection.version,
              },
              "DELETE",
            );
            removed?.();
            close();
          } catch (e) {
            setError(String(e));
          } finally {
            setConfirmDelete(false);
            setBusy(false);
          }
        }}
      >
        将移除模型配置和密钥。历史记录保留，关联会话需切换模型后继续。
      </Modal>
      {diagnostic && <p role="status">{diagnostic}</p>}
    </Modal>
  );
}
