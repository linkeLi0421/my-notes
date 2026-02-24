# My Notes (Online Book)

This repo is set up as an online book using MkDocs Material. Notes live under `notes/` and are published as-is.

## Local preview

```bash
pip install -r requirements.txt
mkdocs serve
```

Then open the local URL printed in the terminal.

## Deploy to GitHub Pages

A GitHub Actions workflow is included. After your first push to `main`:

1. Open the repo settings in GitHub.
2. Pages -> Source: select the `gh-pages` branch.
3. Save. The site will publish to GitHub Pages.

## Add new notes

Just drop new `.md` files under `notes/` (any folder depth). They appear automatically in the sidebar.
