//! WSL setup decisions, shared by the native adapter and isolated tests.
#[derive(Debug, PartialEq)]
pub enum Outcome {
    Ready,
    RestartRequired,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Step {
    Install,
    Update,
    Status,
}

impl Step {
    pub fn args(self) -> &'static [&'static str] {
        match self {
            Self::Install => &["--install", "--no-distribution", "--web-download"],
            Self::Update => &["--update", "--web-download"],
            Self::Status => &["--status"],
        }
    }
}

pub struct CommandResult {
    pub code: Option<i32>,
    pub detail: String,
}

impl CommandResult {
    fn reboot_required(&self) -> bool {
        matches!(self.code.map(|c| c as u32), Some(3010 | 0x80070bc2))
            || self.detail.contains("WSL_E_REBOOT_REQUIRED")
    }

    fn check(&self, step: Step) -> Result<(), String> {
        if self.code == Some(0) {
            return Ok(());
        }
        let action = match step {
            Step::Install => "系统组件安装未完成",
            Step::Update => "系统组件更新未完成",
            Step::Status => "系统组件尚不可用",
        };
        Err(format!(
            "{action}（退出码 {:?}）\n{}",
            self.code,
            guidance(&self.detail)
        ))
    }
}

pub trait Host {
    fn run(&mut self, step: Step) -> Result<CommandResult, String>;
    fn restart_pending(&mut self) -> Result<bool, String>;
    fn schedule_resume(&mut self) -> Result<(), String>;
}

pub fn prepare(host: &mut impl Host) -> Result<Outcome, String> {
    for step in [Step::Install, Step::Update] {
        let result = host.run(step)?;
        if result.reboot_required() {
            return restart(host);
        }
        result.check(step)?;
        // wsl --install can return zero while Windows features are EnablePending.
        // Do not infer this from localized console text or discard an update error.
        if host.restart_pending()? {
            return restart(host);
        }
    }
    let status = host.run(Step::Status)?;
    if status.reboot_required() {
        return restart(host);
    }
    status.check(Step::Status)?;
    Ok(Outcome::Ready)
}

fn restart(host: &mut impl Host) -> Result<Outcome, String> {
    host.schedule_resume()
        .map_err(|e| format!("系统组件需要重启，但未能保存自动接续设置。请重试。\n{e}"))?;
    Ok(Outcome::RestartRequired)
}

pub fn guidance(detail: &str) -> String {
    let upper = detail.to_ascii_uppercase();
    if upper.contains("0X80370102") || upper.contains("HYPERV_NOT_INSTALLED") {
        format!("请启用 Windows“虚拟机平台”和 BIOS/UEFI 硬件虚拟化，然后重启电脑。\n{detail}")
    } else {
        detail.to_owned()
    }
}
