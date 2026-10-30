fn main() {
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "desktop_status",
            "prepare_runtime",
            "enable_wsl",
            "restart_windows",
            "open_existing",
            "return_to_source",
            "storage_info",
            "choose_data_directory",
            "migrate_data_directory",
        ]),
    ))
    .expect("desktop permissions");
}
