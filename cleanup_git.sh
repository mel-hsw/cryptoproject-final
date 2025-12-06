#!/bin/bash
# Script to clean up Git tracking of files that should be ignored
# This removes files from Git index but keeps them on your filesystem

echo "Removing tracked files that should be gitignored..."

# Remove .DS_Store files from Git tracking
git rm --cached -r .DS_Store 2>/dev/null || true
find . -name ".DS_Store" -exec git rm --cached {} \; 2>/dev/null || true

# Remove __pycache__ directories
find . -type d -name "__pycache__" -exec git rm -r --cached {} \; 2>/dev/null || true

# Remove .pyc files
find . -name "*.pyc" -exec git rm --cached {} \; 2>/dev/null || true

# Remove processed data files (keep structure, remove data)
git rm --cached data/processed/*.parquet 2>/dev/null || true

# Remove MLflow database
git rm --cached docker/mlflow_data/mlflow.db 2>/dev/null || true

# Remove old deleted files from staging
git rm --cached docs/latency_report.rmd 2>/dev/null || true
git rm --cached docs/load_test_results.json 2>/dev/null || true
git rm --cached docs/runbook.md 2>/dev/null || true
git rm --cached docs/selection_rationale.md 2>/dev/null || true
git rm --cached docs/slo.md 2>/dev/null || true
git rm --cached docs/team_charter.md 2>/dev/null || true
git rm --cached pytest.ini 2>/dev/null || true
git rm --cached reports/evidently/Evidently_report.pdf 2>/dev/null || true

echo "✓ Files removed from Git tracking (but kept on filesystem)"
echo ""
echo "Next steps:"
echo "1. Review changes: git status"
echo "2. Commit the cleanup: git add .gitignore && git commit -m 'Clean up tracked artifacts and update gitignore'"
echo "3. These files will now be ignored in future commits"
