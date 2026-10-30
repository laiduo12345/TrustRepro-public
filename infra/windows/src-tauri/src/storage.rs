//! Windows location metadata lives outside the movable Linux disk. Never export,
//! unregister, recreate, or delete a distro to recover an uncertain move.
use crate::runtime;
use serde::{Deserialize, Serialize};
use std::{
    fs,
    path::{Component, Path, PathBuf},
};
use winreg::{
    enums::{HKEY_CURRENT_USER, KEY_READ},
    RegKey,
};

const KEY: &str = "Software\\TrustReproDesktop";
const MARKER: &str = ".trustrepro-migration";

#[derive(Clone, Default, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Record {
    pub directory: Option<PathBuf>,
    pub pending: Option<Migration>,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Migration {
    pub id: String,
    pub source: PathBuf,
    pub target: PathBuf,
    pub phase: Phase,
    #[serde(default)]
    pub allow_other_wsl: bool,
    #[serde(default)]
    pub disk_bytes: Option<u64>,
}

#[derive(Clone, Copy, Debug, PartialEq, Deserialize, Serialize)]
pub enum Phase {
    Prepared,
    Moving,
    Failed,
    Finishing,
}

pub trait Store {
    fn load(&self) -> Result<Record, String>;
    fn save(&self, record: &Record) -> Result<(), String>;
}

pub struct Registry;
impl Store for Registry {
    fn load(&self) -> Result<Record, String> {
        read_registry(KEY)
    }
    fn save(&self, record: &Record) -> Result<(), String> {
        write_registry(KEY, record)
    }
}

pub struct TestRegistry(pub String);
impl Store for TestRegistry {
    fn load(&self) -> Result<Record, String> {
        read_registry(&format!("{KEY}\\InstallationTests\\{}", self.0))
    }
    fn save(&self, record: &Record) -> Result<(), String> {
        write_registry(&format!("{KEY}\\InstallationTests\\{}", self.0), record)
    }
}

fn read_registry(path: &str) -> Result<Record, String> {
    let root = RegKey::predef(HKEY_CURRENT_USER);
    let key = match root.open_subkey(path) {
        Ok(key) => key,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Record::default()),
        Err(e) => return Err(e.to_string()),
    };
    match key.get_value::<String, _>("Storage") {
        Ok(json) => serde_json::from_str(&json)
            .map_err(|e| format!("数据目录配置无法读取，原有数据未作修改：{e}")),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(Record::default()),
        Err(e) => Err(e.to_string()),
    }
}
fn write_registry(path: &str, record: &Record) -> Result<(), String> {
    let (key, _) = RegKey::predef(HKEY_CURRENT_USER)
        .create_subkey(path)
        .map_err(|e| e.to_string())?;
    // One registry value atomically commits both the location and its journal.
    key.set_value(
        "Storage",
        &serde_json::to_string(record).map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())?;
    let status = unsafe { windows_sys::Win32::System::Registry::RegFlushKey(key.raw_handle()) };
    if status == 0 {
        Ok(())
    } else {
        Err(std::io::Error::from_raw_os_error(status as i32).to_string())
    }
}

pub fn default_dir() -> Result<PathBuf, String> {
    Ok(
        PathBuf::from(std::env::var_os("LOCALAPPDATA").ok_or("本地用户目录不可用")?)
            .join("TrustReproData"),
    )
}

pub fn current(store: &impl Store) -> Result<PathBuf, String> {
    let record = store.load()?;
    if let Some(directory) = record.directory {
        if !directory.is_dir() {
            return Err(format!(
                "数据目录不可用，请检查磁盘连接和访问权限：{}",
                directory.display()
            ));
        }
        Ok(directory)
    } else {
        default_dir()
    }
}

// Compare registered WSL paths lexically. Canonicalizing an AppData parent in an
// MSIX process can resolve Windows metadata redirection instead of the WSL disk.
fn path_key(path: &Path) -> String {
    path.to_string_lossy()
        .trim_start_matches(r"\\?\")
        .trim_end_matches(['\\', '/'])
        .replace('/', "\\")
        .to_lowercase()
}
pub fn same_path(a: &Path, b: &Path) -> bool {
    path_key(a) == path_key(b)
}
fn within(child: &Path, parent: &Path) -> bool {
    let c = path_key(child);
    let p = path_key(parent);
    c == p || c.starts_with(&(p + "\\"))
}

pub fn location(distro: &str) -> Result<Option<PathBuf>, String> {
    let root = RegKey::predef(HKEY_CURRENT_USER);
    let list = match root.open_subkey_with_flags(
        "Software\\Microsoft\\Windows\\CurrentVersion\\Lxss",
        KEY_READ,
    ) {
        Ok(key) => key,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e.to_string()),
    };
    for name in list.enum_keys() {
        let key = list
            .open_subkey(name.map_err(|e| e.to_string())?)
            .map_err(|e| e.to_string())?;
        if key
            .get_value::<String, _>("DistributionName")
            .map_err(|e| e.to_string())?
            == distro
        {
            return key
                .get_value::<String, _>("BasePath")
                .map(PathBuf::from)
                .map(Some)
                .map_err(|e| e.to_string());
        }
    }
    Ok(None)
}

