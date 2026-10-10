from setuptools import setup

# Minimale setup.py, da die Konfiguration in pyproject.toml definiert ist
setup(
    packages=["github_collector"],
    package_dir={"": "src"},
)
