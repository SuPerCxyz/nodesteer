#!/usr/bin/env bash
# 重新生成 UI 等宽字体子集。
#
# 用法：
#   ./scripts/build-font-subset.sh [源字体.ttf]
#
# 默认源字体为 ~/tmp/MapleMonoNL-NF-CN-Medium.ttf。
# 依赖 fonttools（pyftsubset）和 brotli；若当前 Python 未安装 brotli，可先建临时 venv：
#   python3 -m venv /tmp/font-subset-venv
#   /tmp/font-subset-venv/bin/pip install fonttools brotli
#   PATH=/tmp/font-subset-venv/bin:$PATH ./scripts/build-font-subset.sh
set -euo pipefail

cd "$(dirname "$0")/.."

SOURCE=${1:-"$HOME/tmp/MapleMonoNL-NF-CN-Medium.ttf"}
CHARS_FILE="scripts/font-subset-chars.txt"
OUTPUT="src/assets/fonts/maple-mono-nl-cn-medium-subset.woff2"
mkdir -p "$(dirname "$OUTPUT")"

if [[ ! -f "$SOURCE" ]]; then
  echo "源字体不存在：$SOURCE" >&2
  exit 1
fi

if ! command -v pyftsubset >/dev/null 2>&1; then
  echo "缺少 pyftsubset：请安装 fonttools 和 brotli，或使用脚本注释中的临时 venv。" >&2
  exit 1
fi

# 收集 web/src 下所有会被用户看到的非 ASCII 字符：
# - .ts/.tsx/.css/.html 源码（排除测试和截图快照）
# - locales 已包含在 src 内
# 再补齐 ASCII 可打印字符、常用标点、全角标点与单位符号。
python3 - "$CHARS_FILE" <<'PY'
import pathlib
import sys

root = pathlib.Path("src")
chars = set()

for path in root.rglob("*"):
    if not path.is_file() or path.suffix not in {".ts", ".tsx", ".css", ".html"}:
        continue
    if ".test." in path.name or ".spec." in path.name or "__screenshots__" in path.parts:
        continue
    chars.update(ch for ch in path.read_text(encoding="utf-8", errors="ignore") if ord(ch) > 127)

# ASCII 可打印字符
chars.update(chr(code) for code in range(0x20, 0x7F))

# 常用标点、箭头、数学符号、单位、全角标点（源码中未出现但运行时会用到的兜底字符）
chars.update(
    "—–·→←↑↓↔≥≤≠≈×÷±°µ%‰§¶†‡•…、。，；：？！“”‘’（）【】《》〈〉「」『』"
    "〔〕［］｛｝｜～￥％＃＆＊＋－／＼＝＜＞＠＾｀　"
)

ordered = "".join(sorted(chars, key=ord))
pathlib.Path(sys.argv[1]).write_text(ordered, encoding="utf-8")
print(f"字符清单：{len(ordered)} 个字符（CJK {sum(1 for c in ordered if 0x4E00 <= ord(c) <= 0x9FFF)} 个）")
PY

pyftsubset "$SOURCE" \
  --text-file="$CHARS_FILE" \
  --output-file="$OUTPUT" \
  --flavor=woff2 \
  --layout-features='*' \
  --notdef-glyph \
  --notdef-outline \
  --recommended-glyphs \
  --name-IDs='*' \
  --name-legacy \
  --name-languages='*'

echo "已生成：$OUTPUT（$(stat -c%s "$OUTPUT") 字节）"
