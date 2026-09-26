// Нативная оболочка (Tauri v2) для локального ИИ-агента.
// Логика агента остаётся на Python: оболочка поднимает Python-сервер как дочерний
// процесс, ждёт готовности и открывает окно на его локальном адресе. Так весь
// текущий веб-интерфейс и весь бэкенд (WS + REST) работают без изменений.

mod browser;

use std::process::{Child, Command};
use std::sync::Mutex;
use tauri::image::Image;
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

/// Дочерний Python-процесс, чтобы погасить его при выходе.
struct Backend(Mutex<Option<Child>>);

// Встроенные варианты иконки приложения (тот же «Звёздыш», разные космос-фоны —
// как в Android). Встраиваем в бинарь, чтобы смена работала и в dev, и в проде
// без возни с путями к ресурсам.
const IC_DEFAULT: &[u8] = include_bytes!("../icons/variants/default.png");
const IC_AURORA: &[u8] = include_bytes!("../icons/variants/aurora.png");
const IC_BLUE: &[u8] = include_bytes!("../icons/variants/blue.png");
const IC_EMBER: &[u8] = include_bytes!("../icons/variants/ember.png");
const IC_MILKY: &[u8] = include_bytes!("../icons/variants/milky.png");
const IC_MINIMAL: &[u8] = include_bytes!("../icons/variants/minimal.png");
const IC_ROSE: &[u8] = include_bytes!("../icons/variants/rose.png");
const IC_VIOLET: &[u8] = include_bytes!("../icons/variants/violet.png");

fn variant_bytes(name: &str) -> Option<&'static [u8]> {
    Some(match name {
        "default" => IC_DEFAULT,
        "aurora" => IC_AURORA,
        "blue" => IC_BLUE,
        "ember" => IC_EMBER,
        "milky" => IC_MILKY,
        "minimal" => IC_MINIMAL,
        "rose" => IC_ROSE,
        "violet" => IC_VIOLET,
        _ => return None,
    })
}

/// Меняет иконку окна/таскбара на выбранный вариант (вызывается из UI —
/// «Внешний вид → Иконка приложения»).
#[tauri::command]
fn set_app_icon(window: tauri::WebviewWindow, variant: String) -> Result<(), String> {
    let bytes = variant_bytes(&variant).ok_or_else(|| format!("неизвестная иконка: {variant}"))?;
    let img = Image::from_bytes(bytes).map_err(|e| e.to_string())?;
    window.set_icon(img).map_err(|e| e.to_string())?;
    Ok(())
}

/// Свободный TCP-порт: биндим :0, забираем номер, отпускаем.
fn free_port() -> u16 {
    std::net::TcpListener::bind("127.0.0.1:0")
        .and_then(|l| l.local_addr())
        .map(|a| a.port())
        .unwrap_or(8765)
}

/// Стабильный порт для окна. Origin (а значит и localStorage: тема, акцент,
/// выбор иконки, недавние папки) зависит от порта — со случайным портом всё
/// сбрасывалось бы при каждом запуске. Берём фиксированный, если свободен;
/// иначе (редко, второй экземпляр) — любой свободный.
fn stable_port() -> u16 {
    const FIXED: u16 = 8137;
    match std::net::TcpListener::bind(("127.0.0.1", FIXED)) {
        Ok(listener) => {
            drop(listener); // освобождаем — бэкенд займёт его сам
            FIXED
        }
        Err(_) => free_port(),
    }
}

/// Shown at once while the backend starts: a first cold start of the packaged backend
/// (antivirus scanning a large bundle) can take well over 20 seconds, and an app with no
/// window for that long looks broken — people click it again and start a second copy.
const SPLASH: &str = "data:text/html;charset=utf-8,<html><body style='margin:0;height:100vh;display:flex;\
align-items:center;justify-content:center;background:rgb(11,11,16);color:rgb(230,226,214);\
font-family:Segoe UI,sans-serif'><div style='text-align:center'><div style='font-size:44px;\
color:rgb(232,193,112)'>✦</div><div style='margin-top:14px;font-size:15px;opacity:.8'>\
Altair is starting…</div></div></body></html>";

const ERROR_PAGE: &str = "data:text/html;charset=utf-8,<html><body style='margin:0;padding:40px;\
background:rgb(11,11,16);color:rgb(230,226,214);font-family:Segoe UI,sans-serif'>\
<h2>Altair could not start its backend</h2><p>Close this window and start Altair again. If it \
keeps happening, reinstall the app or check the log in %LOCALAPPDATA%\\LocalAIAgent\\logs.</p>\
<h2 style='margin-top:32px'>Altair не смог запустить бэкенд</h2><p>Закройте окно и запустите \
Altair снова. Если повторяется — переустановите приложение или посмотрите лог в \
%LOCALAPPDATA%\\LocalAIAgent\\logs.</p></body></html>";

