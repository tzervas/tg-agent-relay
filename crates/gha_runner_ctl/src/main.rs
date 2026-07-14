//! One GitHub Actions self-hosted runner controller (Podman).
//!
//! - Listens for demand (queued self-hosted jobs) and spins **one** runner up.
//! - Spins it down when idle.
//! - Registers with a short-lived GitHub registration token (never logged).
//! - Uses a pre-built image + seeded volume snapshot for near-zero cold start.
//!
//! This is intentionally single-runner — not a fleet manager.

use clap::{Parser, Subcommand, ValueEnum};
use serde::Deserialize;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

const DEFAULT_REPO: &str = "tzervas/tg-agent-relay";
const DEFAULT_IMAGE: &str = "localhost/tg-agent-relay-gha-runner:latest";
const DEFAULT_CONTAINER: &str = "gha-runner-tg-agent-relay";
const DEFAULT_VOLUME: &str = "tg-agent-relay-gha-runner-data";
const DEFAULT_LABELS: &str = "self-hosted,linux,x64,podman";
const DEFAULT_NAME: &str = "tg-agent-relay-podman";

#[derive(Debug, Clone, ValueEnum)]
enum Mode {
    /// Re-register each spin-up (`config.sh --ephemeral`); container exits after one job.
    Ephemeral,
    /// Keep `.runner` on the snapshot volume; start/stop only.
    Retain,
}

#[derive(Debug, Parser)]
#[command(
    name = "gha-runner-ctl",
    about = "Single self-hosted GHA runner: prepare snapshot, listen, up/down"
)]
struct Cli {
    #[command(subcommand)]
    cmd: Cmd,

    /// owner/repo
    #[arg(long, env = "GHA_REPO", default_value = DEFAULT_REPO, global = true)]
    repo: String,

    /// Podman image name
    #[arg(long, env = "GHA_IMAGE", default_value = DEFAULT_IMAGE, global = true)]
    image: String,

    /// Single container name (only one runner)
    #[arg(long, env = "GHA_CONTAINER", default_value = DEFAULT_CONTAINER, global = true)]
    container: String,

    /// Snapshot volume (runner home)
    #[arg(long, env = "GHA_VOLUME", default_value = DEFAULT_VOLUME, global = true)]
    volume: String,

    /// Runner name registered with GitHub
    #[arg(long, env = "GHA_RUNNER_NAME", default_value = DEFAULT_NAME, global = true)]
    runner_name: String,

    /// Comma-separated runner labels
    #[arg(long, env = "GHA_LABELS", default_value = DEFAULT_LABELS, global = true)]
    labels: String,

    /// CPUs for the container
    #[arg(long, env = "GHA_CPUS", default_value = "5", global = true)]
    cpus: String,

    /// Memory limit (podman --memory)
    #[arg(long, env = "GHA_MEMORY", default_value = "8g", global = true)]
    memory: String,

    /// Path to Containerfile directory (for prepare)
    #[arg(long, env = "GHA_BUILD_DIR", global = true)]
    build_dir: Option<PathBuf>,

    /// Registration mode
    #[arg(long, env = "GHA_MODE", value_enum, default_value_t = Mode::Ephemeral, global = true)]
    mode: Mode,
}

#[derive(Debug, Subcommand)]
enum Cmd {
    /// Build image + seed volume snapshot (do this once / after toolchain bumps)
    Prepare {
        /// Also create a stopped container from the snapshot for fastest start
        #[arg(long, default_value_t = true)]
        with_container: bool,
    },
    /// Register (if needed) and start the one runner
    Up,
    /// Stop the runner container (and remove registration when ephemeral)
    Down {
        /// Remove container after stop
        #[arg(long, default_value_t = true)]
        rm: bool,
    },
    /// Show container + GitHub runner status
    Status,
    /// Poll GitHub for queued self-hosted jobs; up on demand, down when idle
    Listen {
        /// Seconds between polls
        #[arg(long, default_value_t = 15)]
        interval: u64,
        /// Seconds with no active/queued jobs before spinning down
        #[arg(long, default_value_t = 120)]
        idle_secs: u64,
        /// Also listen on 127.0.0.1:PORT for POST /wake and POST /sleep
        #[arg(long, env = "GHA_WAKE_PORT")]
        wake_port: Option<u16>,
    },
}

