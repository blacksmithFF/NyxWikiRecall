"""Tests never touch a real Hermes home: every file the plugin writes by default lands in a temp folder."""

import os
import tempfile

os.environ["HERMES_HOME"] = tempfile.mkdtemp(prefix="nwr-test-home-")
