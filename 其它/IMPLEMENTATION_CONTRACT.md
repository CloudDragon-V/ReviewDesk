# ReviewDesk 0.1 implementation contract

Runtime: Python 3.14, PySide6-Essentials 6.11.2; Windows x64.
Package: src/reviewdesk. Entry: app.py. Root owns models.py, service.py, api.py, storage.py, packaging and integration.

Core owns analysis.py, context.py and tests/test_analysis.py, tests/test_context.py.
- `scan_project(root: pathlib.Path, max_files: int = 500) -> Analysis`: bounded Python-only read-only snapshot, skip generated/hidden/venv/secrets/symlinks; explain omissions.
- `analyze_sources(files: list[SourceFile]) -> Analysis`: AST static findings and graph. Never execute input.
- `build_context(analysis: Analysis, budget: int = 6000, strategy: str = 'dependency', focus_paths: list[str] | None = None) -> Context`: stable bounded selection, source line labels, redact recognizable secrets. Support 'truncate' and 'dependency'. Estimate conservatively, no claim exact tokenizer.
- `estimate_tokens(text: str) -> int`.

Root service interface:
- `ReviewService()`
- `analyze_project(root: Path) -> Analysis`
- `analyze_code(code: str, filename: str = 'snippet.py') -> Analysis`
- `review(analysis: Analysis, project_name: str, use_api: bool = False, api_key: str = '', model: str = 'deepseek-flash', budget: int = 6000, strategy: str = 'dependency', category: str = 'all', max_cost_cny: str = '0.20') -> ReviewResult`
- `preview(analysis, budget=6000, strategy='dependency') -> Context`
- `export_report(result: ReviewResult, path: Path) -> None`: suffix .json or .md
- `store.history() -> list[dict]`, `store.load_report(id: int) -> ReviewResult`, `store.save_report(result) -> int`, `store.update_report(id,result)`
- `CredentialStore().get_key() -> str`, `.set_key(key: str) -> None`, `.delete_key() -> None` (Windows Credential Manager)
- `DeepSeekClient(api_key: str).check_connection() -> dict` (GET balance/models, no charged completion)

UI owns ui.py / widgets.py / resources and tests/test_ui.py. `MainWindow()` with `.load_demo()` and `.run_local_review()` asynchronous, `.analysis`, `.result`; `--smoke-test` supplied by root entry. Elegant Chinese desktop with navigation, review, graph, usage/history, settings; demo runnable without key; worker tasks; code navigation; result filters; report export; dismiss/accept status. Avoid HTML rendering for untrusted result text. Set key optional; preview before paid transmission; no hidden charge. Explain static graph inference and budget coverage.

Evaluation agent owns evaluation/ only. Blind fixture author freezes hashed cases and oracle before engine inspection. Do not expose oracle to developers until candidate frozen. Root will invoke harness after implementation; independent grading later sees anonymized outputs with hidden candidate identity. Describe this as simulated double-blind (agent roles share model family/environment), not human study.
