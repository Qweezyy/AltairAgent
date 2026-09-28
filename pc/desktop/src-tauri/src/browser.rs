// Built-in browser: real Chromium (WebView2) tabs living inside the app window.
//
// Like the browser pane of Claude Desktop, pages are not streamed as pictures: each tab
// is a native WebView2 control placed over the browser panel of the UI, so it renders
// sharply and reacts to the user instantly. The controls are created straight through
// WebView2 — not as Tauri webviews — so pages see a clean Edge: no Tauri IPC objects
// (window.__TAURI_INTERNALS__, window.ipc) that a Tauri webview always carries, and no
// way to reach the app. The browser has its own persistent profile (logins survive
// restarts) and no automation flags; the agent drives the same tabs over the DevTools
// protocol on a local port.
//
// The UI owns the tab strip and tells us where the panel is; we create, place, show and
// close the controls and report what happens in them as "altair-browser" events.

use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, Manager};

/// Shared by every tab: one WebView2 environment = one browser with one profile.
pub struct BrowserHost {
    pub cdp_port: u16,
    /// The backend's network proxy for the browser (core/browser_net.py): per site it goes
    /// straight to the internet or through the user's VPN.
    pub net_port: u16,
    pub dir: PathBuf,
}

impl BrowserHost {
    pub fn new(cdp_port: u16, net_port: u16) -> Self {
        let base = std::env::var_os("LOCALAPPDATA")
            .map(PathBuf::from)
            .unwrap_or_else(std::env::temp_dir);
        Self { cdp_port, net_port, dir: base.join("LocalAIAgent").join("browser") }
    }

    /// A PAC script instead of a fixed --proxy-server: "; DIRECT" keeps pages loading when
    /// the backend's proxy is down, and local/LAN addresses never go through it.
    fn pac_url(&self) -> String {
        let script = format!(
            "function FindProxyForURL(url, host) {{ if (isPlainHostName(host) || host == 'localhost' || \
             shExpMatch(host, '127.*') || shExpMatch(host, '10.*') || shExpMatch(host, '192.168.*') || \
             shExpMatch(host, '169.254.*') || shExpMatch(host, '172.1[6-9].*') || \
             shExpMatch(host, '172.2[0-9].*') || shExpMatch(host, '172.3[01].*') || host == '[::1]') \
             return 'DIRECT'; return 'PROXY 127.0.0.1:{}; DIRECT'; }}",
            self.net_port
        );
        let mut out = String::from("data:application/x-ns-proxy-autoconfig,");
        for b in script.bytes() {
            if b.is_ascii_alphanumeric() || b"-_.~".contains(&b) {
                out.push(b as char);
            } else {
                out.push_str(&format!("%{:02X}", b));
            }
        }
        out
    }

    fn profile_dir(&self) -> PathBuf {
        self.dir.join("profile")
    }

    pub fn quarantine_dir(&self) -> PathBuf {
        self.dir.join("quarantine")
    }

    fn browser_args(&self) -> String {
        // Without the mini-menu/PDF overlays wry also drops, but WITH SmartScreen: its
        // malicious-site and download checks are worth keeping for arbitrary pages.
        // AutomationControlled off: Chromium otherwise sets navigator.webdriver once a
        // debugging port is open, which anti-bot checks read as "robot".
        format!(
            "--disable-features=msWebOOUI,msPdfOOUI --disable-blink-features=AutomationControlled \
             --remote-debugging-port={} --remote-allow-origins=http://127.0.0.1:{} --proxy-pac-url={}",
            self.cdp_port, self.cdp_port, self.pac_url()
        )
    }
}

#[derive(Deserialize, Clone, Copy)]
pub struct Bounds {
    x: f64,
    y: f64,
    w: f64,
    h: f64,
}

#[derive(Serialize, Clone, Default)]
struct BrowserEvent {
    tab: String,
    kind: &'static str,
    #[serde(skip_serializing_if = "String::is_empty")]
    url: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    title: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    path: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    target_id: String,
    ok: bool,
}

fn emit(app: &AppHandle, event: BrowserEvent) {
    let _ = app.emit_to("main", "altair-browser", event);
}

