# paper-to-md

把学术 PDF（Nature、Science、IPCC 报告、Supplementary Information、preprints 等）转成单文件 Markdown，图与公式按阅读顺序就位。

基于 `pymupdf` + Python 标准库，纯 Python 实现，约 800 行。原本是我读论文用的 Claude Code Skill，从私有 `~/.claude/skills/` 拉出来开源。

## 为什么再造一个轮子

通用工具在学术 PDF 上各有失败模式：

- `pdftotext -layout` 保留视觉栏 → 按扫描线左右穿插，把两栏排版撕成行级碎片。
- `pdftotext`（无 flag）列流尚可，但不输出图、不输出标题，连字符断词（`eco-\nsystems`）也不修。
- `markitdown` 两栏处理可以，但提取不出任何图片，连字符也不修。
- `pymupdf get_text("text", sort=True)` 同样有扫描线问题。

`scripts/extract.py` 专门面向两栏学术期刊文章及其 Supplementary Information，处理上述所有问题。

## 安装

```bash
pip install pymupdf
```

其余依赖均为 Python 标准库。

## 用法

单 PDF（输出与 PDF 同目录）：

```bash
python3 scripts/extract.py path/to/paper.pdf
```

主文 + SI 一起（共享同一个 `figures/` 目录）：

```bash
python3 scripts/extract.py main.pdf SI.pdf
```

可选参数：

- `-o, --output PATH` 输出 Markdown 路径
- `--dpi INT` 图与公式渲染 DPI（默认 180；打印质量用 240）
- `--prefix STR` 图文件名前缀
- `--figures-dir PATH` 图输出目录

## 处理的事

- 双栏 bbox 阅读顺序分类（页面中线对照 left / right / full）
- body 字体自动检测（按 span 数量挑 dominant `(size, font)` 作正文）
- 跨页 / 跨栏段落合并（前文末非完整句 + 后文小写起首时触发）
- 图区域迭代扩展（±60pt 垂直 / ±30pt 水平，把坐标轴标签和子图标号拉进来）
- 矢量图检测（`get_drawings()`；Nature 大量使用矢量图，纯 raster 检测漏检约 70%）
- 公式聚类渲染为 PNG（小字体按 18pt 垂直 / 220pt 水平邻近度聚簇）
- header / footer 噪声过滤（regex + 字体 + 位置三层）
- 连字符修复（短粘合词典）
- 引用列表按 `\d+\.\s+[A-Z]` 边界拆分（≥4 条触发）

## 已知缺陷

详见 [`references/known_limitations.md`](references/known_limitations.md)：旋转的坐标轴标签会以散行斜体形式出现、公式续行偶尔被分到 small/italic、个别论文作者顺序会被列号交错。均为低严重度，不影响阅读。

文件同时记录了"已经试过但行不通的方案"，避免后续迭代重复踩坑。

## 评测集

`evals/evals.json` 含两条端到端用例（主文 + SI）。新加规则或参数调整后跑一遍，回归测试。

## 修 bug 的偏好

遇到某篇 PDF 输出有问题时，请改 `scripts/extract.py` 的根因逻辑，不要补丁输出的 `.md` 文件——这样 fix 对未来论文同样生效。

## 协作说明

本 Skill 由我（环境与能源系统研究背景，非软件工程出身）与 Claude Code 协作完成。我负责场景定义、能力边界判断、Eval 集设计与 Badcase 收敛策略，Python 工程实现由 Claude Code 完成。欢迎 Issue 与 PR。

## License

MIT
