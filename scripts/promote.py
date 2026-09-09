#!/usr/bin/env python3
"""JFrog promotion pipeline helper for GitHub Actions.
Subcommands: download, promote-test, smoke, promote-prod.

A "full package" = the named package + ALL of its dependencies. With
include-deps=yes (default) pip resolves the whole tree; every file is copied
through each tier, so JFrog/Xray scans all of them (top-level and deps alike).

Manifest (the exact file list) is stored IN the GitHub repo under records/manifests/."""
import argparse, glob, json, os, re, subprocess, sys, time
from urllib.parse import urlsplit
import requests

ART = os.environ["ARTIFACTORY_BASE_URL"].rstrip("/")
# Repo names default to the PyPI tiers but can be overridden per-workflow (e.g. the R
# pipeline sets these to r-remote-cache / r-testing / r-local). Promotion is package-agnostic.
REMOTE_STORE = os.environ.get("SRC_CACHE_REPO", "pypi-remote-cache")
TESTING = os.environ.get("TESTING_REPO", "pypi-testing")
PROD    = os.environ.get("PROD_REPO", "pypi-local")
# Manifest folder is per-language (pypi vs r) - set by each workflow so the two pipelines
# never collide on records/manifests/<base>.json.
MANIFEST_DIR = os.environ.get("MANIFEST_DIR", "records/manifests")

# ---------------------------------------------------------------- identities
def sess_admin():
    u = urlsplit(os.environ["ADMIN_INDEX_URL"])
    s = requests.Session(); s.auth = (u.username, u.password); return s
def sess_bearer(tok):
    s = requests.Session(); s.headers["Authorization"] = f"Bearer {tok}"; return s

def base_of(pkg):
    return re.split(r"[<>=!~\[]", pkg, 1)[0].strip().replace("-", "_").lower()

# ------------------------------------------------------------------- helpers
def find_by_name(s, repo, name):
    # Non-admin AQL REQUIRES repo, path, name in .include() (else HTTP 400).
    aql = 'items.find({"repo":"%s","name":"%s"}).include("repo","path","name")' % (repo, name)
    r = s.post(f"{ART}/api/search/aql", data=aql, headers={"Content-Type": "text/plain"})
    if r.status_code != 200:
        print(f"[aql] {repo}/{name}: [{r.status_code}] {r.text[:160]}"); return None
    for it in r.json().get("results", []):
        if it.get("name") == name:                       # exact filename guard
            p = it.get("path", ".")
            return name if p in ("", ".") else f'{p}/{name}'
    return None

def exists(s, repo, rel):
    return s.get(f"{ART}/api/storage/{repo}/{rel}").status_code == 200

def copy(s, src, dst, rel):
    r = s.post(f"{ART}/api/copy/{src}/{rel}?to=/{dst}/{rel}")
    if r.status_code not in (200, 201):
        sys.exit(f"[copy] FAIL {src}/{rel} -> {dst} [{r.status_code}] {r.text[:160]}")
    print(f"[copy] {src}/{rel} -> {dst}/{rel} OK")

# ------------------------------------------------------- manifest (in GitHub)
def manifest_save(base, spec, files):
    os.makedirs(MANIFEST_DIR, exist_ok=True)
    json.dump({"base": base, "spec": spec, "files": files, "ts": time.time()},
              open(f"{MANIFEST_DIR}/{base}.json", "w"), indent=2)
    print(f"[manifest] wrote {MANIFEST_DIR}/{base}.json ({len(files)} files)")
def manifest_load(base):
    p = f"{MANIFEST_DIR}/{base}.json"
    if not os.path.exists(p): sys.exit(f"[manifest] {p} missing - download job must run first")
    return json.load(open(p))["files"]

# ------------------------------------------------------------------- pip pull
def run_pip_download(dest, index_url, spec, no_deps, allow_pre):
    os.makedirs(dest, exist_ok=True)
    cmd = [sys.executable, "-m", "pip", "download", "--no-cache-dir", "--dest", dest,
           "--progress-bar", "off", "--timeout", "300", "--retries", "10"]
    if no_deps:   cmd.append("--no-deps")
    if allow_pre: cmd.append("--pre")
    cmd.append(spec)
    env = {**os.environ, "PIP_INDEX_URL": index_url}          # creds via env, not argv
    rc = subprocess.call(cmd, env=env)
    files = sorted(os.path.basename(f) for f in glob.glob(f"{dest}/*"))
    return rc, files

# ------------------------------------------------------------------------ CLI
def main():
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("download", "promote-test", "smoke", "promote-prod"):
        p = sub.add_parser(c)
        p.add_argument("--package", required=True)
        p.add_argument("--version", default="")
        p.add_argument("--include-deps", default="no")
        p.add_argument("--allow-pre", default="no")
    a = ap.parse_args()

    base = base_of(a.package)
    pinned = bool(re.search(r"[<>=!~]", a.package))
    spec = a.package if (pinned or not a.version) else f"{a.package}=={a.version}"
    os.environ["PKG"] = spec
    no_deps   = a.include_deps != "yes"   # deps ON by default: promote an installable tree, not a lone wheel
    allow_pre = a.allow_pre == "yes"

    if a.cmd == "download":
        rc, files = run_pip_download("/tmp/dl", os.environ["ADMIN_INDEX_URL"], spec, no_deps, allow_pre)
        if rc != 0 or not files:
            sys.exit(f"download failed: rc={rc} files={len(files)}")
        for f in files: print("   -", f)
        manifest_save(base, spec, files)
        print(f"[download] cached {len(files)} file(s)")

    elif a.cmd == "promote-test":
        A, C = sess_admin(), sess_bearer(os.environ["CURATOR_TOKEN"])
        copied = missing = 0
        for name in manifest_load(base):
            rel = find_by_name(A, REMOTE_STORE, name)
            if not rel:
                print("WARN not cached:", name); missing += 1; continue
            if not exists(C, TESTING, rel):
                copy(C, REMOTE_STORE, TESTING, rel); copied += 1
            A.put(f"{ART}/api/storage/{TESTING}/{rel}?properties=promote.approved=false")
        if missing:
            sys.exit(f"promote-test failed: {missing} file(s) missing")
        print(f"[promote-test] staged {copied} file(s)")

    elif a.cmd == "smoke":
        idx = os.environ["TESTER_INDEX_URL"]          # full pypi-testing index, tester creds baked in
        rc, got = run_pip_download("/tmp/smoke", idx, spec, no_deps=True, allow_pre=allow_pre)
        if rc != 0:
            sys.exit("smoke failed: tester could not install from pypi-testing")
        print(f"[smoke] tester installed {len(got)} file(s) from pypi-testing")

    elif a.cmd == "promote-prod":
        C = sess_bearer(os.environ["CURATOR_TOKEN"])
        promoted = 0
        for name in manifest_load(base):
            rel = find_by_name(C, TESTING, name)
            if not rel:
                sys.exit(f"promote-prod failed: {name} not in testing")
            if not exists(C, PROD, rel):
                copy(C, TESTING, PROD, rel); promoted += 1
        print(f"[promote-prod] PROMOTED {promoted} file(s) to {PROD} - live")

if __name__ == "__main__":
    main()
