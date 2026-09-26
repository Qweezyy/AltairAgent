fn main() {
    // App commands must be declared to get ACL permissions ("allow-<command>"): the
    // window shows the backend's http://127.0.0.1 page, a *remote* origin for Tauri,
    // and remote origins may only call commands a capability explicitly allows.
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "set_app_icon",
            "browser_info",
            "browser_open",
            "browser_bounds",
            "browser_close",
            "browser_navigate",
            "browser_history",
            "browser_focus",
        ]),
    ))
    .expect("failed to run tauri-build");
}
