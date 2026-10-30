use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::HashMap,
    fs,
    io::Read,
    net::TcpListener,
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    time::Duration,
};

pub const DISTRO: &str = "TrustRepro";

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Bundle {
    pub version: String,
    pub runtime_version: u32,
    pub release_id: String,
    pub files: HashMap<String, String>,
}

#[derive(Deserialize, Serialize)]
pub struct Endpoint {
    pub port: u16,
    pub token: String,
}

pub fn command(program: &str) -> Command {
    let mut command = Command::new(program);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    command
}

pub fn output(command: &mut Command) -> Result<String, String> {
    let out = command.output().map_err(|e| e.to_string())?;
    if !out.status.success() {
        return Err(format!("{}\n{}", decode(&out.stdout), decode(&out.stderr))
            .trim()
            .to_owned());
    }
    Ok(decode(&out.stdout))
}

fn decode(bytes: &[u8]) -> String {
    if bytes.contains(&0) {
        let words: Vec<u16> = bytes
            .chunks_exact(2)
            .map(|v| u16::from_le_bytes([v[0], v[1]]))
            .collect();
        String::from_utf16_lossy(&words)
            .trim_start_matches('\u{feff}')
            .to_string()
    } else {
        String::from_utf8_lossy(bytes).to_string()
    }
}

pub fn wsl(distro: &str, args: &[&str]) -> Result<String, String> {
    output(
        command("wsl.exe")
            .args([
                "--distribution",
                distro,
                "--user",
                "root",
                "--exec",
                "/usr/local/bin/trustreproctl",
            ])
            .args(args),
    )
}

pub fn installed(distro: &str) -> Result<bool, String> {
    let list = output(command("wsl.exe").args(["--list", "--quiet"]))?;
    Ok(list.lines().any(|s| s.trim() == distro))
}

pub fn verify(root: &Path, manifest: &Bundle, name: &str) -> Result<PathBuf, String> {
    if !matches!(name, "runtime.tar.gz" | "release.tar") {
        return Err("Invalid bundle resource".into());
    }
    let expected = manifest.files.get(name).ok_or("安装包资源不完整")?;
    let path = root.join(name);
    let mut file = fs::File::open(&path).map_err(|e| e.to_string())?;
    let mut hash = Sha256::new();
    let mut buffer = vec![0; 1024 * 1024];
    loop {
        let n = file.read(&mut buffer).map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
        hash.update(&buffer[..n]);
    }
    if hex::encode(hash.finalize()) != *expected {
        return Err(format!("安装包中的 {name} 校验失败，请重新下载安装包"));
    }
    Ok(path)
}

pub fn import(distro: &str, location: &Path, archive: &Path) -> Result<(), String> {
    fs::create_dir_all(location).map_err(|e| e.to_string())?;
    output(
        command("wsl.exe")
            .arg("--import")
            .arg(distro)
            .arg(location)
            .arg(archive)
            .args(["--version", "2"]),
    )?;
    Ok(())
}

pub fn deploy(distro: &str, version: &str, archive: &Path, log: &Path) -> Result<(), String> {
    let out = fs::File::create(log).map_err(|e| e.to_string())?;
    let mut child = command("wsl.exe")
        .args([
            "--distribution",
            distro,
            "--user",
            "root",
            "--exec",
            "/usr/bin/python3",
            "-c",
            // Keep the archive on a private descriptor and give legacy idle
            // subprocesses /dev/null. Old runtime roots otherwise let Compose
            // consume stdin before the bootstrap has read the release archive.
            "import os,sys,importlib.util; source=os.fdopen(os.dup(0),'rb'); null=os.open(os.devnull,os.O_RDONLY); os.dup2(null,0); os.close(null); spec=importlib.util.spec_from_file_location('tr_bootstrap','/usr/local/lib/trustrepro/bootstrap.py'); module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); module.deploy(sys.argv[1],source)",
            version,
        ])
        .stdin(Stdio::piped())
        .stdout(Stdio::from(out.try_clone().map_err(|e| e.to_string())?))
        .stderr(Stdio::from(out))
        .spawn()
        .map_err(|e| e.to_string())?;
    let mut input = fs::File::open(archive).map_err(|e| e.to_string())?;
    let copied = std::io::copy(&mut input, &mut child.stdin.take().ok_or("Missing pipe")?);
    let status = child.wait().map_err(|e| e.to_string())?;
    if !status.success() {
        return Err(read_log(log));
    }
    copied.map_err(|e| e.to_string())?;
    Ok(())
}