fn check_tab(tab: &str) -> Result<(), String> {
    if tab.is_empty() || tab.len() > 40 || !tab.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_') {
        return Err(format!("bad tab id: {tab}"));
    }
    Ok(())
}

/// Web pages, local files and about:blank — never app-internal or custom schemes.
/// file:// is the user's own choice here; the agent's file:// access is limited to the
/// workspace by the backend before it asks for a tab.
fn check_url(url: &str) -> Result<String, String> {
    let url = if url.is_empty() { "about:blank" } else { url };
    let parsed: tauri::Url = url.parse().map_err(|e| format!("bad url: {e}"))?;
    match parsed.scheme() {
        "http" | "https" | "file" => Ok(parsed.to_string()),
        "about" if parsed.as_str() == "about:blank" => Ok(parsed.to_string()),
        other => Err(format!("scheme not allowed: {other}")),
    }
}

static DOWNLOAD_SEQ: AtomicU64 = AtomicU64::new(0);

/// A fresh folder per download keeps the original file name without collisions.
fn quarantine_target(dir: &std::path::Path, proposed: &str) -> PathBuf {
    let stamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);
    let seq = DOWNLOAD_SEQ.fetch_add(1, Ordering::Relaxed);
    let name = std::path::Path::new(proposed)
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .filter(|n| !n.is_empty())
        .unwrap_or_else(|| "download".into());
    let folder = dir.join(format!("{stamp}-{seq}"));
    let _ = std::fs::create_dir_all(&folder);
    folder.join(name)
}

/// Runs `f` on the UI thread (WebView2 controls belong to it) and waits for the result.
fn on_ui<R: Send + 'static>(app: &AppHandle, f: impl FnOnce(&AppHandle) -> Result<R, String> + Send + 'static) -> Result<R, String> {
    let (tx, rx) = std::sync::mpsc::channel();
    let handle = app.clone();
    app.run_on_main_thread(move || {
        let _ = tx.send(f(&handle));
    })
    .map_err(|e| e.to_string())?;
    rx.recv().map_err(|e| e.to_string())?
}

#[cfg(windows)]
mod native {
    use super::*;
    use std::cell::RefCell;
    use std::collections::HashMap;
    use std::sync::mpsc;

    use webview2_com::Microsoft::Web::WebView2::Win32::*;
    use webview2_com::{
        take_pwstr, wait_with_pump, CallDevToolsProtocolMethodCompletedHandler, ContentLoadingEventHandler,
        CoreWebView2EnvironmentOptions, CreateCoreWebView2ControllerCompletedHandler,
        CreateCoreWebView2EnvironmentCompletedHandler, DocumentTitleChangedEventHandler, DownloadStartingEventHandler,
        NavigationCompletedEventHandler, NewWindowRequestedEventHandler, SourceChangedEventHandler,
        StateChangedEventHandler,
    };
    use windows::core::{w, Interface, BOOL, HSTRING, PCWSTR, PWSTR};
    use windows::Win32::Foundation::{E_POINTER, HWND, LPARAM, LRESULT, RECT, WPARAM};
    use windows::Win32::System::LibraryLoader::GetModuleHandleW;
    use windows::Win32::UI::WindowsAndMessaging::{
        CreateWindowExW, DefWindowProcW, DestroyWindow, FindWindowExW, RegisterClassW, SetWindowPos, HWND_TOP,
        SWP_HIDEWINDOW, SWP_NOACTIVATE, SWP_SHOWWINDOW, WINDOW_EX_STYLE, WNDCLASSW, WS_CHILD, WS_CLIPCHILDREN,
        WS_CLIPSIBLINGS,
    };

    /// Each tab lives in its own child window of the app window. The container is what
    /// we move, show, hide and — crucially — keep ABOVE the app's own webview (a bare
    /// WebView2 control would sit below it and stay invisible), yet below Tauri's
    /// resize-border window so the app can still be resized from its edges.
    struct TabView {
        container: HWND,
        controller: ICoreWebView2Controller,
        webview: ICoreWebView2,
    }

    const HOST_CLASS: PCWSTR = w!("AltairBrowserTab");

    unsafe extern "system" fn host_proc(hwnd: HWND, msg: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
        DefWindowProcW(hwnd, msg, wparam, lparam)
    }

