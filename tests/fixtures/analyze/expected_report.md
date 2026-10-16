# Token usage report

Local use only: this report names local paths, projects and sessions. To share numbers, use `eai analyze --export`, which contains no names or paths.

2 sessions in 2 projects, 2026-10-01 to 2026-10-02

## Tokens

| tokens      |  main | subagents | total |
|-------------|------:|----------:|------:|
| input       |   135 |        55 |   190 |
| output      |   130 |        15 |   145 |
| cache read  | 6,290 |       550 | 6,840 |
| cache write | 5,450 |       500 | 5,950 |

9 API calls, 2 user prompts, 1 compactions

## Waste

exact = from API usage; est. = characters / 4. Exact and estimated numbers are never added together.

| rule              | hits | exact tokens | est. tokens | fix                              |
|-------------------|-----:|-------------:|------------:|----------------------------------|
| repeated-read     |    1 |            0 |         125 | read-once                        |
| large-tool-output |    1 |            0 |       6,250 | trim                             |
| cache-rebuild     |    1 |        2,300 |           0 | defaults                         |
| big-instructions  |    1 |            0 |           0 | defaults                         |
| many-mcp          |    1 |            0 |           0 | defaults (mcp-diet if justified) |
| long-session      |    0 |            0 |           0 | defaults                         |

## Top 10 sessions

| project             | session | date       | prompts | api calls | compactions | total tokens | first-call context | peak context |
|---------------------|---------|------------|--------:|----------:|------------:|-------------:|-------------------:|-------------:|
| C--work-secret-repo | sess-a  | 2026-10-01 |       1 |         6 |           1 |        9,995 |              2,100 |        2,305 |
| C--work-other       | sess-b  | 2026-10-02 |       1 |         3 |           0 |        3,130 |              1,010 |        1,055 |

- sess-a: main 8,875 / subagents 1,120 tokens; models claude-sonnet-5; tools Read 3, Bash 1, mcp:secretsrv 1
- sess-b: main 3,130 / subagents 0 tokens; models arn:aws:bedrock:us-east-1:123456789012:inference-profile/x, claude-opus-5-5; tools Read 2, Edit 1

## Tool calls

| tool          | calls |
|---------------|------:|
| Read          |     5 |
| Bash          |     1 |
| Edit          |     1 |
| mcp:secretsrv |     1 |

## Findings

### repeated-read (fix: read-once)

- C--work-secret-repo/sess-a 2026-10-01 09:01: C:/work/secret-repo/secret_module.py read 2x [main] (500 chars, est. 125 tokens)

### large-tool-output (fix: trim)

- C--work-secret-repo/sess-a 2026-10-01 09:02: Bash:pytest returned 25,000 chars [main] (25,000 chars, est. 6,250 tokens)

### cache-rebuild (fix: defaults)

- C--work-secret-repo/sess-a 2026-10-01 09:20: idle 18 min, then 2,300 tokens written to cache (exact 2,300 tokens)

### big-instructions (fix: defaults)

- - -: <TMP>/other-repo/CLAUDE.md: 250 lines, 7,140 bytes (size on disk today)

### many-mcp (fix: defaults (mcp-diet if justified))

- C--work-secret-repo/sess-a 2026-10-01 09:00: 4 MCP servers announced (alpha, beta, gamma, secretsrv); first-call context 2,100 tokens

## Parse health

Parsed 3 files (0 unreadable), 28 lines; skipped 1 bad lines and 1 unknown records (mystery-record x1); Claude Code 2.1.290 to 2.1.290
