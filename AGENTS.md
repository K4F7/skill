# AGENTS.md

给在本仓库里新增或修改 skill 的 coding agent 用。

## 仓库做什么

这是一个 [Agent Skills](https://agentskills.io/) 包，供 `npx skills add K4F7/skill` 安装。每个 skill 是 `skills/<name>/` 下的一个目录，至少包含 `SKILL.md`。

不要在仓库根放 `SKILL.md`。根级 `SKILL.md` 会让 CLI 把整个仓库当成单个 skill。

## 新增 skill

1. 目录：`skills/<skill-name>/`，`skill-name` 只用小写字母、数字、连字符，不能以连字符开头或结尾，不能有连续连字符。
2. 用官方脚手架或手写均可：

   ```bash
   npx skills init skills/<skill-name>
   ```

3. `SKILL.md` 必须以 YAML frontmatter 开头，且至少包含：

   ```markdown
   ---
   name: skill-name
   description: 做什么，以及何时使用。写上触发词。
   ---
   ```

4. `name` 必须和父目录名完全一致。
5. `description` 同时写「能力」和「触发场景」。不要写成空泛的标题。

可选 frontmatter：`license`、`compatibility`（仅当有环境要求）、`metadata`。

可选子目录：

- `scripts/` — 确定性自动化
- `references/` — 长文档、清单、模板
- `assets/` — 静态资源

## 写 SKILL.md

- 正文是给 agent 的步骤，不是给人类的宣传文案。
- 控制在约 500 行以内。细节放到 `references/`，从 `SKILL.md` 用相对路径引用，且只引用一层。
- 有脚本时写清怎么跑、依赖是什么。Bash 用 `#!/bin/bash` 和 `set -e`；Node 用 `#!/usr/bin/env node` 和 `.mjs`。状态信息走 stderr，机器可读输出走 stdout。
- 不要把本文件或 README 里的约定再抄一份进 skill 正文。

## 仓库级文件

- `README.md`：安装命令和对外说明。新增对外可见的 skill 后，在 README 补一条简介。
- `skills.sh.json`：只在 skills.sh 仓库页需要分组时再加。没有 skill 或只有一两个时不要硬写空分组。
- 改完后用 `npx skills add . --list` 确认 CLI 能发现。