    fn register_class() {
        thread_local! { static DONE: RefCell<bool> = const { RefCell::new(false) }; }
        DONE.with(|done| {
            if *done.borrow() {
                return;
            }
            unsafe {
                let class = WNDCLASSW {
                    lpfnWndProc: Some(host_proc),
                    hInstance: GetModuleHandleW(None).map(Into::into).unwrap_or_default(),
                    lpszClassName: HOST_CLASS,
                    ..Default::default()
                };
                RegisterClassW(&class);
            }
            *done.borrow_mut() = true;
        });
    }

    /// Where the tab window goes in the z-order: right under Tauri's resize borders.
    fn insert_after(parent: HWND) -> HWND {
        unsafe {
            FindWindowExW(Some(parent), None, w!("TAURI_DRAG_RESIZE_BORDERS"), PCWSTR::null())
                .unwrap_or(HWND_TOP)
        }
    }

    fn place(parent: HWND, container: HWND, r: RECT, visible: bool) {
        let flags = SWP_NOACTIVATE | if visible { SWP_SHOWWINDOW } else { SWP_HIDEWINDOW };
        unsafe {
            let _ = SetWindowPos(container, Some(insert_after(parent)), r.left, r.top, r.right - r.left, r.bottom - r.top, flags);
        }
    }

    fn inner(r: RECT) -> RECT {
        RECT { left: 0, top: 0, right: r.right - r.left, bottom: r.bottom - r.top }
    }

    thread_local! {
        static ENV: RefCell<Option<ICoreWebView2Environment>> = const { RefCell::new(None) };
        static TABS: RefCell<HashMap<String, TabView>> = RefCell::new(HashMap::new());
    }

    fn err(e: impl std::fmt::Debug) -> String {
        format!("{e:?}")
    }

    fn environment(host: &BrowserHost) -> Result<ICoreWebView2Environment, String> {
        if let Some(env) = ENV.with(|e| e.borrow().clone()) {
            return Ok(env);
        }
        let (tx, rx) = mpsc::channel();
        let options = CoreWebView2EnvironmentOptions::default();
        unsafe {
            options.set_additional_browser_arguments(host.browser_args());
            CreateCoreWebView2EnvironmentWithOptions(
                PCWSTR::null(),
                &HSTRING::from(host.profile_dir().as_os_str()),
                &ICoreWebView2EnvironmentOptions::from(options),
                &CreateCoreWebView2EnvironmentCompletedHandler::create(Box::new(move |hr, env| {
                    let result = (|| {
                        hr?;
                        env.ok_or_else(|| windows::core::Error::from(E_POINTER))
                    })();
                    let _ = tx.send(result);
                    Ok(())
                })),
            )
            .map_err(err)?;
        }
        let env = wait_with_pump(rx).map_err(err)?.map_err(err)?;
        ENV.with(|e| *e.borrow_mut() = Some(env.clone()));
        Ok(env)
    }

    fn rect(bounds: Bounds, scale: f64) -> RECT {
        RECT {
            left: (bounds.x * scale).round() as i32,
            top: (bounds.y * scale).round() as i32,
            right: ((bounds.x + bounds.w.max(1.0)) * scale).round() as i32,
            bottom: ((bounds.y + bounds.h.max(1.0)) * scale).round() as i32,
        }
    }

    fn source(webview: &ICoreWebView2) -> String {
        let mut uri = PWSTR::null();
        unsafe {
            if webview.Source(&mut uri).is_ok() {
                return take_pwstr(uri);
            }
        }
        String::new()
    }

