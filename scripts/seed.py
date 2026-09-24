"""准备 sample-repo：作为独立 git 仓库提交（repo-svc 本地克隆演示用）。"""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT / "fixtures" / "sample-repo"

GIT_ID = ["-c", "user.name=TestForge", "-c", "user.email=testforge@local"]


def run(cmd: list[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True)


def main() -> None:
    gitdir = REPO / ".git"
    if not gitdir.exists():
        run(["git", "init", "-b", "main"], REPO)
    run(["git", "add", "-A"], REPO)
    done = subprocess.run(["git", *GIT_ID, "commit", "-m", "sample-repo baseline"], cwd=REPO, capture_output=True)
    if done.returncode != 0 and b"nothing to commit" not in done.stderr:
        raise SystemExit(done.stderr.decode(errors="ignore"))
    print(f"sample-repo ready at {REPO}")


if __name__ == "__main__":
    main()
