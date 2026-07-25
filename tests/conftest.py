import os

# The database name, user, and password are required settings with no defaults.
# Unit tests never open a real connection, so provide throwaway values before the
# package (and its settings singleton) is imported.
os.environ.setdefault("PG_DB", "test_db")
os.environ.setdefault("PG_USER", "test_user")
os.environ.setdefault("PG_PASSWORD", "test_password")