fn main() {
    if let Err(e) = run() {
        eprintln!("gha-runner-ctl: {e}");
        std::process::exit(1);
    }
}

fn run() -> Result<(), String> {
    let cli = Cli::parse();
    match &cli.cmd {
        Cmd::Prepare { with_container } => prepare(&cli, *with_container),
        Cmd::Up => up(&cli),
        Cmd::Down { rm } => down(&cli, *rm),
        Cmd::Status => status(&cli),
        Cmd::Listen {
            interval,
            idle_secs,
            wake_port,
        } => listen(&cli, *interval, *idle_secs, *wake_port),
    }
}

// --- GitHub auth (never print token material) ---------------------------------

fn github_token() -> Result<String, String> {
    if let Ok(t) = std::env::var("GH_TOKEN") {
        if !t.is_empty() {
            return Ok(t);
        }
    }
    if let Ok(t) = std::env::var("GITHUB_TOKEN") {
        if !t.is_empty() {
            return Ok(t);
        }
    }
    // Prefer gh's stored credentials over a long-lived env export.
    let out = Command::new("gh")
        .args(["auth", "token"])
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .output()
        .map_err(|e| format!("gh auth token failed: {e}"))?;
    if !out.status.success() {
        return Err(
            "no GH_TOKEN/GITHUB_TOKEN and `gh auth token` failed — authenticate with gh or set a fine-grained PAT (Administration: read/write on this repo)".into(),
        );
    }
    let t = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if t.is_empty() {
        return Err("empty token from gh auth token".into());
    }
    Ok(t)
}

fn redacted_ok() {
    // Tokens must never appear in logs; call sites use this as a reminder.
}

#[derive(Deserialize)]
struct RegistrationTokenResponse {
    token: String,
}

fn registration_token(repo: &str, api_token: &str) -> Result<String, String> {
    redacted_ok();
    let url = format!("https://api.github.com/repos/{repo}/actions/runners/registration-token");
    let resp = ureq::post(&url)
        .set("Authorization", &format!("Bearer {api_token}"))
        .set("Accept", "application/vnd.github+json")
        .set("User-Agent", "tg-agent-relay-gha-runner-ctl")
        .set("X-GitHub-Api-Version", "2022-11-28")
        .call()
        .map_err(|e| format!("registration-token request failed: {e}"))?;
    let body: RegistrationTokenResponse = resp
        .into_json()
        .map_err(|e| format!("registration-token parse failed: {e}"))?;
    if body.token.is_empty() {
        return Err("empty registration token".into());
    }
    Ok(body.token)
}

// --- Podman helpers ----------------------------------------------------------

fn podman(args: &[&str]) -> Result<String, String> {
    let out = Command::new("podman")
        .args(args)
        .output()
        .map_err(|e| format!("podman not runnable: {e}"))?;
    if !out.status.success() {
        let err = String::from_utf8_lossy(&out.stderr);
        let stdout = String::from_utf8_lossy(&out.stdout);
        return Err(format!(
            "podman {} failed: {}{}",
            args.join(" "),
            err.trim(),
            if stdout.trim().is_empty() {
                String::new()
            } else {
                format!(" ({})", stdout.trim())
            }
        ));
    }
    Ok(String::from_utf8_lossy(&out.stdout).trim().to_string())
}

fn podman_ok(args: &[&str]) -> bool {
    podman(args).is_ok()
}

fn container_running(name: &str) -> bool {
    podman(&["inspect", "-f", "{{.State.Running}}", name]).is_ok_and(|s| s == "true")
}

