# Contributing

Thanks for helping make this list better! There are three easy ways to contribute.

## 1. Suggest a paper (easiest)

Open an [issue](../../issues/new) with:

- **Paper title + link** (arXiv / DOI / publisher page)
- **Why it belongs here** — i.e. what makes it *zero-shot* or *training-free* for navigation

## 2. Report a mis-classification

Same as above — just say which paper and what's wrong (wrong category, not
actually zero-shot, duplicate, etc.). These reports directly improve
`scripts/relevance.py` and the category rules.

## 3. Add a new source or query (code)

The pipeline is deliberately **zero-dependency** (Python standard library only).

### Where things live

```
scripts/
  relevance.py   ← ⭐ relevance rules (add terms here if a legit paper is filtered out)
  sources.py     ← harvesters (OpenAlex, Crossref) + QUERIES list
  pipeline.py    ← harvest → filter → dedup → store (SQLite)
  export.py      ← generates README.md + docs/data/papers.json
data/papers.db   ← the dataset (committed, so history is preserved)
docs/            ← GitHub Pages site (plain HTML/JS, no build step)
```

### Adding a search query

Append to `QUERIES` in `scripts/sources.py`:

```python
QUERIES = [
    ...
    "your new query",
]
```

Queries are sent to OpenAlex (`title_and_abstract.search`) and Crossref
(`query.bibliographic`) with a polite `mailto`.

### Adding relevance terms

Edit the term lists in `scripts/relevance.py`. There's a self-test at the
bottom of that file:

```bash
python3 scripts/relevance.py     # should print "6/6 passed"
```

**Please add a test case** for any new rule — that's what keeps the filter from
drifting over time.

### Testing locally

```bash
python3 scripts/relevance.py            # unit-style checks
python3 scripts/sources.py              # smoke-test the two harvesters
python3 scripts/pipeline.py --days 7    # incremental harvest
python3 scripts/export.py               # regenerate README + site data
python3 -m http.server -d docs 8000     # preview the site at localhost:8000
```

No `pip install` required. Python 3.11+.

### Guidelines

- **No new dependencies** unless there's a strong reason (see `requirements.txt`).
- Keep the site **plain HTML/CSS/JS** — no build step, no frameworks.
- Prefer **rules over manual curation** — if you spot a pattern of mistakes,
  encode it in `relevance.py` rather than fixing entries one by one.

## Note on scope

This list is intentionally **narrow**: zero-shot / training-free navigation.
For broader coverage, please contribute to the
[related lists](README.md#-related-awesome-lists) instead.
