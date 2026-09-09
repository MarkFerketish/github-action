# JFrog package promotion

Governed pipeline that moves a Python package up three JFrog Artifactory trust tiers,
with a human approval gate before it reaches production.

```
pypi-remote (raw internet proxy)  ->  pypi-testing (staged)  ->  pypi-local (approved / prod)
        admin warms the cache          curator copies              curator copies
                                        tester smoke-tests          AFTER a human approves
```

A **full package = the named package + all of its dependencies.** With deps on (the
default), the whole install tree is promoted, so a consumer can `pip install` it from
`pypi-local` and it just works. JFrog/Xray scans every file that moves through.

---

## One-time setup

1. **Repo secrets** (Settings -> Secrets and variables -> Actions):

   | Secret | Identity | Value / format |
   |---|---|---|
   | `ARTIFACTORY_BASE_URL` | — | `https://<host>/artifactory` (no trailing slash) |
   | `ADMIN_INDEX_URL` | admin (pip) | `https://admin:<token>@<host>/artifactory/api/pypi/pypi-remote/simple` |
   | `CURATOR_TOKEN` | curator (REST) | `<curator access token>` (used as a Bearer token for server-side copies) |
   | `TESTER_INDEX_URL` | tester (pip) | `https://tester:<token>@<host>/artifactory/api/pypi/pypi-testing/simple` |

   Naming rule: **pip** identities carry the repo in the URL, so they're `*_INDEX_URL`
   (`ADMIN_INDEX_URL` -> pypi-remote, `TESTER_INDEX_URL` -> pypi-testing). The **curator**
   works over the REST API where the repo is named in the request path, so it's just a
   `*_TOKEN`. `<host>` is the same host as `ARTIFACTORY_BASE_URL`.

2. **Environment** named `production` (Settings -> Environments):
   add yourself / the approvers under **Required reviewers**. This is what pauses the
   pipeline for sign-off before prod.

3. **Label** `promotion` must exist (Issues -> Labels) — each run opens an audit issue with it.

---

## How to run it

### Option A — manually from GitHub
1. Actions -> **JFrog package promotion** -> **Run workflow**.
2. Fill in `package` (e.g. `requests`), optional `version`, `include deps` (default **yes**),
   `allow pre-release` (default no).
3. Click **Run workflow**.

### Option B — automatically from Databricks
The admin notebook does a `pip download` to warm `pypi-remote`, then fires a
`repository_dispatch` (`event_type: package-added`) with the package details. That starts
the same pipeline with no clicks.

---

## What happens each run

| Stage | What it does |
|---|---|
| **download** | Opens an audit issue, warms `pypi-remote`, writes the file list to `records/manifests/<pkg>.json`, commits it. |
| **promote-test** | Curator copies every manifest file into `pypi-testing` (server-side, no re-download). |
| **smoke-test** | Tester `pip install`s the package from `pypi-testing` to prove it's usable. |
| **approve** | **Pauses** for a required reviewer to approve in the `production` environment. |
| **promote-prod** | After approval, curator copies the files into `pypi-local`, then closes the issue. |
| **rejected** | Runs only if any stage fails — including a reviewer **rejecting** at the gate. Comments who rejected and their note (or a generic failure) on the audit issue. |

Every stage comments its status on the audit issue, so the issue is the full history of the promotion.

---

## Dependencies

- `include deps = yes` (default) pulls the package **and its whole dependency tree**.
  This is what makes it installable from `pypi-local`.
- The reviewer at the approval gate is approving **the package and every dependency** listed
  in `records/manifests/<pkg>.json` — check that file to see the full set.
- Set `include deps = no` per run only when you deliberately want the single top-level file.

---

## Air-gapped / production runners

The jobs need the Python `requests` library. Each job uses:

```yaml
- name: Ensure requests
  run: python -c "import requests" 2>/dev/null || pip install requests
```

- **Bake `requests` into the self-hosted prod runner image.** Then `import requests` succeeds
  and the step installs **nothing** — no call to public PyPI. This is the only remaining
  internet dependency, so closing it makes the pipeline fully air-gap clean.
- On internet-connected runners (e.g. the test env) the same line just installs on first use.

When cutting over to prod, also change each job's `runs-on: ubuntu-latest` to your
self-hosted label (e.g. `[self-hosted, prod]`).