pub fn control_logged(distro: &str, args: &[&str], log: &Path) -> Result<(), String> {
    let file = fs::File::create(log).map_err(|e| e.to_string())?;
    let status = command("wsl.exe")
        .args([
            "--distribution",
            distro,
            "--user",
            "root",
            "--exec",
            "/usr/local/bin/trustreproctl",
        ])
        .args(args)
        .stdout(Stdio::from(file.try_clone().map_err(|e| e.to_string())?))
        .stderr(Stdio::from(file))
        .status()
        .map_err(|e| e.to_string())?;
    if status.success() {
        Ok(())
    } else {
        let detail = read_log(log);
        Err(detail
            .chars()
            .rev()
            .take(3000)
            .collect::<String>()
            .chars()
            .rev()
            .collect())
    }
}

fn read_log(path: &Path) -> String {
    // wsl.exe can mix UTF-16 startup warnings with UTF-8 child output.
    let bytes = fs::read(path).unwrap_or_default();
    let text: String = String::from_utf8_lossy(&bytes)
        .chars()
        .filter(|c| *c != '\0')
        .collect();
    if text.trim().is_empty() {
        "运行环境操作未完成，请查看诊断记录后重试".into()
    } else {
        text
    }
}

pub fn endpoint(distro: &str) -> Result<Endpoint, String> {
    serde_json::from_str(&wsl(distro, &["endpoint"])?).map_err(|e| e.to_string())
}

pub fn keepalive(distro: &str) -> Result<Child, String> {
    command("wsl.exe")
        .args([
            "--distribution",
            distro,
            "--user",
            "root",
            "--exec",
            "/usr/bin/sleep",
            "infinity",
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| e.to_string())
}

pub struct RuntimeGuard(pub Child);

impl Drop for RuntimeGuard {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

pub fn port() -> Result<u16, String> {
    let listener = TcpListener::bind("127.0.0.1:0").map_err(|e| e.to_string())?;
    Ok(listener.local_addr().map_err(|e| e.to_string())?.port())
}

pub fn ready(endpoint: &Endpoint) -> Result<serde_json::Value, String> {
    let client = reqwest::blocking::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(10))
        .build()
        .map_err(|e| e.to_string())?;
    client
        .get(format!(
            "http://127.0.0.1:{}/api/v1/desktop/status",
            endpoint.port
        ))
        .bearer_auth(&endpoint.token)
        .send()
        .and_then(|r| r.error_for_status())
        .and_then(|r| r.json())
        .map_err(|e| e.to_string())
}

pub fn resume_after_restart() -> Result<(), String> {
    let executable = std::env::current_exe().map_err(|e| e.to_string())?;
    output(
        command("reg.exe")
            .args([
                "add",
                "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\RunOnce",
                "/v",
                "TrustReproResume",
                "/t",
                "REG_SZ",
                "/d",
            ])
            .arg(format!("\"{}\" --resume", executable.display()))
            .arg("/f"),
    )?;
    Ok(())
}

#[cfg(windows)]
pub fn prepare_wsl() -> Result<crate::setup::Outcome, String> {
    if !is_administrator()? {
        return Err("请重新打开应用并完成启动时的管理员授权".into());
    }
    // The GUI already holds the elevated token. Ordinary child processes inherit
    // it; running another `runas` helper would add an unnecessary UAC boundary.
    crate::setup::prepare(&mut WslSetup)
}

#[cfg(windows)]
pub fn is_administrator() -> Result<bool, String> {
    use std::mem::size_of;
    use windows_sys::Win32::{
        Foundation::CloseHandle,
        Security::{GetTokenInformation, TokenElevation, TOKEN_ELEVATION, TOKEN_QUERY},
        System::Threading::{GetCurrentProcess, OpenProcessToken},
    };
    unsafe {
        let mut token = std::ptr::null_mut();
        if OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token) == 0 {
            return Err(std::io::Error::last_os_error().to_string());
        }
        let mut elevation: TOKEN_ELEVATION = std::mem::zeroed();
        let mut returned = 0;
        let success = GetTokenInformation(
            token,
            TokenElevation,
            &mut elevation as *mut _ as *mut _,
            size_of::<TOKEN_ELEVATION>() as u32,
            &mut returned,
        );
        let error = std::io::Error::last_os_error();
        CloseHandle(token);
        if success == 0 {
            Err(error.to_string())
        } else {
            Ok(elevation.TokenIsElevated != 0)
        }
    }
}

