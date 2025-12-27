# Repository Guidelines

## Project Structure & Module Organization
- `src/main.rs` hosts the Axum web server, request handlers, and in-module tests.
- `src/tvdb.rs` wraps TVDB API access and includes API integration tests.
- `Cargo.toml` defines Rust dependencies and crate metadata.
- `Dockerfile` and `fly.toml` describe container and Fly.io deployment settings.

## Build, Test, and Development Commands
- `cargo run` starts the API locally on `0.0.0.0:3000`.
- `cargo test` runs unit and integration tests (some tests skip without `TVDB_API_KEY`).
- `cargo fmt` formats Rust code with rustfmt.
- `cargo clippy` runs Rust lints; fix warnings before submitting.

## Coding Style & Naming Conventions
- Use standard Rust 2024 style with rustfmt (4-space indentation).
- Prefer clear, snake_case for functions/variables and UpperCamelCase for types.
- Keep handlers small; extract helpers for TVDB parsing and scoring logic.
- Avoid adding new dependencies unless they are clearly justified.

## Testing Guidelines
- Tests live in `#[cfg(test)]` modules in `src/main.rs` and `src/tvdb.rs`.
- Use `cargo test` for all tests; TVDB-dependent tests require `TVDB_API_KEY`.
- Name tests with `test_...` and cover parsing/scoring edge cases when added.

## Commit & Pull Request Guidelines
- Commit messages follow Conventional Commits (e.g., `feat: ...`, `fix: ...`).
- PRs should describe behavior changes, reference related issues, and list manual test steps.
- Include response samples or curl commands when changing API behavior.

## Configuration & Runtime Notes
- The API provides redirects for `/poster`, `/fanart`, and `/torrent-art`.
