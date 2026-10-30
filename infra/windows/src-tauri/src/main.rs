#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod runtime;
mod setup;
mod storage;
mod supervisor;

use serde::Serialize;
use std::{
    path::PathBuf,
    process::Child,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
};
use storage::Store;
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager, WebviewUrl, WebviewWindowBuilder,
};

#[derive(Clone, Default, Serialize)]
#[serde(rename_all = "camelCase")]
struct Progress {
    message: String,
    error: Option<String>,
    needs_wsl: bool,
    needs_restart: bool,
    can_open_existing: bool,
    can_return_to_source: bool,
    busy: bool,
}

struct Desktop {
    progress: Mutex<Progress>,
    busy: AtomicBool,
    port: Arc<Mutex<Option<u16>>>,
    keepalive: Mutex<Option<Child>>,
    resources: PathBuf,
    data: Mutex<PathBuf>,
}

impl Desktop {
    fn message(&self, message: &str) {
        *self.progress.lock().unwrap() = Progress {
            message: message.to_owned(),
            ..Default::default()
        };
    }
    fn failure(&self, error: String, needs_wsl: bool) {
        *self.progress.lock().unwrap() = Progress {
            message: "准备未完成".into(),
            error: Some(error),
            needs_wsl,
            can_open_existing: storage::Registry
                .load()
                .map(|s| s.pending.is_none())
                .unwrap_or(false)
                && runtime::installed(runtime::DISTRO).unwrap_or(false),
            can_return_to_source: storage::Registry
                .load()
                .ok()
                .zip(storage::location(runtime::DISTRO).ok().flatten())
                .is_some_and(|(record, registered)| storage::can_cancel(&record, &registered)),
            ..Default::default()
        };
    }
}

#[tauri::command]
fn desktop_status(state: tauri::State<'_, Arc<Desktop>>) -> Progress {
    let mut progress = state.progress.lock().unwrap().clone();
    progress.busy = state.busy.load(Ordering::SeqCst);
    progress
}

