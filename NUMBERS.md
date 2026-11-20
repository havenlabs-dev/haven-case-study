# How the numbers were counted

Counted on 2026-11-19. Paths are relative to each private repo.

| Number | Command |
|---|---|
| Python lines, `apps/` / `tests/` | `git ls-files "apps/*.py" \| xargs cat \| wc -l` (same for `tests/`). Raw lines including comments and blanks; no cloc on the box |
| Agent tools (493) | `grep -h "ToolSpec(" apps/domains/*/tools.py \| wc -l`, cross-checked against the unique `name="..."` count |
| API routes (~900) | `git grep -cE "^\s*@(router\|app)\.(get\|post\|put\|patch\|delete)\(" -- "apps/*.py"`, summed. A few may sit on routers that aren't mounted, so this is approximate |
| Tables (427) | `sqlite3 -readonly data/haven.db "select count(*) from sqlite_master where type='table'"` |
| Timeline adapters (20) | `grep -c "^@adapter(" apps/core/timeline.py` |
| Jobs (121) | the job registry's `status` command |
| Commits (2,925) | `git rev-list --count HEAD` in each repo: backend 1,454 · Android 437 · docs 444 · web 317 · iPhone 236 · watch 37 |
| Android lines (~100k) | `git ls-files "*.kt" \| xargs cat \| wc -l` → 101,859 |
| Integrations (~20) | counted by hand from the service modules; approximate on purpose |
