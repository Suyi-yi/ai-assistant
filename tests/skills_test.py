"""技能库自检：扫描、去重、中文名优先、搜索、巨型技能截断。

用法：python tests/skills_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import skills  # noqa: E402

passed = 0
failed = 0


def check(label: str, condition, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  [ok] {label}")
    else:
        failed += 1
        print(f"  [FAIL] {label} {detail}")


def matches(items, query):
    low = query.lower()
    return [
        item for item in items
        if low in item["name"].lower()
        or low in item["display_name"].lower()
        or low in (item["description"] or "").lower()
        or low in (item["short"] or "").lower()
    ]


def main() -> None:
    items = skills.list_skills(force=True)
    print(f"技能库：扫描到 {len(items)} 个")
    check("数量在合理范围", len(items) >= 50, f"count={len(items)}")
    names = [item["name"] for item in items]
    check("没有重名", len(names) == len(set(names)))

    personal = [item for item in items if item["source"] == "个人"]
    check("能识别个人技能", len(personal) >= 20, f"personal={len(personal)}")
    check("个人技能排在最前", items[0]["source"] == "个人", items[0]["source"])

    cn = [item for item in items if item["display_name"] != item["name"]]
    check("中文显示名优先", len(cn) >= 10, f"with_cn={len(cn)}")
    sample = next((item for item in items if item["name"] == "karpathy-guidelines"), None)
    check("Karpathy 技能有中文名", bool(sample) and sample["display_name"] != "karpathy-guidelines",
          str(sample and sample["display_name"]))

    check("搜『嵌入式』有结果", len(matches(items, "嵌入式")) > 0)
    check("搜『ppt』有结果", len(matches(items, "ppt")) > 0)
    check("搜『github』有结果", len(matches(items, "github")) > 0)

    huge = max(items, key=lambda item: item["bytes"])
    print(f"  最大技能：{huge['name']}（{round(huge['bytes'] / 1024)} KB）")
    detail = skills.read_skill(huge["name"])
    check("巨型技能能读取", detail.get("ok"))
    check("读取时做了截断", detail.get("truncated") is True)
    check("截断后不超过上限", len(detail["content"]) <= skills.DETAIL_LIMIT + 1)

    block = skills.skill_prompt_block([huge["name"]])
    check("注入提示词按 8000 字截断", len(block) < skills.INJECT_LIMIT + 1500, str(len(block)))
    check("注入内容带截断说明", "只注入了开头部分" in block)
    check("找不到的技能不报错", skills.read_skill("不存在-技能")["ok"] is False)

    print(f"\n通过 {passed} 项，失败 {failed} 项")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
