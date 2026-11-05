---
name: ssh-skill
description: "Use when a task requires SSH or SCP/SFTP behavior, a remote server, server alias/IP/hostname/user@host, bastion or jump-host access, remote command execution, upload/download, server transfer, deployment, tunnels, port forwarding, or remote internal services; Chinese triggers include 服务器, 远程, 连接, 登录, 上传, 下载, 部署, 跳板机, 隧道. DO NOT use for local shell commands, localhost/current-machine work, local Docker, browser downloads, Git remotes, or direct non-SSH database access."
allowed-tools: Bash, Read, Write, Glob
metadata:
  version: "4.0.0"
  keywords: SSH,SCP,SFTP,server,remote,bastion,jump-host,upload,download,transfer,tunnel,deploy,服务器,远程,连接,登录,上传,下载,部署,跳板机,隧道
---

# SSH Skill v4

Use the unified CLI for SSH-mediated work in Codex and Claude Code. Continue in
the user's language. DO NOT use for local shell/Docker, Git remotes, browser
downloads, or databases that do not traverse SSH.

## Entry point and discovery

Resolve the directory containing this loaded file as `<SSH_SKILL_ROOT>` once.
Invoke `<SSH_SKILL_ROOT>/scripts/ssh_skill.py` with the platform Python; pass the
remote command as one process argument, without a local shell wrapper.
Do not construct raw `ssh`, `scp`, `sftp`, or `rsync` commands.

- Use the known alias. Only use `config find`/`list-servers` when it is unknown;
  do not dump the server catalog before every task.
- Do not run doctor before every operation. Use `doctor --json` for dependency,
  platform, config or copy-drift problems. Keep the loaded copy authoritative;
  do not overwrite other installations merely to normalize them.
- Read only the platform guide needed for syntax:
  [Windows](references/platforms-windows.md),
  [macOS](references/platforms-macos.md), [Linux](references/platforms-linux.md).
- Read [commands](references/commands.md) for operation-specific flags, and
  [safety](references/safety.md) for credentials, changed host keys or recovery.

## Execution and output

The CLI emits one JSON document. Parse `success`, `data`, `error`, and `meta`
before continuing; a failed tool call is not a successful remote action.

- Default output is compact: no command echo, duplicate success flags, empty
  stderr or empty diagnostic metadata. stdout/stderr retain head and tail when
  over 16 KiB; truncation is explicit in `data.output`.
- For diagnostics/older consumers use global flags **before** the operation:
  `--verbose` restores the full envelope (up to 256 KiB), or
  `--output-limit 65536` raises the per-stream compact limit.
- Keep the structured result inside the tool workflow. Tell the user the
  outcome, material evidence and unresolved issues, not every JSON envelope.
- Prefer one bounded command for related read-only facts. Avoid repeated
  status calls, shell banners, whole-config dumps and duplicate transcript
  wrappers. For long work, start a tracked job and read its status/log tail;
  do not hold an ordinary exec open until an ambiguous timeout.
- Request transfer progress with `--progress` only when useful; it is bounded
  JSONL on stderr. Never merge it into stdout. Truncation alone is not a reason
  to rerun a mutation; narrow the inspection instead.

## Failures and authorization

- `outcome_unknown`: stop replaying that operation and preserve its request ID.
  Continue with independent, bounded read-only status/log checks within the
  existing authorization. A clearly read-only query timeout does not itself
  require user approval. For mutations, do not infer success or replay until
  state is known. Ask when verification/recovery is unsafe, outside scope, or
  cannot determine state; stop dependent mutations in that case.
- Authentication failure or changed host key: do not loop or disable checking.
  Verify changed keys through an independent trusted channel before updating
  just that host's record.
- A local OpenSSH version check can select Paramiko for older direct
  clients before any command is sent; host-key verification stays enabled.
  Do not bypass a bastion or agent-forwarding requirement for compatibility.
- Missing packages are environment issues, not authentication failures. Help
  and doctor work without Paramiko. Use an existing compatible environment or
  install task dependencies within the user's authorized scope.
- Preview exact targets before multi-host/irreversible work. Existing task
  authorization normally covers necessary implementation and verification;
  do not ask again just because a preview is required.
- `cluster` is a disconnected preview until `--apply`; production targets also
  require `--confirm-production`. On partial failure, retain per-host results
  and do not replay successful hosts.
- `config delete` previews first; `--apply` saves a backup. Preserve unrelated
  aliases, formatting and comments. Do not add plaintext password metadata.

## Transfers and tunnels

- Use upload/download/transfer with POSIX remote paths. Direct server transfer
  does not forward an agent unless explicitly authorized with
  `--allow-agent-forwarding`. Use `--resume` only for known interrupted copies.
- Tunnels bind loopback. Keep the returned tunnel ID/port for subsequent use.
  Repeating the same alias/local-port/remote-target start reuses that tunnel;
  a different target on the same port is an error. Inspect before stopping a
  vaguely identified tunnel, and stop task-owned temporary tunnels on cleanup.
- Daemon reuse is automatic; `--no-daemon` is a diagnostic escape hatch. Do not
  restart daemons as a routine prerequisite for the next command.
- Never print passwords, private keys, tokens, askpass environment values or
  credential-bearing config. Use private files for sensitive task artifacts.

Legacy entrypoints remain compatible. New callers use this unified CLI;
`--legacy-json` is only for an identified old consumer.
