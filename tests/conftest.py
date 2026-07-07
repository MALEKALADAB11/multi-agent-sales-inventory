"""Configuration pytest — la racine du repo est le seul chemin nécessaire."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