/// Waits until the backend listens (up to ~2 min), giving up at once if its process died.
fn wait_ready(app: &tauri::AppHandle, port: u16) -> bool {
    for _ in 0..480 {
        if std::net::TcpStream::connect(("127.0.0.1", port)).is_ok() {
            return true;
        }
        if let Some(backend) = app.try_state::<Backend>() {
            let mut guard = backend.0.lock().unwrap();
            match guard.as_mut().map(|child| child.try_wait()) {
                None => return false,               // never started
                Some(Ok(Some(_))) => return false,  // exited during startup
                _ => {}
            }
        }
        std::thread::sleep(std::time::Duration::from_millis(250));
    }
    false
}

/// Запускает Python-бэкенд. В dev — `python main.py --server` из корня репозитория
/// (путь известен на этапе компиляции). В собранном приложении на его месте будет
/// sidecar-бинарь (см. README оболочки).
fn spawn_backend(port: u16, host: &browser::BrowserHost) -> Option<Child> {
    // The backend exits by itself when this process dies (crash or kill), so it never
    // lingers holding the port — see main.py::_exit_with_parent.
    let parent = std::process::id().to_string();
    // Where the built-in browser lives: the agent drives its tabs over this DevTools
    // port and reads downloads from its quarantine folder.
    let cdp = host.cdp_port.to_string();
    let browser_dir = host.dir.to_string_lossy().into_owned();
    let extra = ["--browser-cdp-port", cdp.as_str(), "--browser-dir", browser_dir.as_str()];
    // desktop/src-tauri -> desktop -> корень репозитория
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(|p| p.parent())
        .map(|p| p.to_path_buf());

    // 1) Собранный sidecar рядом с оболочкой (прод), если есть.
    if let Ok(exe_dir) = std::env::current_exe().map(|p| p.parent().map(|d| d.to_path_buf())) {
        if let Some(dir) = exe_dir {
            let sidecar = dir.join(if cfg!(windows) { "LocalAIAgent.exe" } else { "LocalAIAgent" });
            if sidecar.exists() {
                return Command::new(sidecar)
                    .args(["--server", "--host", "127.0.0.1", "--port", &port.to_string(), "--parent-pid", &parent])
                    .args(extra)
                    .spawn()
                    .ok();
            }
        }
    }

    // 2) Dev: запускаем интерпретатором из корня репозитория.
    let root = root?;
    let py = if cfg!(windows) { "python" } else { "python3" };
    Command::new(py)
        .args(["main.py", "--server", "--host", "127.0.0.1", "--port", &port.to_string(), "--parent-pid", &parent])
        .args(extra)
        .current_dir(&root)
        .spawn()
        .ok()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            set_app_icon,
            browser::browser_info,
            browser::browser_open,
            browser::browser_bounds,
            browser::browser_close,
            browser::browser_navigate,
            browser::browser_history,
            browser::browser_focus
        ])
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }

            let port = stable_port();
            let host = browser::BrowserHost::new(free_port());
            let child = spawn_backend(port, &host);
            app.manage(Backend(Mutex::new(child)));
            app.manage(host);

            // The window opens right away on a splash; the backend is awaited off the
            // main thread. The system frame stays until the app page loads — only the
            // app draws its own title bar and window buttons (static/, initWindowControls),
            // so the splash and the error page must remain movable and closable.
            let window = WebviewWindowBuilder::new(app.handle(), "main", WebviewUrl::External(SPLASH.parse()?))
                .title("Altair")
                .inner_size(1200.0, 820.0)
                .min_inner_size(720.0, 520.0)
                .decorations(true)
                .build()?;

            let handle = app.handle().clone();
            std::thread::spawn(move || {
                if wait_ready(&handle, port) {
                    if let Ok(url) = format!("http://127.0.0.1:{port}/").parse() {
                        let _ = window.set_decorations(false);
                        let _ = window.navigate(url);
                    }
                } else if let Ok(url) = ERROR_PAGE.parse() {
                    let _ = window.navigate(url);
                }
            });

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while running tauri application")
        .run(|app, event| {
            // Гасим Python-процесс при выходе — иначе он переживёт окно.
            if let tauri::RunEvent::ExitRequested { .. } = event {
                if let Some(backend) = app.try_state::<Backend>() {
                    if let Some(mut child) = backend.0.lock().unwrap().take() {
                        let _ = child.kill();
                    }
                }
            }
        });
}
