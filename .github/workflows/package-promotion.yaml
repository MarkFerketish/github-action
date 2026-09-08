name: JFrog package promotion

on:
  workflow_dispatch:
    inputs:
      package:          { description: "pip package", required: true }
      version:          { description: "version (optional)", required: false, default: "" }
      include_deps:     { description: "include deps", type: choice, options: [no, yes], default: no }
      allow_prerelease: { description: "allow pre-release", type: choice, options: [no, yes], default: no }
  repository_dispatch:
    types: [package-added]

permissions:
  contents: write
  issues: write

concurrency:
  group: promote-${{ github.event.inputs.package || github.event.client_payload.package }}
  cancel-in-progress: false

env:
  ART: ${{ secrets.ART }}
  ADMIN_INDEX_URL: ${{ secrets.ADMIN_INDEX_URL }}
  CURATOR_TOKEN: ${{ secrets.CURATOR_TOKEN }}
  TESTER_TOKEN: ${{ secrets.TESTER_TOKEN }}
  TEAMS_WEBHOOK: ${{ secrets.TEAMS_WEBHOOK }}
  GH_TOKEN: ${{ github.token }}
  PKG:       ${{ github.event.inputs.package || github.event.client_payload.package }}
  PKG_VER:   ${{ github.event.inputs.version || github.event.client_payload.version }}
  INC_DEPS:  ${{ github.event.inputs.include_deps || 'no' }}
  ALLOW_PRE: ${{ github.event.inputs.allow_prerelease || 'no' }}

jobs:
  download:
    runs-on: ubuntu-latest
    outputs:
      issue: ${{ steps.open.outputs.number }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install requests
      - id: open
        run: |
          n=$(gh issue create --title "Promote $PKG $PKG_VER" \
                --body "Run ${{ github.run_id }} by @${{ github.actor }}" \
                --label promotion | grep -oE '[0-9]+$')
          echo "number=$n" >> "$GITHUB_OUTPUT"
      - run: |
          python scripts/jfrog.py download --package "$PKG" --version "$PKG_VER" \
            --include-deps "$INC_DEPS" --allow-pre "$ALLOW_PRE"
      - name: Commit manifest
        run: |
          git config user.name "pipeline-bot"
          git config user.email "actions@github.com"
          git add records/manifests
          git commit -m "manifest: $PKG (run ${{ github.run_id }})" || echo "no change"
          git push
      - uses: actions/upload-artifact@v4
        with: { name: manifest, path: records/manifests }
      - run: gh issue comment ${{ steps.open.outputs.number }} --body "**download** ok - manifest committed."

  promote-test:
    needs: download
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install requests
      - uses: actions/download-artifact@v4
        with: { name: manifest, path: records/manifests }
      - run: python scripts/jfrog.py promote-test --package "$PKG" --version "$PKG_VER"
      - run: gh issue comment ${{ needs.download.outputs.issue }} --body "**promote-test** ok - staged to pypi-testing."

  smoke-test:
    needs: [download, promote-test]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install requests
      - uses: actions/download-artifact@v4
        with: { name: manifest, path: records/manifests }
      - run: python scripts/jfrog.py smoke --package "$PKG" --version "$PKG_VER"
      - run: gh issue comment ${{ needs.download.outputs.issue }} --body "**smoke-test** ok - tester installed from pypi-testing. Awaiting approval."

  approve:
    needs: [download, smoke-test]
    runs-on: ubuntu-latest
    environment: production
    steps:
      - run: gh issue comment ${{ needs.download.outputs.issue }} --body "**approved** by @${{ github.actor }} - promoting to prod."

  promote-prod:
    needs: [download, approve]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install requests
      - uses: actions/download-artifact@v4
        with: { name: manifest, path: records/manifests }
      - run: python scripts/jfrog.py promote-prod --package "$PKG" --version "$PKG_VER"
      - run: |
          gh issue comment ${{ needs.download.outputs.issue }} --body "**promote-prod** ok - live in pypi-local. Closing."
          gh issue close ${{ needs.download.outputs.issue }}