fn no_links(path: &Path) -> Result<(), String> {
    use std::os::windows::fs::MetadataExt;
    for ancestor in path.ancestors() {
        match fs::symlink_metadata(ancestor) {
            Ok(meta) if meta.file_attributes() & 0x400 != 0 => {
                return Err("数据目录不能包含符号链接或目录联接".into())
            }
            Ok(_) => (),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => (),
            Err(e) => return Err(e.to_string()),
        }
    }
    Ok(())
}

pub fn validate_target(source: &Path, target: &Path, install: &Path) -> Result<(), String> {
    let text = target.to_string_lossy();
    // Only ordinary local absolute DOS paths. Reject ADS, devices, UNC, parent
    // traversal and Win32 trailing-dot/space aliases before any filesystem write.
    let bytes = text.as_bytes();
    if bytes.len() < 4
        || !bytes[0].is_ascii_alphabetic()
        || bytes[1] != b':'
        || !matches!(bytes[2], b'\\' | b'/')
        || text[2..].contains(':')
        || target
            .components()
            .any(|c| matches!(c, Component::ParentDir | Component::CurDir))
        || text.split(['\\', '/']).skip(1).any(|c| {
            c == "." || c == ".." || c.ends_with(['.', ' ']) || {
                let stem = c.split('.').next().unwrap_or("").to_ascii_uppercase();
                matches!(stem.as_str(), "CON" | "PRN" | "AUX" | "NUL")
                    || ((stem.starts_with("COM") || stem.starts_with("LPT"))
                        && stem.len() == 4
                        && matches!(stem.as_bytes()[3], b'1'..=b'9'))
            }
        })
        || text.contains(['*', '?', '"', '<', '>', '|'])
    {
        return Err("请选择本机磁盘上的完整目录路径".into());
    }
    if within(target, source) || within(source, target) {
        return Err("新目录不能是当前目录、它的子目录或上级目录".into());
    }
    if within(target, install) || within(install, target) {
        return Err("数据目录不能与程序安装目录重叠".into());
    }
    no_links(target)?;
    no_links(source)?;
    if target.exists()
        && (!target.is_dir()
            || fs::read_dir(target)
                .map_err(|e| e.to_string())?
                .next()
                .is_some())
    {
        return Err("请选择空目录或尚未创建的目录，已有文件不会被覆盖".into());
    }
    Ok(())
}

fn check_space(source: &Path, target: &Path) -> Result<(), String> {
    use std::os::windows::ffi::OsStrExt;
    let root: Vec<u16> = target.to_string_lossy()[..3]
        .encode_utf16()
        .chain(Some(0))
        .collect();
    if !matches!(
        unsafe { windows_sys::Win32::Storage::FileSystem::GetDriveTypeW(root.as_ptr()) },
        2 | 3
    ) {
        return Err("请选择可用的本机磁盘，不能使用网络映射盘".into());
    }
    let existing = target
        .ancestors()
        .find(|p| p.exists())
        .ok_or("目标磁盘不可用")?;
    let wide: Vec<u16> = existing.as_os_str().encode_wide().chain(Some(0)).collect();
    let mut available = 0;
    unsafe {
        if windows_sys::Win32::Storage::FileSystem::GetDiskFreeSpaceExW(
            wide.as_ptr(),
            &mut available,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
        ) == 0
        {
            return Err(format!(
                "无法读取目标磁盘空间：{}",
                std::io::Error::last_os_error()
            ));
        }
    }
    let disk = fs::metadata(source.join("runtime/ext4.vhdx"))
        .map_err(|e| e.to_string())?
        .len();
    if available < disk.saturating_add(256 * 1024 * 1024) {
        return Err("目标磁盘空间不足，需要容纳当前虚拟磁盘并预留 256 MB".into());
    }
    Ok(())
}