fn container_exists(name: &str) -> bool {
    podman_ok(&["container", "exists", name])
}

fn volume_exists(name: &str) -> bool {
    podman_ok(&["volume", "exists", name])
}

fn resolve_build_dir(cli: &Cli) -> Result<PathBuf, String> {
    if let Some(p) = &cli.build_dir {
        return Ok(p.clone());
    }
    // crates/gha_runner_ctl -> repo/scripts/self-hosted-runner
    let here = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let candidate = here
        .join("../../scripts/self-hosted-runner")
        .canonicalize()
        .map_err(|e| format!("resolve build dir: {e}"))?;
    if !candidate.join("Containerfile").is_file() {
        return Err(format!(
            "Containerfile not found under {} — pass --build-dir",
            candidate.display()
        ));
    }
    Ok(candidate)
}

// --- Snapshot prepare --------------------------------------------------------

#[allow(clippy::unnecessary_wraps)]
fn prepare(cli: &Cli, with_container: bool) -> Result<(), String> {
    let dir = resolve_build_dir(cli)?;
    eprintln!("prepare: building image {} from {}", cli.image, dir.display());
    podman(&[
        "build",
        "-t",
        &cli.image,
        "-f",
        "Containerfile",
        dir.to_str().unwrap_or("."),
    ])?;

    if !volume_exists(&cli.volume) {
        eprintln!("prepare: creating volume {}", cli.volume);
        podman(&["volume", "create", &cli.volume])?;
    }

    // Seed volume from image seed dir (runner binaries) without starting a live runner.
    eprintln!("prepare: seeding volume snapshot (runner binaries only)…");
    podman(&[
        "run",
        "--rm",
        "--entrypoint",
        "/bin/bash",
        "-v",
        &format!("{}:/opt/actions-runner:Z", cli.volume),
        &cli.image,
        "-c",
        r"
set -euo pipefail
if [[ ! -x /opt/actions-runner/run.sh ]]; then
  cp -a /opt/actions-runner-seed/. /opt/actions-runner/
fi
# Marker so we know snapshot is warm
date -u +%Y-%m-%dT%H:%M:%SZ > /opt/actions-runner/.snapshot-baseline
echo ok
",
    ])?;

    if with_container {
        // Snapshot is the volume; `up` does `podman run` with a fresh short-lived
        // registration token. No pre-created container (would embed stale env).
        eprintln!(
            "prepare: volume snapshot ready (container created on demand by `up`, cpus={} memory={})",
            cli.cpus, cli.memory
        );
    }

    eprintln!("prepare: snapshot ready — near-zero start via `gha-runner-ctl up`");
    Ok(())
}

// --- Up / down ---------------------------------------------------------------

fn write_env_file(path: &Path, reg_token: &str, cli: &Cli) -> Result<(), String> {
    let ephemeral = matches!(cli.mode, Mode::Ephemeral);
    let mut f = fs::File::create(path).map_err(|e| format!("env file: {e}"))?;
    // Restrictive mode best-effort
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = fs::set_permissions(path, fs::Permissions::from_mode(0o600));
    }
    writeln!(
        f,
        "REPO_URL=https://github.com/{}\nRUNNER_NAME={}\nRUNNER_LABELS={}\nRUNNER_EPHEMERAL={}\nRUNNER_RETAIN={}\nRUNNER_TOKEN={}",
        cli.repo,
        cli.runner_name,
        cli.labels,
        if ephemeral { "true" } else { "false" },
        if ephemeral { "false" } else { "true" },
        reg_token
    )
    .map_err(|e| format!("env write: {e}"))?;
    Ok(())
}

