# Activate the automation (2 files, 2 minutes)

GitHub requires special permission to create files under `.github/workflows/`,
which this connection doesn't have — so the two workflow files ship here as
normal files with **identical content**.

## Option A — paste them yourself on github.com (2 min)
1. Repo → **Add file → Create new file**
2. Name it exactly: `.github/workflows/shorts.yml`
3. Copy the content of `workflow-files/shorts.yml` into it → **Commit changes**
4. Repeat for `.github/workflows/selftest.yml` (optional but recommended)

## Option B — reconnect Arena's GitHub with the "workflows" permission
Then just ask the agent to "move the workflows into place" — it's one commit.

Until then the Python pipeline is fully functional locally:
`python -m shorts.run --dry-run` etc. (see README.md).