pub fn begin(
    store: &impl Store,
    distro: &str,
    source: &Path,
    target: &Path,
    install: &Path,
    allow_other_wsl: bool,
) -> Result<(), String> {
    let mut record = store.load()?;
    if record.pending.is_some() {
        return Err("已有迁移尚未结束，请先恢复该次迁移".into());
    }
    validate_target(source, target, install)?;
    if fs::read_to_string(source.join("runtime-owned")).map_err(|e| e.to_string())? != distro {
        return Err("当前目录不属于此应用，数据未作修改".into());
    }
    let registered = location(distro)?.ok_or("应用运行环境未注册，无法迁移")?;
    if !same_path(&registered, &source.join("runtime")) {
        return Err("运行环境位置与设置不一致，数据未作修改".into());
    }
    // System32's WSL launcher can print its legacy help and return -1 even when
    // the installed Store WSL supports --manage. Its version is authoritative.
    let version = runtime::output(runtime::command("wsl.exe").arg("--version"))?;
    if !supports_move(&version) {
        return Err("当前 WSL 不支持目录迁移，请更新 WSL 后重试".into());
    }
    check_space(source, target)?;
    let migration = Migration {
        id: uuid::Uuid::new_v4().to_string(),
        source: source.into(),
        target: target.into(),
        phase: Phase::Prepared,
        allow_other_wsl,
        disk_bytes: None,
    };
    // Register intent before creating target files or stopping services.
    record.pending = Some(migration.clone());
    store.save(&record)?;
    if let Err(error) = stage_target(&migration) {
        // No runtime command has been dispatched. A known directory-creation
        // failure must leave the settings dialog free to accept another path.
        record.pending = None;
        store.save(&record)?;
        return Err(error);
    }
    Ok(())
}

fn supports_move(version: &str) -> bool {
    version
        .lines()
        .next()
        .and_then(|line| {
            line.split(|c: char| !c.is_ascii_digit() && c != '.')
                .find_map(|word| {
                    let values: Vec<_> = word
                        .split('.')
                        .map(str::parse::<u32>)
                        .collect::<Result<_, _>>()
                        .ok()?;
                    (values.len() >= 3).then(|| (values[0], values[1], values[2]) >= (2, 4, 4))
                })
        })
        .unwrap_or(false)
}

fn stage_target(m: &Migration) -> Result<(), String> {
    no_links(&m.target)?;
    fs::create_dir_all(&m.target).map_err(|e| e.to_string())?;
    let marker = m.target.join(MARKER);
    if marker.exists() {
        if fs::read_to_string(marker).map_err(|e| e.to_string())? != m.id {
            return Err("目标目录属于其他迁移，文件未作修改".into());
        }
    } else {
        if fs::read_dir(&m.target)
            .map_err(|e| e.to_string())?
            .next()
            .is_some()
        {
            return Err("目标目录已出现其他文件，迁移已停止".into());
        }
        use std::io::Write;
        let mut file = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(marker)
            .map_err(|e| e.to_string())?;
        file.write_all(m.id.as_bytes())
            .and_then(|_| file.sync_all())
            .map_err(|e| e.to_string())?;
    }
    grant_user_access(&m.target)
}

fn user_sid() -> Result<String, String> {
    let identity =
        runtime::output(runtime::command("whoami.exe").args(["/user", "/fo", "csv", "/nh"]))?;
    let sid = identity
        .trim()
        .rsplit(',')
        .next()
        .unwrap_or("")
        .trim()
        .trim_matches('"');
    if !sid.starts_with("S-1-") || !sid[4..].chars().all(|c| c.is_ascii_digit() || c == '-') {
        return Err("无法确认当前 Windows 用户".into());
    }
    Ok(sid.to_owned())
}

fn grant_user_access(directory: &Path) -> Result<(), String> {
    no_links(directory)?;
    // The app owns this dedicated data directory. Inheritable rights for the
    // current user keep WRITE_OWNER available after a cross-volume WSL move;
    // older WSL otherwise moves the VHD then fails while restoring its owner.
    // No other account is granted rights and no system policy is changed.
    let sid = user_sid()?;
    runtime::output(
        runtime::command("icacls.exe")
            .arg(directory)
            .arg("/grant:r")
            .arg(format!("*{sid}:(OI)(CI)F"))
            .arg("/Q"),
    )
    .map_err(|error| format!("无法准备数据目录权限，请选择当前用户拥有的目录：{error}"))?;
    Ok(())
}

