# Restore pre–code-catalog dashboard

To revert to the baseline dashboard (before code catalog enrichment):

```bash
cp _baseline/index.html ../index.html
cp _baseline/README.md ../README.md
```

Then refresh the browser. No server restart needed.

Optional: remove `code_catalog.json` from the parent folder if you want no catalog file present.
