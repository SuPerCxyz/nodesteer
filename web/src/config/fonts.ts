/**
 * 可用界面字体列表（用于生成 `font-<name>` 类，例如 `font-maple`）。
 *
 * 字体为本地子集：`src/assets/fonts/maple-mono-nl-cn-medium-subset.woff2`，
 * 由 Maple Mono NL NF CN Medium 按 UI 实际用字切子集生成，
 * 声明见 `src/styles/index.css`，重新生成见 `scripts/build-font-subset.sh`。
 *
 * 📝 如何新增字体（Tailwind v4+）：
 * 1. 在本数组加入字体名。
 * 2. 把字体文件放入 `src/assets/fonts/`，并在 `src/styles/index.css` 增加 `@font-face`。
 * 3. 在 `src/styles/theme.css` 的 `@theme inline` 中定义 `--font-<name>`。
 * 4. 若首屏就要生效，在 `index.html` 的内联脚本中同步默认值。
 */
export const fonts = ['system', 'maple'] as const
