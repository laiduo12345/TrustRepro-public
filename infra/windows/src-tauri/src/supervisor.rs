use crate::runtime::{self, Bundle, Endpoint};
use std::{
    fs,
    path::Path,
    time::{Duration, Instant},
};

pub fn prepare(
    bundle_root: &Path,
    data: &Path,
    distro: &str,
    progress: impl Fn(&str),
) -> Result<Endpoint, String> {
    fs::create_dir_all(data).map_err(|e| e.to_string())?;
    let manifest: Bundle = serde_json::from_slice(
        &fs::read(bundle_root.join("manifest.json")).map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())?;
    if manifest.runtime_version != 1 {
        return Err("安装包运行环境版本不兼容".into());
    }
    progress("正在检查系统组件…");
    if !runtime::installed(distro)? {
        if data.join("runtime-owned").exists() || data.join("runtime/ext4.vhdx").exists() {
            return Err("现有运行数据尚未注册，请先恢复原有环境；不会创建空环境覆盖数据".into());
        }
        progress("正在校验运行环境…");
        let archive = runtime::verify(bundle_root, &manifest, "runtime.tar.gz")?;
        progress("正在准备本地运行环境…");
        runtime::import(distro, &data.join("runtime"), &archive)?;
    }
    let registered = crate::storage::location(distro)?.ok_or("运行环境位置不可用")?;
    if !crate::storage::same_path(&registered, &data.join("runtime")) {
        return Err("运行环境位置与数据目录设置不一致，原有数据未作修改".into());
    }
    let _keepalive = runtime::RuntimeGuard(runtime::keepalive(distro)?);
    let probe: serde_json::Value =
        serde_json::from_str(&runtime::wsl(distro, &["probe"])?).map_err(|e| e.to_string())?;
    if probe["runtimeVersion"].as_u64() != Some(1) {
        return Err("已有同名环境与当前应用不兼容，原有环境未作修改".into());
    }
    if probe["release"]["version"].as_str() == Some(&manifest.version)
        && probe["release"]["releaseId"].as_str() != Some(&manifest.release_id)
    {
        return Err("相同版本号对应不同构建，请安装版本号更新的安装包".into());
    }
    if probe["release"]["version"].as_str() != Some(&manifest.version) {
        progress("正在校验应用资源…");
        let archive = runtime::verify(bundle_root, &manifest, "release.tar")?;
        progress("正在安装应用服务…");
        runtime::deploy(
            distro,
            &manifest.version,
            &archive,
            &data.join("install.log"),
        )?;
    }
    let port = match probe["port"].as_u64() {
        Some(port) if port <= 65535 => port as u16,
        _ => runtime::port()?,
    };
    progress("正在启动工作台…");
    runtime::control_logged(
        distro,
        &["start", &port.to_string()],
        &data.join("startup.log"),
    )?;
    let endpoint = runtime::endpoint(distro)?;
    let deadline = Instant::now() + Duration::from_secs(90);
    loop {
        if runtime::ready(&endpoint).is_ok() {
            fs::write(data.join("runtime-owned"), distro).map_err(|e| e.to_string())?;
            return Ok(endpoint);
        }
        if Instant::now() >= deadline {
            return Err("本机服务连接失败，请检查 WSL 网络配置后重试。诊断记录已保留。".into());
        }
        std::thread::sleep(Duration::from_millis(500));
    }
}