    pub fn open(app: &AppHandle, tab: &str, url: &str, bounds: Bounds, visible: bool) -> Result<(), String> {
        if TABS.with(|t| t.borrow().contains_key(tab)) {
            return Ok(());
        }
        let window = app.get_webview_window("main").ok_or("main window is gone")?;
        let parent: HWND = window.hwnd().map_err(err)?;
        let scale = window.scale_factor().map_err(err)?;
        let host = app.state::<BrowserHost>();
        let env = environment(&host)?;

        register_class();
        let area = rect(bounds, scale);
        let container = unsafe {
            CreateWindowExW(
                WINDOW_EX_STYLE::default(),
                HOST_CLASS,
                PCWSTR::null(),
                WS_CHILD | WS_CLIPCHILDREN | WS_CLIPSIBLINGS,
                area.left,
                area.top,
                area.right - area.left,
                area.bottom - area.top,
                Some(parent),
                None,
                GetModuleHandleW(None).ok().map(Into::into),
                None,
            )
            .map_err(err)?
        };
        place(parent, container, area, visible);

        let (tx, rx) = mpsc::channel();
        unsafe {
            env.CreateCoreWebView2Controller(
                container,
                &CreateCoreWebView2ControllerCompletedHandler::create(Box::new(move |hr, controller| {
                    let result = (|| {
                        hr?;
                        controller.ok_or_else(|| windows::core::Error::from(E_POINTER))
                    })();
                    let _ = tx.send(result);
                    Ok(())
                })),
            )
            .map_err(err)?;
        }
        let controller: ICoreWebView2Controller = match wait_with_pump(rx).map_err(err).and_then(|r| r.map_err(err)) {
            Ok(controller) => controller,
            Err(e) => {
                unsafe { let _ = DestroyWindow(container); }
                return Err(e);
            }
        };
        let webview = unsafe { controller.CoreWebView2().map_err(err)? };
        unsafe {
            controller.SetBounds(inner(area)).map_err(err)?;
            controller.SetIsVisible(true).map_err(err)?; // the container decides visibility
        }
        wire_events(app, tab, &webview, host.quarantine_dir()).map_err(err)?;
        report_target_id(app, tab, &webview);
        unsafe {
            webview.Navigate(&HSTRING::from(url)).map_err(err)?;
        }
        TABS.with(|t| t.borrow_mut().insert(tab.to_string(), TabView { container, controller, webview }));
        Ok(())
    }

