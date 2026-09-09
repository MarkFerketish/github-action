#!/usr/bin/env Rscript
# JFrog R promotion helper - the R-specific steps of the GitHub Actions pipeline.
# Subcommands:
#   fetch  - resolve the dependency closure and download source tarballs from r-remote
#            (JFrog proxies + caches them into r-remote-cache) and write the manifest.
#   smoke  - as the tester, resolve+download the package from r-testing to prove it is
#            served and indexed after staging (mirrors the pip-download smoke; no compile).
#
# The REST promotion steps (copy across tiers) are handled by scripts/promote.py, which is
# package-type agnostic. Uses ONLY base R, so nothing has to be installed to run this.
# Manifest is stored in the repo under records/manifests/ (same format promote.py expects).

args <- commandArgs(trailingOnly = TRUE)
cmd  <- if (length(args) >= 1) args[1] else ""

get_opt <- function(flag, default = "") {
  i <- match(flag, args)
  if (is.na(i) || i >= length(args)) return(default)
  args[i + 1]
}

package      <- get_opt("--package")
version      <- get_opt("--version")
include_deps <- get_opt("--include-deps", "yes")
if (!nzchar(package)) stop("--package is required")

# Manifest base MUST match promote.py base_of() so the Python promote steps load the same file.
base         <- tolower(gsub("-", "_", package))
# Per-language manifest folder (set by the workflow); default to the R folder.
manifest_dir <- Sys.getenv("MANIFEST_DIR", unset = "records/manifests/r")
norm <- function(u) sub("/+$", "", u)

# ------------------------------------------------------------------------ fetch
if (cmd == "fetch") {
  admin <- norm(Sys.getenv("ADMIN_INDEX_URL_R"))
  if (!nzchar(admin)) stop("ADMIN_INDEX_URL_R not set")
  dest <- tempfile("dl"); dir.create(dest)

  # PACKAGES index off r-remote lists everything CRAN serves (proxied). Used for dep resolution.
  ap <- available.packages(repos = admin, type = "source")
  if (!(package %in% rownames(ap))) stop(sprintf("package '%s' not found on r-remote", package))
  if (nzchar(version) && version != ap[package, "Version"]) {
    # r-remote only serves the latest version via PACKAGES (older ones live under Archive).
    cat(sprintf("[fetch] WARN requested %s but r-remote serves %s; fetching %s\n",
                version, ap[package, "Version"], ap[package, "Version"]))
  }

  wanted <- package
  if (identical(include_deps, "yes")) {
    # A "full package" = the named package + its whole dependency closure.
    deps <- tools::package_dependencies(package, db = ap, recursive = TRUE,
              which = c("Depends", "Imports", "LinkingTo"))
    wanted <- unique(c(package, unlist(deps, use.names = FALSE)))
  }
  wanted <- intersect(wanted, rownames(ap))          # drop base R packages (not downloadable)

  # download.packages (not install.packages) fetches tarballs WITHOUT compiling, which both
  # warms r-remote-cache and gives us the exact file list for the manifest.
  res   <- download.packages(wanted, destdir = dest, repos = admin, type = "source")
  files <- sort(basename(res[, 2]))
  if (length(files) == 0) stop("no files downloaded")

  resolved <- unname(ap[package, "Version"])
  spec     <- if (nzchar(version)) paste0(package, "==", version) else paste(package, resolved)

  dir.create(manifest_dir, recursive = TRUE, showWarnings = FALSE)
  esc        <- function(x) gsub('"', '\\\\"', x)
  files_json <- paste0('"', vapply(files, esc, ""), '"', collapse = ", ")
  json <- sprintf('{\n  "base": "%s",\n  "spec": "%s",\n  "files": [%s],\n  "ts": %f\n}\n',
                  esc(base), esc(spec), files_json, as.numeric(Sys.time()))
  writeLines(json, file.path(manifest_dir, paste0(base, ".json")))

  cat(sprintf("[fetch] r-remote fetched %d file(s) for %s (deps=%s)\n",
              length(files), package, include_deps))
  for (f in files) cat("   -", f, "\n")

# ------------------------------------------------------------------------ smoke
} else if (cmd == "smoke") {
  tester <- norm(Sys.getenv("TESTER_INDEX_URL_R"))
  if (!nzchar(tester)) stop("TESTER_INDEX_URL_R not set")
  dest <- tempfile("smoke"); dir.create(dest)

  # JFrog reindexes r-testing when tarballs land; allow a few retries for index latency.
  ok <- FALSE
  for (attempt in 1:5) {
    res <- tryCatch(
      download.packages(package, destdir = dest, repos = tester, type = "source"),
      error = function(e) NULL)
    if (!is.null(res) && nrow(res) > 0 && file.exists(res[1, 2])) { ok <- TRUE; break }
    cat(sprintf("[smoke] attempt %d: not resolvable yet, waiting...\n", attempt)); Sys.sleep(10)
  }
  if (!ok) stop(sprintf("smoke failed: tester could not resolve '%s' from r-testing", package))
  cat(sprintf("[smoke] tester resolved %s from r-testing (%s)\n", package, basename(res[1, 2])))

} else {
  stop(sprintf("unknown subcommand: '%s' (use fetch|smoke)", cmd))
}
