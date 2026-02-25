# Agent Guidelines for my-notes

These rules apply to all automated changes in this repo. Keep changes minimal, consistent, and readable.

## Scope
- Applies to all files under this repository.
- If a request conflicts with these rules, follow the user request and note the conflict.

## Notes format
- Notes live under `notes/YYYY/YYYY-MM/`.
- File name: `YYYY-MM-DD-topic-slug.md` (lowercase, hyphenated).
- Each note starts with YAML front matter.
- Required front matter fields:
  - `title` (string)
  - `date` (YYYY-MM-DD)
  - `project` (string)
  - `topic` (string)
- Optional fields:
  - `id`, `tags`, `source`, `confidence`
- If a note already has a front matter schema, preserve it.
- The first H1 (`# ...`) should match the `title`.

## Style & typography
- Do not change typography or global styling unless explicitly asked.
- Keep `mkdocs.yml` theme font settings and `notes/styles/extra.css` consistent.
- Use UTF-8 encoding; preserve non-English text without re-encoding.

## Markdown conventions
- Use consistent heading levels: H1 for title, H2 for major sections, H3 for subsections.
- Use fenced code blocks with a language tag when possible.
- Avoid excessively long inline HTML unless necessary.
- Keep links relative when pointing to local notes or assets.

## Assets
- Store images under `assets/YYYY-MM-DD/`.
- Reference images with relative paths, e.g. `../../assets/2026-02-24/example.png`.

## Safety
- Do not delete or rewrite existing notes unless requested.
- Avoid reformatting entire files for small changes.