    fn wire_events(app: &AppHandle, tab: &str, webview: &ICoreWebView2, quarantine: PathBuf) -> windows::core::Result<()> {
        let mut token = Default::default();
        unsafe {
            let (a, t) = (app.clone(), tab.to_string());
            webview.add_DocumentTitleChanged(
                &DocumentTitleChangedEventHandler::create(Box::new(move |wv, _| {
                    if let Some(wv) = wv {
                        let mut title = PWSTR::null();
                        wv.DocumentTitle(&mut title)?;
                        emit(&a, BrowserEvent { tab: t.clone(), kind: "title", title: take_pwstr(title), ok: true, ..Default::default() });
                    }
                    Ok(())
                })),
                &mut token,
            )?;
            let (a, t) = (app.clone(), tab.to_string());
            webview.add_ContentLoading(
                &ContentLoadingEventHandler::create(Box::new(move |wv, _| {
                    if let Some(wv) = wv {
                        emit(&a, BrowserEvent { tab: t.clone(), kind: "loading", url: source(&wv), ok: true, ..Default::default() });
                    }
                    Ok(())
                })),
                &mut token,
            )?;
            let (a, t) = (app.clone(), tab.to_string());
            webview.add_NavigationCompleted(
                &NavigationCompletedEventHandler::create(Box::new(move |wv, _| {
                    if let Some(wv) = wv {
                        emit(&a, BrowserEvent { tab: t.clone(), kind: "loaded", url: source(&wv), ok: true, ..Default::default() });
                    }
                    Ok(())
                })),
                &mut token,
            )?;
            // Single-page apps change the address without a navigation.
            let (a, t) = (app.clone(), tab.to_string());
            webview.add_SourceChanged(
                &SourceChangedEventHandler::create(Box::new(move |wv, _| {
                    if let Some(wv) = wv {
                        emit(&a, BrowserEvent { tab: t.clone(), kind: "loaded", url: source(&wv), ok: true, ..Default::default() });
                    }
                    Ok(())
                })),
                &mut token,
            )?;
            let (a, t) = (app.clone(), tab.to_string());
            webview.add_NewWindowRequested(
                &NewWindowRequestedEventHandler::create(Box::new(move |_, args| {
                    let Some(args) = args else { return Ok(()) };
                    // A sized popup is usually a sign-in window talking back to its opener
                    // (OAuth): it opens as a small window, as in any browser. Ordinary
                    // "open in new tab" links become tabs in the panel instead.
                    let mut sized = BOOL::from(false);
                    if let Ok(features) = args.WindowFeatures() {
                        let _ = features.HasSize(&mut sized);
                    }
                    if sized.as_bool() {
                        return args.SetHandled(false);
                    }
                    let mut uri = PWSTR::null();
                    args.Uri(&mut uri)?;
                    emit(&a, BrowserEvent { tab: t.clone(), kind: "new_tab", url: take_pwstr(uri), ok: true, ..Default::default() });
                    args.SetHandled(true)
                })),
                &mut token,
            )?;
            if let Ok(webview4) = webview.cast::<ICoreWebView2_4>() {
                let (a, t) = (app.clone(), tab.to_string());
                webview4.add_DownloadStarting(
                    &DownloadStartingEventHandler::create(Box::new(move |_, args| {
                        let Some(args) = args else { return Ok(()) };
                        // Never straight into the user's folders: quarantine first; the
                        // backend checks the file before it may go anywhere else.
                        let mut proposed = PWSTR::null();
                        args.ResultFilePath(&mut proposed)?;
                        let target = quarantine_target(&quarantine, &take_pwstr(proposed));
                        args.SetResultFilePath(&HSTRING::from(target.as_os_str()))?;
                        args.SetHandled(true)?; // no Edge download flyout; the panel shows it
                        let operation = args.DownloadOperation()?;
                        let mut uri = PWSTR::null();
                        operation.Uri(&mut uri)?;
                        let url = take_pwstr(uri);
                        let path = target.to_string_lossy().into_owned();
                        emit(&a, BrowserEvent { tab: t.clone(), kind: "download_started", url: url.clone(), path: path.clone(), ok: true, ..Default::default() });
                        let (a2, t2) = (a.clone(), t.clone());
                        operation.add_StateChanged(
                            &StateChangedEventHandler::create(Box::new(move |op, _| {
                                let Some(op) = op else { return Ok(()) };
                                let mut state = COREWEBVIEW2_DOWNLOAD_STATE::default();
                                op.State(&mut state)?;
                                if state != COREWEBVIEW2_DOWNLOAD_STATE_IN_PROGRESS {
                                    emit(&a2, BrowserEvent {
                                        tab: t2.clone(), kind: "download_finished", url: url.clone(), path: path.clone(),
                                        ok: state == COREWEBVIEW2_DOWNLOAD_STATE_COMPLETED, ..Default::default()
                                    });
                                }
                                Ok(())
                            })),
                            &mut Default::default(),
                        )?;
                        Ok(())
                    })),
                    &mut token,
                )?;
            }
        }
        Ok(())
    }

    /// Reports the tab's DevTools target id, so the agent knows which page is which tab
    /// without anything visible to the page itself.
    fn report_target_id(app: &AppHandle, tab: &str, webview: &ICoreWebView2) {
        let (app, tab) = (app.clone(), tab.to_string());
        unsafe {
            let handler = CallDevToolsProtocolMethodCompletedHandler::create(Box::new(move |_hr, json: String| {
                let target_id = serde_json::from_str::<serde_json::Value>(&json)
                    .ok()
                    .and_then(|v| v["targetInfo"]["targetId"].as_str().map(str::to_string))
                    .unwrap_or_default();
                emit(&app, BrowserEvent { tab, kind: "target", target_id, ok: true, ..Default::default() });
                Ok(())
            }));
            let _ = webview.CallDevToolsProtocolMethod(windows::core::w!("Target.getTargetInfo"), windows::core::w!("{}"), &handler);
        }
    }

    fn with_tab<R>(tab: &str, f: impl FnOnce(&TabView) -> windows::core::Result<R>) -> Result<R, String> {
        TABS.with(|t| {
            let tabs = t.borrow();
            let view = tabs.get(tab).ok_or("no such tab")?;
            f(view).map_err(err)
        })
    }

    pub fn bounds(app: &AppHandle, tab: &str, bounds: Bounds, visible: bool) -> Result<(), String> {
        let window = app.get_webview_window("main").ok_or("main window is gone")?;
        let parent: HWND = window.hwnd().map_err(err)?;
        let area = rect(bounds, window.scale_factor().map_err(err)?);
        with_tab(tab, |v| unsafe {
            place(parent, v.container, area, visible);
            // A hidden tab is sized too: the agent keeps reading, clicking and screenshotting
            // it with the panel closed, and a page without a size renders nothing.
            v.controller.SetBounds(inner(area))?;
            if visible {
                v.controller.NotifyParentWindowPositionChanged()?;
            }
            Ok(())
        })
    }

