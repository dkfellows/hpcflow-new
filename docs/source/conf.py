# Configuration file for the Sphinx documentation builder.
#
# This file only contains a selection of the most common options. For a full
# list see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Path setup --------------------------------------------------------------

# If extensions (or modules to document with autodoc) are in another directory,
# add these directories to sys.path here. If the directory is relative to the
# documentation root, use os.path.abspath to make it absolute, like shown here.
#
# import os
# import sys
# sys.path.insert(0, os.path.abspath('.'))

from pathlib import Path
from textwrap import indent

from ruamel.yaml import YAML

from hpcflow import __version__

# -- Project information -----------------------------------------------------

project = "hpcflow"
copyright = "2022, hpcflow developers"
author = "hpcflow developers"

# The full version, including alpha/beta/rc tags
release = __version__


# -- General configuration ---------------------------------------------------

# Add any Sphinx extension module names here, as strings. They can be
# extensions coming with Sphinx (named 'sphinx.ext.*') or your custom
# ones.
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx_jinja",
    "sphinx_copybutton",
]

autodoc_typehints = "description"

# Add any paths that contain templates here, relative to this directory.
templates_path = ["_templates"]

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
# This pattern also affects html_static_path and html_extra_path.
exclude_patterns = []


# -- Options for HTML output -------------------------------------------------

# The theme to use for HTML and HTML Help pages.  See the documentation for
# a list of builtin themes.
#
html_theme = "pydata_sphinx_theme"
html_theme_options = {
    "logo_link": "https://hpcflow.github.io",
    "github_url": "https://github.com/hpcflow/hpcflow-new",
    "external_links": [],
    "switcher": {
        "json_url": "https://hpcflow.github.io/docs/switcher.json",
        "url_template": "https://hpcflow.github.io/docs/v{version}/",
        "version_match": __version__,
    },
    "navbar_end": ["version-switcher", "navbar-icon-links.html"],
    "use_edit_page_button": True,
}

html_context = {
    "github_user": "hpcflow",
    "github_repo": "hpcflow-new",
    "github_version": "develop",
    "doc_path": "docs/source",
}
html_logo = "_static/images/logo-v2.png"

# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named "default.css" will overwrite the builtin "default.css".
html_static_path = ["_static"]

# These paths are either relative to html_static_path
# or fully qualified paths (eg. https://...)
html_css_files = ["css/custom.css"]

text_newlines = "unix"

# Get just-released binaries:
yaml = YAML()
with Path("released_binaries.yml") as fh:
    bins_dat = yaml.load(fh)

# Generate install/index.rst file programmatically, including binary download links:

EXE_PLAT_LOOKUP = {
    "win.exe": "Windows executable",
    "macOS": "macOS executable",
    "linux": "Linux executable",
    "win-dir.zip": "Windows folder",
    "linux-dir.zip": "Linux folder",
    "macOS-dir.zip": "macOS folder",
}

get_links_table = (
    '<table class="binary-downloads-table">\n'
    + indent(
        text="\n".join(
            f'<tr><td>{EXE_PLAT_LOOKUP["-".join(exe_name.split("-")[2:])]}</td><td><a href="{link}">{exe_name}</a></td></tr>'
            for exe_name, link in sorted(bins_dat.items())
        ),
        prefix="  ",
    )
    + "\n</table>"
)

install_index = f"""
:orphan:

.. _install:

############
Installation
############

Release notes: `on GitHub <https://github.com/hpcflow/hpcflow-new/releases/tag/v{release}>`_

There are two ways of using HPCFlow:
 * HPCFlow CLI (Command Line Interface)
 * The HPCFlow Python package

Both of these options allow workflows to be designed and executed. HPCFlow CLI
is recommended for beginners and strongly recommended if you want to 
run HPCFlow on a cluster. The HPCFlow Python package allows workflows to be
designed and explored via the Python API and is recommended for users 
comfortable working with Python. If you are interested in contributing to 
the development of HPCFlow, the Python package is the place to start.

HPCFlow CLI and the HPCFlow Python package can both be used simultaneously.

*******************************
HPCFlow CLI
*******************************

Install script (v{release})
===========================

HPCFlow CLI can be installed on macOS, Linux or Windows through a terminal
or shell prompt.

**macOS:** Open a terminal, paste the command shown below and press enter.

```bash
(touch tmp.sh && curl -fsSL https://raw.githubusercontent.com/hpcflow/install-scripts/main/src/install-hpcflow.sh > tmp.sh && bash tmp.sh --prerelease --path --onefile) ; rm tmp.sh
```

**Linux:** Open a shell prompt, paste the command shown below and press enter.

```bash
(touch tmp.sh && curl -fsSL https://raw.githubusercontent.com/hpcflow/install-scripts/main/src/install-hpcflow.sh > tmp.sh && bash tmp.sh --prerelease --path --onefile) ; rm tmp.sh
```

**Windows:** Open a Powershell terminal, paste the command shown below and 
press enter.

```bash
& $([scriptblock]::Create((New-Object Net.WebClient).DownloadString('https://raw.githubusercontent.com/hpcflow/install-scripts/main/src/install-hpcflow.ps1'))) -PreRelease -OneFile
```

Download binaries (v{release})
===============================

Binaries are available in two formats:

  * A single executable file containing everything.
  * A folder containing an executable and supporting files.

Click below to download the HPCFlow binary for your platform (other binary releases are available by using the version switcher in the top-right corner):

.. raw:: html

{indent(get_links_table, '   ')}

**************************
The HPCFlow Python package
**************************

Using pip
==========================

Use pip to install the Python package from PyPI::

  pip install hpcflow=={release}

"""
with Path("install/index.rst").open("w", newline="\n") as fh:
    fh.write(install_index)
