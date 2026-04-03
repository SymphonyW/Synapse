import pathlib
import sys


def ensure_package_inits(root: pathlib.Path) -> None:
    # 为生成目录及其子目录补齐 __init__.py，确保可作为 Python 包导入。
    for directory in [root] + [p for p in root.rglob("*") if p.is_dir()]:
        init_file = directory / "__init__.py"
        if not init_file.exists():
            init_file.write_text("", encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/post_gen.py <output_dir>")

    # 如果目标目录不存在则自动创建，再执行初始化文件补齐。
    output_dir = pathlib.Path(sys.argv[1]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    ensure_package_inits(output_dir)