    pub fn close(tab: &str) -> Result<(), String> {
        let view = TABS.with(|t| t.borrow_mut().remove(tab));
        if let Some(view) = view {
            unsafe {
                let _ = view.controller.Close();
                let _ = DestroyWindow(view.container);
            }
        }
        Ok(())
    }

    pub fn navigate(tab: &str, url: &str) -> Result<(), String> {
        with_tab(tab, |v| unsafe { v.webview.Navigate(&HSTRING::from(url)) })
    }

    pub fn history(tab: &str, op: &str) -> Result<(), String> {
        with_tab(tab, |v| unsafe {
            match op {
                "back" => v.webview.GoBack(),
                "forward" => v.webview.GoForward(),
                "reload" => v.webview.Reload(),
                _ => v.webview.Stop(),
            }
        })
    }

    pub fn focus(tab: &str) -> Result<(), String> {
        with_tab(tab, |v| unsafe { v.controller.MoveFocus(COREWEBVIEW2_MOVE_FOCUS_REASON_PROGRAMMATIC) })
    }
}

#[cfg(not(windows))]
mod native {
    use super::*;
    const NO: &str = "the built-in browser is Windows-only for now";
    pub fn open(_: &AppHandle, _: &str, _: &str, _: Bounds, _: bool) -> Result<(), String> { Err(NO.into()) }
    pub fn bounds(_: &AppHandle, _: &str, _: Bounds, _: bool) -> Result<(), String> { Err(NO.into()) }
    pub fn close(_: &str) -> Result<(), String> { Err(NO.into()) }
    pub fn navigate(_: &str, _: &str) -> Result<(), String> { Err(NO.into()) }
    pub fn history(_: &str, _: &str) -> Result<(), String> { Err(NO.into()) }
    pub fn focus(_: &str) -> Result<(), String> { Err(NO.into()) }
}

#[tauri::command]
pub fn browser_info(host: tauri::State<'_, BrowserHost>) -> serde_json::Value {
    serde_json::json!({ "cdpPort": host.cdp_port, "netPort": host.net_port, "dir": host.dir, "quarantine": host.quarantine_dir() })
}

#[tauri::command]
pub async fn browser_open(app: AppHandle, tab: String, url: String, bounds: Bounds, visible: bool) -> Result<(), String> {
    check_tab(&tab)?;
    let url = check_url(&url)?;
    on_ui(&app, move |app| native::open(app, &tab, &url, bounds, visible))
}

/// Places the tab over the panel (or hides it). Called on every layout change.
#[tauri::command]
pub async fn browser_bounds(app: AppHandle, tab: String, bounds: Bounds, visible: bool) -> Result<(), String> {
    check_tab(&tab)?;
    on_ui(&app, move |app| native::bounds(app, &tab, bounds, visible))
}

#[tauri::command]
pub async fn browser_close(app: AppHandle, tab: String) -> Result<(), String> {
    check_tab(&tab)?;
    on_ui(&app, move |_| native::close(&tab))
}

#[tauri::command]
pub async fn browser_navigate(app: AppHandle, tab: String, url: String) -> Result<(), String> {
    check_tab(&tab)?;
    let url = check_url(&url)?;
    on_ui(&app, move |_| native::navigate(&tab, &url))
}

/// Browser buttons: a fixed set of operations, never arbitrary script.
#[tauri::command]
pub async fn browser_history(app: AppHandle, tab: String, op: String) -> Result<(), String> {
    check_tab(&tab)?;
    if !matches!(op.as_str(), "back" | "forward" | "reload" | "stop") {
        return Err(format!("unknown op: {op}"));
    }
    on_ui(&app, move |_| native::history(&tab, &op))
}

#[tauri::command]
pub async fn browser_focus(app: AppHandle, tab: String) -> Result<(), String> {
    check_tab(&tab)?;
    on_ui(&app, move |_| native::focus(&tab))
}