/// Returns true in the elevated GUI; false in the launcher after handing off.
#[cfg(windows)]
pub fn ensure_administrator() -> Result<bool, String> {
    if is_administrator()? {
        return Ok(true);
    }
    use std::{mem::size_of, os::windows::ffi::OsStrExt};
    use windows_sys::Win32::{
        Foundation::{CloseHandle, ERROR_CANCELLED},
        UI::Shell::{
            ShellExecuteExW, SEE_MASK_NOASYNC, SEE_MASK_NOCLOSEPROCESS, SHELLEXECUTEINFOW,
        },
    };
    let executable = std::env::current_exe().map_err(|e| e.to_string())?;
    let wide = |s: &std::ffi::OsStr| -> Vec<u16> { s.encode_wide().chain(Some(0)).collect() };
    let exe = wide(executable.as_os_str());
    let verb = wide(std::ffi::OsStr::new("runas"));
    unsafe {
        let mut info: SHELLEXECUTEINFOW = std::mem::zeroed();
        info.cbSize = size_of::<SHELLEXECUTEINFOW>() as u32;
        // The launcher exits immediately after handoff, so the shell must finish
        // launching the elevated process before returning.
        info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC;
        info.lpVerb = verb.as_ptr();
        info.lpFile = exe.as_ptr();
        // No command lines from the unelevated process are forwarded. Startup
        // and --resume use the same durable registry recovery on the new GUI.
        info.nShow = 1;
        if ShellExecuteExW(&mut info) == 0 {
            let error = std::io::Error::last_os_error();
            return Err(if error.raw_os_error() == Some(ERROR_CANCELLED as i32) {
                "启动需要管理员权限。系统授权已取消，请重新打开 TrustRepro 并选择“是”。".into()
            } else {
                format!("无法以管理员权限启动 TrustRepro：{error}")
            });
        }
        if !info.hProcess.is_null() {
            CloseHandle(info.hProcess);
        }
        Ok(false)
    }
}

#[cfg(not(windows))]
pub fn ensure_administrator() -> Result<bool, String> {
    Err("Windows only".into())
}

#[cfg(not(windows))]
pub fn prepare_wsl() -> Result<crate::setup::Outcome, String> {
    Err("Windows only".into())
}

struct WslSetup;

impl crate::setup::Host for WslSetup {
    fn run(&mut self, step: crate::setup::Step) -> Result<crate::setup::CommandResult, String> {
        let out = command("wsl.exe").args(step.args()).output().map_err(|e| {
            format!("无法运行 WSL 系统组件。请通过 Windows 更新安装 WSL 后重试。\n{e}")
        })?;
        Ok(crate::setup::CommandResult {
            code: out.status.code(),
            detail: format!("{}\n{}", decode(&out.stdout), decode(&out.stderr))
                .trim()
                .into(),
        })
    }

    fn restart_pending(&mut self) -> Result<bool, String> {
        let pending = output(command("powershell.exe").args([
            "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
            "$ErrorActionPreference='Stop'; try { $pending=$false; foreach ($name in @('VirtualMachinePlatform','Microsoft-Windows-Subsystem-Linux')) { $state=(Get-WindowsOptionalFeature -Online -FeatureName $name).State.ToString(); if ($state -in @('EnablePending','DisablePending')) { $pending=$true } }; if ($pending) { 'true' } else { 'false' } } catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }",
        ])).map_err(|e| format!("无法检查系统组件是否需要重启。请重试。\n{e}"))?;
        match pending.trim() {
            "true" => Ok(true),
            "false" => Ok(false),
            _ => Err("系统组件状态无法识别，请重试".into()),
        }
    }

    fn schedule_resume(&mut self) -> Result<(), String> {
        resume_after_restart()
    }
}

pub fn open_external(url: &str) {
    if !url.starts_with("https://") && !url.starts_with("http://") {
        return;
    }
    #[cfg(windows)]
    unsafe {
        let target: Vec<u16> = url.encode_utf16().chain(Some(0)).collect();
        windows_sys::Win32::UI::Shell::ShellExecuteW(
            std::ptr::null_mut(),
            std::ptr::null(),
            target.as_ptr(),
            std::ptr::null(),
            std::ptr::null(),
            1,
        );
    }
}
