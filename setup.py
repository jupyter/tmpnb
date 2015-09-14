from distutils.core import setup
import sys

if "develop" in sys.argv or any(arg.startswith("bdist") for arg in sys.argv):
    import setuptools

setup_args = dict(
    name="tmpnb",
    version="0.1.0",
    description="Tool for aunching temporary Jupyter notebook servers",
    url="https://github.com/jupyter/tmpnb",
    license="BSD",
    author="Jupyter Development Team",
    author_email="jupyter@googlegroups.com",
    platforms="Linux, Mac OS X"
)

with open("requirements.txt") as required:
    setup_args["install_requires"] = required.read().splitlines()

if __name__ == "__main__":
    setup(**setup_args)