fn up(cli: &Cli) -> Result<(), String> {
    if container_running(&cli.container) {
        eprintln!("up: already running ({})", cli.container);
        return Ok(());
    }
    if !volume_exists(&cli.volume) {
        return Err(format!(
            "volume {} missing — run `gha-runner-ctl prepare` first",
            cli.volume
        ));
    }

    let api = github_token()?;
    let reg = registration_token(&cli.repo, &api)?;
    let env_path = std::env::temp_dir().join(format!("gha-runner-{}-env", std::process::id()));
    write_env_file(&env_path, &reg, cli)?;

    // Always recreate container so env (registration token) is fresh and not
    // visible in `podman inspect` history of an old create.
    if container_exists(&cli.container) {
        let _ = podman(&["rm", "-f", &cli.container]);
    }

    eprintln!(
        "up: starting one runner ({:?} mode, snapshot volume={})",
        cli.mode, cli.volume
    );
    let ephemeral = matches!(cli.mode, Mode::Ephemeral);
    podman(&[
        "run",
        "-d",
        "--name",
        &cli.container,
        "--cpus",
        &cli.cpus,
        "--memory",
        &cli.memory,
        "--memory-swap",
        &cli.memory,
        "--pids-limit",
        "4096",
        "--env-file",
        env_path.to_str().unwrap_or("/dev/null"),
        "-e",
        &format!(
            "RUNNER_EPHEMERAL={}",
            if ephemeral { "true" } else { "false" }
        ),
        "-e",
        &format!(
            "RUNNER_RETAIN={}",
            if ephemeral { "false" } else { "true" }
        ),
        "-v",
        &format!("{}:/opt/actions-runner:Z", cli.volume),
        &cli.image,
    ])?;

    // Best-effort shred of env file (token material)
    let _ = fs::remove_file(&env_path);
    eprintln!("up: container {}", cli.container);
    Ok(())
}

#[allow(clippy::unnecessary_wraps)]
fn down(cli: &Cli, rm: bool) -> Result<(), String> {
    if container_exists(&cli.container) {
        eprintln!("down: stopping {}", cli.container);
        let _ = podman(&["stop", "-t", "30", &cli.container]);
        if rm {
            let _ = podman(&["rm", "-f", &cli.container]);
        }
    } else {
        eprintln!("down: no container {}", cli.container);
    }
    // Ephemeral runners self-remove from GitHub when the process exits cleanly.
    // Retain mode keeps .runner on the volume for the next start.
    if matches!(cli.mode, Mode::Ephemeral) {
        // Drop local registration leftovers so next up re-registers cleanly.
        let _ = podman(&[
            "run",
            "--rm",
            "--entrypoint",
            "/bin/bash",
            "-v",
            &format!("{}:/opt/actions-runner:Z", cli.volume),
            &cli.image,
            "-c",
            "rm -f /opt/actions-runner/.runner /opt/actions-runner/.credentials /opt/actions-runner/.credentials_rsaparams 2>/dev/null; true",
        ]);
    }
    Ok(())
}

#[allow(clippy::unnecessary_wraps)]
fn status(cli: &Cli) -> Result<(), String> {
    println!("container: {}", cli.container);
    if container_exists(&cli.container) {
        let running = container_running(&cli.container);
        println!("  exists: true");
        println!("  running: {running}");
    } else {
        println!("  exists: false");
    }
    println!("volume: {} (exists={})", cli.volume, volume_exists(&cli.volume));
    println!("image: {}", cli.image);
    println!("mode: {:?}", cli.mode);

    if let Ok(api) = github_token() {
        let url = format!("https://api.github.com/repos/{}/actions/runners", cli.repo);
        if let Ok(resp) = ureq::get(&url)
            .set("Authorization", &format!("Bearer {api}"))
            .set("Accept", "application/vnd.github+json")
            .set("User-Agent", "tg-agent-relay-gha-runner-ctl")
            .call()
        {
            #[derive(Deserialize)]
            struct Runners {
                runners: Vec<Runner>,
            }
            #[derive(Deserialize)]
            struct Runner {
                name: String,
                status: String,
                busy: bool,
            }
            if let Ok(body) = resp.into_json::<Runners>() {
                println!("github runners:");
                for r in body.runners {
                    println!("  - {} status={} busy={}", r.name, r.status, r.busy);
                }
            }
        }
    }
    Ok(())
}