fn open_workspace(
    app: &tauri::AppHandle,
    state: &Desktop,
    endpoint: runtime::Endpoint,
    settings: bool,
) -> Result<(), String> {
    *state.port.lock().unwrap() = Some(endpoint.port);
    let window = app.get_webview_window("main").ok_or("窗口已关闭")?;
    // A POST sets an HttpOnly cookie without placing the token in URLs, history or logs.
    let action = serde_json::to_string(&format!(
        "http://127.0.0.1:{}/desktop/connect",
        endpoint.port
    ))
    .unwrap();
    let token = serde_json::to_string(&endpoint.token).unwrap();
    let return_to = if settings { "/settings" } else { "/" };
    window.eval(&format!("(()=>{{const f=document.createElement('form');f.method='POST';f.action={action};for(const [name,value] of [['token',{token}],['returnTo','{return_to}']]){{const t=document.createElement('input');t.type='hidden';t.name=name;t.value=value;f.append(t);}}document.body.append(f);f.submit();}})()"))
        .map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
fn prepare_runtime(app: tauri::AppHandle, state: tauri::State<'_, Arc<Desktop>>) {
    if state.busy.swap(true, Ordering::SeqCst) {
        return;
    }
    let state = state.inner().clone();
    std::thread::spawn(move || {
        prepare_workspace(&app, &state);
        state.busy.store(false, Ordering::SeqCst);
    });
}

// Called with busy held, including directly after WSL setup without a reboot.
fn prepare_workspace(app: &tauri::AppHandle, state: &Desktop) {
    let result = (|| {
        let _lock = storage::Lock::acquire()?;
        let recovering = storage::Registry.load()?.pending.is_some();
        storage::recover(
            &storage::Registry,
            &storage::WslHost {
                distro: runtime::DISTRO,
                release_keepalive: &|| release_keepalive(&state),
            },
            |m| state.message(m),
        )?;
        let data = storage::current(&storage::Registry)?;
        *state.data.lock().unwrap() = data.clone();
        let endpoint = supervisor::prepare(&state.resources, &data, runtime::DISTRO, |m| {
            state.message(m)
        })?;
        Ok::<_, String>((endpoint, recovering))
    })();
    match result {
        Ok((endpoint, recovering)) => {
            if state.keepalive.lock().unwrap().is_none() {
                *state.keepalive.lock().unwrap() = runtime::keepalive(runtime::DISTRO).ok();
            }
            if let Err(error) = open_workspace(&app, &state, endpoint, recovering) {
                state.failure(error, false);
            }
        }
        Err(error) => {
            let needs_wsl = runtime::output(runtime::command("wsl.exe").arg("--status")).is_err()
                || error.contains("HYPERV")
                || error.contains("0x80370102")
                || error.contains("WSL_E");
            let error = if needs_wsl {
                format!(
                    "运行环境尚未就绪，请准备系统组件。\n{}",
                    setup::guidance(&error)
                )
            } else {
                error
            };
            state.failure(error, needs_wsl);
        }
    }
}

#[tauri::command]
fn enable_wsl(app: tauri::AppHandle, state: tauri::State<'_, Arc<Desktop>>) {
    if state.busy.swap(true, Ordering::SeqCst) {
        return;
    }
    let state = state.inner().clone();
    std::thread::spawn(move || {
        state.message("正在准备系统组件…");
        match runtime::prepare_wsl() {
            Ok(setup::Outcome::Ready) => prepare_workspace(&app, &state),
            Ok(setup::Outcome::RestartRequired) => {
                *state.progress.lock().unwrap() = Progress {
                    message: "系统组件已准备，请重启电脑".into(),
                    needs_restart: true,
                    ..Default::default()
                }
            }
            Err(error) => state.failure(error, true),
        }
        state.busy.store(false, Ordering::SeqCst);
    });
}

#[tauri::command]
fn restart_windows(state: tauri::State<'_, Arc<Desktop>>) -> Result<(), String> {
    if state.busy.load(Ordering::SeqCst) || !state.progress.lock().unwrap().needs_restart {
        return Err("当前无需重启电脑".into());
    }
    runtime::output(runtime::command("shutdown.exe").args(["/r", "/t", "0"]))?;
    Ok(())
}

#[tauri::command]
fn return_to_source(
    app: tauri::AppHandle,
    state: tauri::State<'_, Arc<Desktop>>,
) -> Result<(), String> {
    if state.busy.load(Ordering::SeqCst) {
        return Err("操作尚未结束".into());
    }
    {
        let _lock = storage::Lock::acquire()?;
        let registered = storage::location(runtime::DISTRO)?.ok_or("运行环境位置不可用")?;
        storage::cancel(&storage::Registry, &registered)?;
    }
    prepare_runtime(app, state);
    Ok(())
}

#[tauri::command]
fn open_existing(
    app: tauri::AppHandle,
    state: tauri::State<'_, Arc<Desktop>>,
) -> Result<(), String> {
    let _lock = storage::Lock::acquire()?;
    if storage::Registry.load()?.pending.is_some() {
        return Err("请先完成数据迁移".into());
    }
    let endpoint = runtime::endpoint(runtime::DISTRO)?;
    runtime::ready(&endpoint)?;
    open_workspace(&app, &state, endpoint, false)
}

fn release_keepalive(state: &Desktop) {
    if let Some(mut child) = state.keepalive.lock().unwrap().take() {
        let _ = child.kill();
        let _ = child.wait();
    }
}

fn settings_caller(window: &tauri::WebviewWindow, state: &Desktop) -> Result<(), String> {
    let url = window.url().map_err(|e| e.to_string())?;
    if window.label() != "main" || !is_settings_url(&url, *state.port.lock().unwrap()) {
        return Err("此操作仅可在桌面应用设置中使用".into());
    }
    Ok(())
}

fn is_settings_url(url: &tauri::Url, port: Option<u16>) -> bool {
    port.is_some()
        && url.scheme() == "http"
        && url.host_str() == Some("127.0.0.1")
        && url.port() == port
        && url.path() == "/settings"
}

#[tauri::command]
fn storage_info(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<Desktop>>,
) -> Result<String, String> {
    settings_caller(&window, &state)?;
    Ok(storage::current(&storage::Registry)?
        .to_string_lossy()
        .into_owned())
}

#[tauri::command]
async fn choose_data_directory(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<Desktop>>,
) -> Result<Option<String>, String> {
    settings_caller(&window, &state)?;
    let directory = storage::current(&storage::Registry)?;
    tauri::async_runtime::spawn_blocking(move || {
        rfd::FileDialog::new()
            .set_title("选择新的空数据目录")
            .set_directory(directory.parent().unwrap_or(&directory))
            .pick_folder()
            .map(|p| p.to_string_lossy().into_owned())
    })
    .await
    .map_err(|e| e.to_string())
}

#[tauri::command]
fn migrate_data_directory(
    app: tauri::AppHandle,
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<Desktop>>,
    directory: String,
) -> Result<(), String> {
    settings_caller(&window, &state)?;
    let target = PathBuf::from(directory.trim());
    let source = storage::current(&storage::Registry)?;
    let executable = std::env::current_exe().map_err(|e| e.to_string())?;
    storage::validate_target(
        &source,
        &target,
        executable.parent().ok_or("程序目录不可用")?,
    )?;
    if state.busy.swap(true, Ordering::SeqCst) {
        return Err("已有操作正在进行，请稍后重试".into());
    }
    // Validation and the durable intent happen before navigating away, so invalid
    // destinations leave the settings form and the running service available.
    let planned = (|| {
        let _lock = storage::Lock::acquire()?;
        storage::begin(
            &storage::Registry,
            runtime::DISTRO,
            &source,
            &target,
            executable.parent().unwrap(),
            true,
        )
    })();
    if let Err(error) = planned {
        state.busy.store(false, Ordering::SeqCst);
        return Err(error);
    }
    state.message("正在准备迁移数据…");
    if let Err(error) = window.navigate(
        "http://tauri.localhost/index.html?manual=1"
            .parse()
            .unwrap(),
    ) {
        state.busy.store(false, Ordering::SeqCst);
        return Err(error.to_string());
    }
    let state = state.inner().clone();
    std::thread::spawn(move || {
        let result = (|| {
            let _lock = storage::Lock::acquire()?;
            storage::recover(
                &storage::Registry,
                &storage::WslHost {
                    distro: runtime::DISTRO,
                    release_keepalive: &|| release_keepalive(&state),
                },
                |m| state.message(m),
            )?;
            let data = storage::current(&storage::Registry)?;
            *state.data.lock().unwrap() = data.clone();
            supervisor::prepare(&state.resources, &data, runtime::DISTRO, |m| {
                state.message(m)
            })
        })();
        match result {
            Ok(endpoint) => {
                *state.keepalive.lock().unwrap() = runtime::keepalive(runtime::DISTRO).ok();
                if let Err(error) = open_workspace(&app, &state, endpoint, true) {
                    state.failure(error, false);
                }
            }
            Err(error) => state.failure(error, false),
        }
        state.busy.store(false, Ordering::SeqCst);
    });
    Ok(())
}

fn show(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

fn quit(app: tauri::AppHandle) {
    let state = app.state::<Arc<Desktop>>().inner().clone();
    if state.busy.swap(true, Ordering::SeqCst) {
        return;
    }
    std::thread::spawn(move || {
        state.message("正在暂停任务并退出…");
        let data = state.data.lock().unwrap().clone();
        let result = (|| {
            let _lock = storage::Lock::acquire()?;
            if storage::Registry.load()?.pending.is_some() {
                return Err("请先恢复数据迁移，当前数据已保留".into());
            }
            if !data.join("runtime-owned").exists() {
                // No workspace has ever been opened, so no user work can be active.
                Ok(())
            } else {
                match runtime::installed(runtime::DISTRO) {
                    Ok(true) => runtime::control_logged(
                        runtime::DISTRO,
                        &["stop", &uuid::Uuid::new_v4().to_string()],
                        &data.join("shutdown.log"),
                    ),
                    Ok(false) => Ok(()),
                    Err(error) if data.join("runtime").exists() => Err(error),
                    Err(_) => Ok(()),
                }
            }
        })();
        match result {
            Ok(()) => {
                if let Some(mut child) = state.keepalive.lock().unwrap().take() {
                    let _ = child.kill();
                    let _ = child.wait();
                }
                app.exit(0);
            }
            Err(error) => {
                state.failure(error, false);
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.navigate(
                        "http://tauri.localhost/index.html?manual=1"
                            .parse()
                            .unwrap(),
                    );
                }
                show(&app);
                state.busy.store(false, Ordering::SeqCst);
            }
        }
    });
}

fn cli(args: &[String]) -> Option<Result<(), String>> {
    if let Some(index) = args.iter().position(|s| s == "--storage-check") {
        return Some((|| {
            let root = PathBuf::from(args.get(index + 1).ok_or("Bundle directory required")?);
            let distro = args
                .get(index + 2)
                .ok_or("Dedicated test distro required")?;
            let target = PathBuf::from(args.get(index + 3).ok_or("Empty target required")?);
            if !distro.starts_with("TrustRepro-InstallTest-")
                || !distro
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || c == '-')
            {
                return Err("Only dedicated installation test distributions are accepted".into());
            }
            let _lock = storage::Lock::acquire()?;
            let store = storage::TestRegistry(distro.clone());
            let mut record = store.load()?;
            if record.directory.is_none() {
                let registered =
                    storage::location(distro)?.ok_or("Test distro is not registered")?;
                record.directory = Some(
                    registered
                        .parent()
                        .ok_or("Test data directory missing")?
                        .into(),
                );
                store.save(&record)?;
            }
            if record.pending.is_none() {
                let source = storage::current(&store)?;
                storage::begin(
                    &store,
                    distro,
                    &source,
                    &target,
                    &std::env::current_exe().map_err(|e| e.to_string())?,
                    args.iter().any(|s| s == "--allow-other-wsl"),
                )?;
            }
            storage::recover(
                &store,
                &storage::WslHost {
                    distro,
                    release_keepalive: &|| (),
                },
                |s| eprintln!("{s}"),
            )?;
            let data = storage::current(&store)?;
            let endpoint = supervisor::prepare(&root, &data, distro, |s| eprintln!("{s}"))?;
            println!(
                "{}",
                serde_json::json!({"ready": true, "directory": data, "status": runtime::ready(&endpoint)?})
            );
            Ok(())
        })());
    }
    if args
        .iter()
        .any(|s| s == "--prepare-update" || s == "--prepare-uninstall")
    {
        return Some((|| {
            let _lock = storage::Lock::acquire()?;
            if storage::Registry.load()?.pending.is_some() {
                return Err("数据迁移尚未结束，请打开应用完成迁移后再更新或卸载".into());
            }
            if !storage::current(&storage::Registry)?
                .join("runtime-owned")
                .exists()
            {
                return Ok(());
            }
            if !runtime::installed(runtime::DISTRO)? {
                return Ok(());
            }
            if args.iter().any(|s| s == "--prepare-update") {
                runtime::wsl(runtime::DISTRO, &["idle"])?;
            }
            runtime::wsl(
                runtime::DISTRO,
                &["stop", &uuid::Uuid::new_v4().to_string()],
            )?;
            Ok(())
        })());
    }
    if let Some(index) = args.iter().position(|s| s == "--runtime-check") {
        return Some((|| {
            let root = PathBuf::from(args.get(index + 1).ok_or("Bundle directory required")?);
            let distro = args
                .get(index + 2)
                .ok_or("Dedicated test distro required")?;
            if !distro.starts_with("TrustRepro-InstallTest-")
                || !distro
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || c == '-')
            {
                return Err("Test distro must start with TrustRepro-InstallTest-".into());
            }
            let store = storage::TestRegistry(distro.clone());
            let record = store.load()?;
            let data = if record.directory.is_some() {
                storage::current(&store)?
            } else {
                storage::default_dir()?.join("tests").join(distro)
            };
            if record.pending.is_some() {
                return Err("Test migration must be recovered with --storage-check".into());
            }
            let endpoint = supervisor::prepare(&root, &data, distro, |s| eprintln!("{s}"))?;
            let status = runtime::ready(&endpoint)?;
            println!(
                "{}",
                serde_json::json!({"ready": true, "administrator": runtime::is_administrator()?, "status": status})
            );
            Ok(())
        })());
    }
    None
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if let Some(result) = cli(&args) {
        if let Err(error) = result {
            eprintln!("{error}");
            std::process::exit(1);
        }
        return;
    }
    // Elevate before opening WebView or touching per-user runtime state. CLI
    // maintenance remains callable by NSIS and inherits the installer's token.
    match runtime::ensure_administrator() {
        Ok(true) => (),
        Ok(false) => return,
        Err(error) => {
            rfd::MessageDialog::new()
                .set_title("TrustRepro 未启动")
                .set_description(&error)
                .set_level(rfd::MessageLevel::Error)
                .show();
            std::process::exit(1);
        }
    }
    let port = Arc::new(Mutex::new(None::<u16>));
    let nav_port = port.clone();
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| show(app)))
        .invoke_handler(tauri::generate_handler![
            desktop_status,
            prepare_runtime,
            enable_wsl,
            restart_windows,
            open_existing,
            return_to_source,
            storage_info,
            choose_data_directory,
            migrate_data_directory
        ])
        .setup(move |app| {
            let data = storage::default_dir()?;
            app.manage(Arc::new(Desktop {
                progress: Mutex::new(Progress::default()),
                busy: AtomicBool::new(false),
                port: port.clone(),
                keepalive: Mutex::new(None),
                resources: app.path().resource_dir()?.join("runtime"),
                data: Mutex::new(data),
            }));
            let window =
                WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                    .title("TrustRepro")
                    .inner_size(1380., 900.)
                    .min_inner_size(1240., 720.)
                    .on_navigation(move |url| {
                        let internal = (url.scheme() == "http"
                            && url.host_str() == Some("tauri.localhost"))
                            || (url.scheme() == "tauri" && url.host_str() == Some("localhost"))
                            || (url.scheme() == "http"
                                && url.host_str() == Some("127.0.0.1")
                                && url.port() == *nav_port.lock().unwrap());
                        if !internal {
                            runtime::open_external(url.as_str());
                        }
                        internal
                    })
                    .on_new_window(|url, _| {
                        runtime::open_external(url.as_str());
                        tauri::webview::NewWindowResponse::Deny
                    })
                    .on_download(|_, event| {
                        if let tauri::webview::DownloadEvent::Requested { destination, .. } = event
                        {
                            if let Some(path) = rfd::FileDialog::new()
                                .set_file_name(
                                    destination
                                        .file_name()
                                        .unwrap_or_default()
                                        .to_string_lossy(),
                                )
                                .save_file()
                            {
                                *destination = path;
                            } else {
                                return false;
                            }
                        }
                        true
                    })
                    .build()?;
            window.on_window_event({
                let window = window.clone();
                move |event| {
                    if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                        api.prevent_close();
                        let _ = window.hide();
                    }
                }
            });
            let open = MenuItem::with_id(app, "open", "打开 TrustRepro", true, None::<&str>)?;
            let exit = MenuItem::with_id(app, "quit", "暂停任务并退出", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open, &exit])?;
            TrayIconBuilder::new()
                .icon(app.default_window_icon().unwrap().clone())
                .menu(&menu)
                .tooltip("TrustRepro")
                .on_menu_event(|app, event| match event.id().as_ref() {
                    "open" => show(app),
                    "quit" => quit(app.clone()),
                    _ => (),
                })
                .build(app)?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("TrustRepro desktop startup failed");
}
