"""make proto 后处理：把 pb2_grpc 的顶层 import 改写为包内相对路径。"""

import re
from pathlib import Path

GEN = Path(__file__).resolve().parents[1] / "services" / "shared" / "gen"


def main() -> None:
    f = GEN / "testforge_pb2_grpc.py"
    if not f.exists():
        raise SystemExit("testforge_pb2_grpc.py 不存在，先跑 protoc")
    src = f.read_text(encoding="utf-8")
    fixed = re.sub(
        r"^import testforge_pb2 as testforge__pb2$",
        "from services.shared.gen import testforge_pb2 as testforge__pb2",
        src,
        flags=re.M,
    )
    if fixed != src:
        f.write_text(fixed, encoding="utf-8")
        print("fixed import -> services.shared.gen.testforge_pb2")
    else:
        print("import already ok")


if __name__ == "__main__":
    main()
