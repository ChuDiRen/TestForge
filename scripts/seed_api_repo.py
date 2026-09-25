"""准备 api-repo（M4 多仓第二仓库）：独立 git 仓库。"""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT / "fixtures" / "api-repo"

GIT_ID = ["-c", "user.name=TestForge", "-c", "user.email=testforge@local"]


def main() -> None:
    gitdir = REPO / ".git"
    if not gitdir.exists():
        run(["git", "init", "-b", "main"], REPO)
    run(["git", "add", "-A"], REPO)
    done = subprocess.run(["git", *GIT_ID, "commit", "-m", "api-repo baseline"], cwd=REPO, capture_output=True)
    out = (done.stdout or b"") + (done.stderr or b"")
    if done.returncode != 0 and b"nothing to commit" not in out:
        raise SystemExit(out.decode(errors="ignore"))
    print(f"api-repo ready at {REPO}")


def run(cmd: list[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True)


if __name__ == "__main__":
    main()
