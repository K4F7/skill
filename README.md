# skill

个人 / 团队可复用的 Agent Skills 仓库。安装后，兼容的 coding agent 会按 `description` 在相关任务里自动加载。

Skills 遵循 [Agent Skills](https://agentskills.io/) 格式。

[![skills.sh](https://skills.sh/b/K4F7/skill)](https://skills.sh/K4F7/skill)

## Available Skills

### issue-to-main

从最新 `origin/main` 开隔离 worktree，实现已有 GitHub Issue，本地验证后开 PR，等必需 CI、失败则在同一 worktree 修复，开启 auto-merge，确认 PR 已合入且 issue 已关闭，再清理 worktree 和分支。

**Use when:**

- `implement issue #123`
- 把一个 issue 做到 PR 合入并关闭
- 使用 worktree PR 流程

**Requires:** 本地 `git`、`gh`，仓库默认分支为 `main`。

```bash
npx skills add K4F7/skill --skill issue-to-main
```

## 安装

```bash
npx skills add K4F7/skill
```

列出仓库里的 skill，不安装：

```bash
npx skills add K4F7/skill --list
```

只装某一个：

```bash
npx skills add K4F7/skill --skill <skill-name>
```

全局安装（跨项目可用）：

```bash
npx skills add -g K4F7/skill
```

## 目录结构

```text
skills/
  <skill-name>/
    SKILL.md          # 必需：YAML frontmatter + 指令
    scripts/          # 可选：可执行脚本
    references/       # 可选：按需加载的补充文档
    assets/           # 可选：模板、示意图等静态资源
```

每个 skill 目录名必须和 `SKILL.md` 里的 `name` 一致（小写 + 连字符）。

## 新增 skill

在仓库根目录：

```bash
npx skills init skills/<skill-name>
```

然后改 `skills/<skill-name>/SKILL.md` 的 `name`、`description` 和正文指令。`description` 要同时写清「做什么」和「什么时候用」，这是 agent 决定是否加载的依据。

写完后推到 `main`。别人（或你自己的其他项目）用上面的 `npx skills add` 即可拉取。

## License

MIT