pub trait Host {
    fn location(&self) -> Result<PathBuf, String>;
    fn stop(&self, migration: &Migration) -> Result<(), String>;
    fn move_disk(&self, target: &Path) -> Result<(), String>;
}
pub struct WslHost<'a> {
    pub distro: &'a str,
    pub release_keepalive: &'a dyn Fn(),
}
impl Host for WslHost<'_> {
    fn location(&self) -> Result<PathBuf, String> {
        location(self.distro)?.ok_or("应用运行环境未注册".into())
    }
    fn stop(&self, m: &Migration) -> Result<(), String> {
        runtime::control_logged(
            self.distro,
            &["stop", &m.id],
            &m.source.join("migration.log"),
        )?;
        grant_user_access(&m.source)?;
        (self.release_keepalive)();
        runtime::output(runtime::command("wsl.exe").args(["--terminate", self.distro]))?;
        // WSL termination can return before its VHD handles are detached. An
        // exclusive open checks release without reading or modifying disk data.
        use std::{
            os::windows::fs::OpenOptionsExt,
            time::{Duration, Instant},
        };
        let deadline = Instant::now() + Duration::from_secs(3);
        loop {
            match fs::OpenOptions::new()
                .read(true)
                .write(true)
                .share_mode(0)
                .open(m.source.join("runtime/ext4.vhdx"))
            {
                Ok(_) => break,
                Err(error)
                    if matches!(error.raw_os_error(), Some(32 | 33))
                        && Instant::now() < deadline =>
                {
                    std::thread::sleep(Duration::from_millis(300))
                }
                Err(error) if matches!(error.raw_os_error(), Some(32 | 33)) => {
                    // WSL can retain distro disks for the lifetime of the shared
                    // VM. Stopping another distro requires explicit consent.
                    let running = runtime::output(runtime::command("wsl.exe").args([
                        "--list",
                        "--running",
                        "--quiet",
                    ]))?;
                    if !may_shutdown_vm(&running, self.distro, m.allow_other_wsl) {
                        return Err("WSL 仍在占用虚拟磁盘。请返回原目录，在迁移窗口勾选允许暂时停止其他 WSL 环境，或重启电脑后重试。其他环境未被停止。".into());
                    }
                    runtime::output(runtime::command("wsl.exe").arg("--shutdown"))?;
                    let release_deadline = Instant::now() + Duration::from_secs(60);
                    loop {
                        match fs::OpenOptions::new()
                            .read(true)
                            .write(true)
                            .share_mode(0)
                            .open(m.source.join("runtime/ext4.vhdx"))
                        {
                            Ok(_) => return Ok(()),
                            Err(e)
                                if matches!(e.raw_os_error(), Some(32 | 33))
                                    && Instant::now() < release_deadline =>
                            {
                                std::thread::sleep(Duration::from_millis(300))
                            }
                            Err(e) => return Err(format!("虚拟磁盘尚未释放，请稍后重试：{e}")),
                        }
                    }
                }
                Err(error) => return Err(format!("虚拟磁盘无法访问：{error}")),
            }
        }
        Ok(())
    }
    fn move_disk(&self, target: &Path) -> Result<(), String> {
        runtime::output(
            runtime::command("wsl.exe")
                .args(["--manage", self.distro, "--move"])
                .arg(target),
        )?;
        Ok(())
    }
}

fn may_shutdown_vm(running: &str, distro: &str, consent: bool) -> bool {
    consent
        || !running
            .lines()
            .any(|s| !s.trim().is_empty() && s.trim() != distro)
}

fn copy_metadata(source: &Path, target: &Path, root: bool) -> Result<(), String> {
    no_links(source)?;
    no_links(target)?;
    fs::create_dir_all(target).map_err(|e| e.to_string())?;
    for entry in fs::read_dir(source).map_err(|e| e.to_string())? {
        let entry = entry.map_err(|e| e.to_string())?;
        // Runtime is moved by WSL. Development test distributions have their own
        // registrations and must never be copied as if they were application data.
        if root
            && matches!(
                entry.file_name().to_str(),
                Some("runtime" | "tests" | MARKER)
            )
        {
            continue;
        }
        let from = entry.path();
        let to = target.join(entry.file_name());
        no_links(&from)?;
        no_links(&to)?;
        if entry.file_type().map_err(|e| e.to_string())?.is_dir() {
            copy_metadata(&from, &to, false)?;
        } else {
            fs::copy(&from, &to).map_err(|e| e.to_string())?;
        }
    }
    Ok(())
}

