"""Point the app at a throwaway /data before any app module is imported."""
import os
import tempfile

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="ise-ndg-sync-test-"))