// --- Demand detection + listen loop ------------------------------------------

#[derive(Debug, Deserialize)]
struct WorkflowRuns {
    workflow_runs: Vec<WorkflowRun>,
}

#[derive(Debug, Deserialize)]
struct WorkflowRun {
    id: u64,
}

#[derive(Debug, Deserialize)]
struct JobsResp {
    jobs: Vec<Job>,
}

#[derive(Debug, Deserialize)]
struct Job {
    status: String,
    labels: Vec<String>,
}

fn queued_or_active_self_hosted(repo: &str, api: &str) -> Result<(bool, bool), String> {
    // queued: any run in queued/in_progress with a job requesting self-hosted
    let url = format!("https://api.github.com/repos/{repo}/actions/runs?status=queued&per_page=10");
    let mut need = false;
    let mut active = false;

    let runs = fetch_runs(&url, api)?;
    for run in &runs {
        if job_wants_self_hosted(repo, run.id, api)? {
            need = true;
            break;
        }
    }

    let url_ip = format!(
        "https://api.github.com/repos/{repo}/actions/runs?status=in_progress&per_page=10"
    );
    let runs_ip = fetch_runs(&url_ip, api)?;
    for run in &runs_ip {
        if job_wants_self_hosted(repo, run.id, api)? {
            active = true;
            // in_progress still needs a runner if not busy yet
            need = true;
            break;
        }
    }

    Ok((need, active))
}

fn fetch_runs(url: &str, api: &str) -> Result<Vec<WorkflowRun>, String> {
    let resp = ureq::get(url)
        .set("Authorization", &format!("Bearer {api}"))
        .set("Accept", "application/vnd.github+json")
        .set("User-Agent", "tg-agent-relay-gha-runner-ctl")
        .set("X-GitHub-Api-Version", "2022-11-28")
        .call()
        .map_err(|e| format!("list runs: {e}"))?;
    let body: WorkflowRuns = resp
        .into_json()
        .map_err(|e| format!("parse runs: {e}"))?;
    Ok(body.workflow_runs)
}

fn job_wants_self_hosted(repo: &str, run_id: u64, api: &str) -> Result<bool, String> {
    let url = format!("https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs");
    let resp = ureq::get(&url)
        .set("Authorization", &format!("Bearer {api}"))
        .set("Accept", "application/vnd.github+json")
        .set("User-Agent", "tg-agent-relay-gha-runner-ctl")
        .set("X-GitHub-Api-Version", "2022-11-28")
        .call()
        .map_err(|e| format!("list jobs: {e}"))?;
    let body: JobsResp = resp
        .into_json()
        .map_err(|e| format!("parse jobs: {e}"))?;
    for j in body.jobs {
        if j.status == "completed" {
            continue;
        }
        let labels = j.labels.join(",").to_ascii_lowercase();
        if labels.contains("self-hosted") || labels.contains("podman") {
            return Ok(true);
        }
    }
    Ok(false)
}