pub fn recover(
    store: &impl Store,
    host: &impl Host,
    progress: impl Fn(&str),
) -> Result<(), String> {
    let mut record = store.load()?;
    let Some(mut m) = record.pending.clone() else {
        return Ok(());
    };
    let registered = host.location()?;
    let at_source = same_path(&registered, &m.source.join("runtime"));
    let at_target = same_path(&registered, &m.target.join("runtime"));
    if !at_source && !at_target {
        return Err("运行环境位置发生变化，请检查原目录和目标目录，数据未作修改".into());
    }
    if at_source && !matches!(m.phase, Phase::Prepared | Phase::Failed) {
        return Err("上次迁移结果尚未确认，请等待原迁移进程结束并检查 WSL 注册位置。不会重复搬迁或重新创建数据。".into());
    }
    stage_target(&m)?;
    if at_source {
        let target_runtime = m.target.join("runtime");
        if target_runtime.exists() {
            // A known failed WSL move may have created an empty destination.
            // Remove only this owned empty directory, never either disk copy.
            no_links(&target_runtime)?;
            fs::remove_dir(&target_runtime).map_err(|_| {
                "目标目录保留了未完成的迁移文件，请返回原目录并重新选择空目录".to_string()
            })?;
        }
        progress("正在暂停任务并保存数据…");
        host.stop(&m)?;
        // Recheck immediately before dispatch. Never run a second move when the
        // first invocation's result is unknown (e.g. a desktop process crash).
        if !same_path(&host.location()?, &m.source.join("runtime")) {
            return Err("运行环境位置发生变化，请重新打开应用检查迁移结果".into());
        }
        m.phase = Phase::Moving;
        m.disk_bytes = Some(
            fs::metadata(m.source.join("runtime/ext4.vhdx"))
                .map_err(|e| e.to_string())?
                .len(),
        );
        record.pending = Some(m.clone());
        store.save(&record)?;
        progress("正在迁移数据，请保持磁盘连接…");
        if let Err(error) = host.move_disk(&m.target.join("runtime")) {
            if same_path(&host.location()?, &m.source.join("runtime")) {
                m.phase = Phase::Failed;
                record.pending = Some(m.clone());
                store.save(&record)?;
                return Err(format!("迁移未完成，原有数据已保留：{error}"));
            }
            if !same_path(&host.location()?, &m.target.join("runtime")) {
                return Err(error);
            }
        }
    }
    if !same_path(&host.location()?, &m.target.join("runtime"))
        || !m.target.join("runtime/ext4.vhdx").is_file()
    {
        return Err("目标虚拟磁盘尚未确认，原数据目录配置已保留".into());
    }
    m.phase = Phase::Finishing;
    record.pending = Some(m.clone());
    store.save(&record)?;
    progress("正在保存新目录设置…");
    copy_metadata(&m.source, &m.target, true)?;
    record.directory = Some(m.target);
    record.pending = None;
    store.save(&record)
}

pub fn can_cancel(record: &Record, registered: &Path) -> bool {
    record.pending.as_ref().is_some_and(|m| {
        matches!(m.phase, Phase::Prepared | Phase::Failed)
            && same_path(registered, &m.source.join("runtime"))
            && m.source.join("runtime/ext4.vhdx").is_file()
    })
}

pub fn cancel(store: &impl Store, registered: &Path) -> Result<(), String> {
    let mut record = store.load()?;
    if !can_cancel(&record, registered) {
        return Err("迁移结果尚未确认，不能切回原目录".into());
    }
    let source = record.pending.as_ref().unwrap().source.clone();
    // Keep any target files for diagnosis; cancellation never deletes data.
    record.directory = Some(source);
    record.pending = None;
    store.save(&record)
}

/// Per-user, cross-process lock; Windows releases it even after a process crash.
pub struct Lock(windows_sys::Win32::Foundation::HANDLE);
impl Lock {
    pub fn acquire() -> Result<Self, String> {
        use sha2::{Digest, Sha256};
        use windows_sys::Win32::{
            Foundation::{CloseHandle, WAIT_ABANDONED, WAIT_OBJECT_0},
            System::Threading::{CreateMutexW, WaitForSingleObject},
        };
        let suffix = hex::encode(Sha256::digest(default_dir()?.to_string_lossy().as_bytes()));
        let name: Vec<u16> = format!("Local\\TrustReproStorage-{suffix}")
            .encode_utf16()
            .chain(Some(0))
            .collect();
        unsafe {
            let handle = CreateMutexW(std::ptr::null(), 0, name.as_ptr());
            if handle.is_null() {
                return Err(std::io::Error::last_os_error().to_string());
            }
            match WaitForSingleObject(handle, 0) {
                WAIT_OBJECT_0 | WAIT_ABANDONED => Ok(Self(handle)),
                _ => {
                    CloseHandle(handle);
                    Err("应用正在准备、迁移或停止运行环境，请稍后重试".into())
                }
            }
        }
    }
}
impl Drop for Lock {
    fn drop(&mut self) {
        unsafe {
            windows_sys::Win32::System::Threading::ReleaseMutex(self.0);
            windows_sys::Win32::Foundation::CloseHandle(self.0);
        }
    }
}
