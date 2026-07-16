# Repository Notes Loop (Exposed in Git, Excluded from Website)

This directory is committed to GitHub to support cloud persistence and agent access, but it is completely excluded from the Next.js visualizer website build. It serves as your inbox and synthesis workbench for the curriculum feedback loop.

---

## 🔄 The Feedback Loop

```
Production Work
      ↓
New Learning (Blog, Incident, Issues, Reddit, Traces)
      ↓
Captured in notes/incoming/ (Markdown + Images)
      ↓
Ingested & Synthesized (Once a week via Coding Agent)
      ↓
Curriculum Update (Updates part-X/ examples & walkthroughs)
      ↓
Interactive Teaching
      ↓
Deeper Engineering Understanding
      ↓
Better Production Work
```

---

## Workflow

1. **Capture**: Drop articles, incident logs, GitHub issues, stack traces, and screenshots into the `incoming/` directory.
2. **Review**: Once a week, analyze the inbox.
3. **Ingest**: Use a coding agent or manual editing to integrate these notes into the relevant curriculum chapters (`part-X/`), updating `example.py`, documentation, and system platform architectures.
4. **Prune**: Delete processed notes from the `incoming/` folder.

---

## Format Recommendations

Use **Markdown (`.md`)** for all note files in `incoming/`:
* **Flexibility**: Markdown integrates text descriptions, code blocks, checklists, and references to local images.
* **Rich Content**: Keep local images under `incoming/images/` and reference them with standard `![Description](images/filename.png)` tags.
* **AI Ready**: Markdown is easily read and parsed by AI coding agents when executing ingestion routines.