fn listen(
    cli: &Cli,
    interval: u64,
    idle_secs: u64,
    wake_port: Option<u16>,
) -> Result<(), String> {
    eprintln!(
        "listen: one runner, poll every {interval}s, idle_down={idle_secs}s, mode={:?}",
        cli.mode
    );
    if !volume_exists(&cli.volume) {
        eprintln!("listen: snapshot missing — running prepare…");
        prepare(cli, true)?;
    }

    // Optional local wake socket (for manual/on-LAN poke without waiting for poll).
    if let Some(port) = wake_port {
        let cli_clone = cli_snapshot(cli);
        thread::spawn(move || wake_server(port, cli_clone));
        eprintln!("listen: wake server on 127.0.0.1:{port}  (POST /wake | POST /sleep)");
    }

    let mut idle_since: Option<Instant> = None;
    loop {
        let api = match github_token() {
            Ok(t) => t,
            Err(e) => {
                eprintln!("listen: auth: {e}");
                thread::sleep(Duration::from_secs(interval));
                continue;
            }
        };

        let (need, _active) = match queued_or_active_self_hosted(&cli.repo, &api) {
            Ok(v) => v,
            Err(e) => {
                eprintln!("listen: poll: {e}");
                thread::sleep(Duration::from_secs(interval));
                continue;
            }
        };

        let running = container_running(&cli.container);

        if need && !running {
            eprintln!("listen: demand detected — up");
            if let Err(e) = up(cli) {
                eprintln!("listen: up failed: {e}");
            }
            idle_since = None;
        } else if !need && running {
            let since = idle_since.get_or_insert_with(Instant::now);
            if since.elapsed() >= Duration::from_secs(idle_secs) {
                eprintln!("listen: idle {idle_secs}s — down");
                if let Err(e) = down(cli, true) {
                    eprintln!("listen: down failed: {e}");
                }
                idle_since = None;
            }
        } else if need && running {
            idle_since = None;
        } else {
            // !need && !running
            idle_since = None;
        }

        thread::sleep(Duration::from_secs(interval));
    }
}

/// Minimal clone of settings for the wake thread.
struct CliSnap {
    repo: String,
    image: String,
    container: String,
    volume: String,
    runner_name: String,
    labels: String,
    cpus: String,
    memory: String,
    mode: Mode,
}

fn cli_snapshot(cli: &Cli) -> CliSnap {
    CliSnap {
        repo: cli.repo.clone(),
        image: cli.image.clone(),
        container: cli.container.clone(),
        volume: cli.volume.clone(),
        runner_name: cli.runner_name.clone(),
        labels: cli.labels.clone(),
        cpus: cli.cpus.clone(),
        memory: cli.memory.clone(),
        mode: cli.mode.clone(),
    }
}

fn snap_to_cli(s: &CliSnap) -> Cli {
    Cli {
        cmd: Cmd::Status, // unused
        repo: s.repo.clone(),
        image: s.image.clone(),
        container: s.container.clone(),
        volume: s.volume.clone(),
        runner_name: s.runner_name.clone(),
        labels: s.labels.clone(),
        cpus: s.cpus.clone(),
        memory: s.memory.clone(),
        build_dir: None,
        mode: s.mode.clone(),
    }
}

fn wake_server(port: u16, snap: CliSnap) {
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::sync::Arc;

    let snap = Arc::new(snap);
    let bind = format!("127.0.0.1:{port}");
    let listener = match TcpListener::bind(&bind) {
        Ok(l) => l,
        Err(e) => {
            eprintln!("wake: bind {bind}: {e}");
            return;
        }
    };
    for stream in listener.incoming().flatten() {
        let mut s = stream;
        let mut buf = [0_u8; 1024];
        let n = s.read(&mut buf).unwrap_or(0);
        let req = String::from_utf8_lossy(&buf[..n]);
        let cli = snap_to_cli(&snap);
        let (code, body) = if req.starts_with("POST /wake") {
            match up(&cli) {
                Ok(()) => ("200 OK", "up\n"),
                Err(e) => {
                    eprintln!("wake: {e}");
                    ("500", "error\n")
                }
            }
        } else if req.starts_with("POST /sleep") {
            match down(&cli, true) {
                Ok(()) => ("200 OK", "down\n"),
                Err(e) => {
                    eprintln!("sleep: {e}");
                    ("500", "error\n")
                }
            }
        } else if req.starts_with("GET /health") {
            ("200 OK", "ok\n")
        } else {
            ("404", "use POST /wake or POST /sleep\n")
        };
        let _ = write!(
            s,
            "HTTP/1.1 {code}\r\nContent-Type: text/plain\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
            body.len()
        );
    }
}
