import { useEffect, useState } from "react";
import { Button, Form, Input, Modal, Space } from "antd";
import { FolderOpenOutlined } from "@ant-design/icons";
import { ErrorBox, Panel } from "./ui";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;
function nativeInvoke(): Invoke | undefined {
  return (window as Window & { __TAURI__?: { core?: { invoke?: Invoke } } })
    .__TAURI__?.core?.invoke;
}

export function DesktopStorage({ disabled }: { disabled: boolean }) {
  const invoke = nativeInvoke();
  const [current, setCurrent] = useState("");
  const [directory, setDirectory] = useState("");
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let disposed = false;
    if (invoke) void invoke<string>("storage_info").then((path) => {
      if (!disposed) setCurrent(path);
    }).catch((e) => { if (!disposed) setError(String(e)); });
    return () => { disposed = true; };
  }, [invoke]);
  if (!invoke) return null;
  return (
    <Panel title="数据目录">
      <div className="panel-body settings-form">
        <Form layout="vertical">
          <Form.Item label="当前目录">
            <Input value={current} readOnly aria-label="当前数据目录" />
          </Form.Item>
          <Button disabled={disabled || !current} onClick={() => {
            setDirectory(""); setError(""); setOpen(true);
          }}>更改数据目录</Button>
          {disabled && <p className="muted">请先保存当前配置并关闭编辑窗口。</p>}
        </Form>
        {!open && <ErrorBox error={error} />}
        <Modal title="更改数据目录" open={open} okText="迁移并重启" cancelText="取消"
          confirmLoading={busy} closable={!busy} mask={{ closable: !busy }}
          cancelButtonProps={{ disabled: busy, "aria-label": "取消" }}
          okButtonProps={{ disabled: busy || !directory.trim(), "aria-label": "迁移并重启" }}
          onCancel={() => { if (!busy) setOpen(false); }}
          onOk={async () => {
            if (busy) return;
            setBusy(true); setError("");
            try { await invoke("migrate_data_directory", { directory: directory.trim() }); }
            catch (e) { setError(String(e)); setBusy(false); }
          }}>
          <Form layout="vertical">
            <Form.Item label="新目录" htmlFor="new-data-directory">
              <Space.Compact style={{ width: "100%" }}>
                <Input id="new-data-directory" aria-label="新数据目录" placeholder="选择空目录或输入新目录路径"
                  value={directory} disabled={busy} onChange={(e) => setDirectory(e.target.value)} />
                <Button icon={<FolderOpenOutlined />} disabled={busy} onClick={async () => {
                  setError("");
                  try {
                    const path = await invoke<string | null>("choose_data_directory");
                    if (path) setDirectory(path);
                  } catch (e) { setError(String(e)); }
                }}>浏览</Button>
              </Space.Compact>
            </Form.Item>
          </Form>
          <ErrorBox error={error} />
        </Modal>
      </div>
    </Panel>
  );
}
