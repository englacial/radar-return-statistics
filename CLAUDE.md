Keep code concise with minimal but useful documentation.

Use uv for dependency management and `uv run` to run any python code, including small inline runs.

Put any session notes, longer text analyses, purely temporary code, or any other session artifacts you want to keep but that aren't part of the main flows in `claude_notes/`. Put plans in `claude_plans/`, prefix them with the date in YYYYMMDD-title.md format, and update these when you have completed them for future reference.

Do not edit anything outside of this directory. Put any outputs into an `outputs/` directory.

Review the `docs/` directory before starting on any task. `docs/architecture.md` describes the basic outline.

Run the tests (using pytest) after making any changes. Add tests to verify new functionality you've been asked to add, but do not modify existing tests without asking.

Useful references:
* Required surface SNR calculations: https://github.com/thomasteisberg/required_surface_snr