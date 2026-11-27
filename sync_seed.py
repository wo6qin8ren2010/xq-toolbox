"""打包前把本机自建库同步进 app/seed/自建库.json。

用法（在项目根目录）：
    ..\\.venv\\Scripts\\python.exe sync_seed.py

之后按 README 的打包命令出包，别人更新到新版本时内置条目会自动并入/更新到他们本地。
"""
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SEED = os.path.join(ROOT, "app", "seed", "自建库.json")
LOCAL = os.path.join(os.environ.get("LOCALAPPDATA", ""), "MixerToolsRebuild", "自建库.json")


def main() -> int:
    if not os.path.isfile(LOCAL):
        print("找不到本机自建库：%s" % LOCAL)
        return 1
    with open(LOCAL, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        print("本机自建库结构异常")
        return 1
    software = len(data.get("software") or [])
    projects = len(data.get("projects") or [])
    os.makedirs(os.path.dirname(SEED), exist_ok=True)
    if os.path.isfile(SEED):
        try:
            shutil.copyfile(SEED, SEED + ".bak")
        except Exception:
            pass
    with open(SEED, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    print("已同步：软件 %d 条、项目 %d 条 -> %s" % (software, projects, SEED))
    return 0


if __name__ == "__main__":
    sys.exit(main())
