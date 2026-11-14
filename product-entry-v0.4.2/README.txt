Forushyar-AI Product Entry UI - v0.4.2

This archive intentionally ships the Python API and HTML page as separate files.
That avoids a large shell heredoc being cut off during download/copy.

On Debian, from the Forushyar-AI repository root, extract this archive and run:
  bash product-entry-v0.4.2/install.sh "$HOME/forushyar-AI"

Then build/restart only the API:
  cd ~/forushyar-AI
  docker compose build api
  docker compose up -d api

Check:
  curl -i http://127.0.0.1:8000/admin/products

Expected: HTTP 200. The page itself is public on localhost, but its API actions
require the existing X-API-Key entered in the form.

The installer backs up every file it changes. It does not delete Docker volumes
and does not run database migrations.
