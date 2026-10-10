"""The tools HOOD may install to accomplish a mission (only with the owner's permission).

Owner's rules (2026-10-10):
- installs happen only inside WSL2 / Linux, never on Windows itself;
- HOOD asks once per tool; after that it may reuse and update that tool without asking;
- HOOD never answers "no" when a tool listed here would make the work possible: it asks.

Every source is official. System packages come from Ubuntu's signed repositories, and only the
packages listed here are ever installed (also enforced by scripts/wsl/hood-pkg). Downloads
are checked against the checksums their publisher serves over HTTPS before anything is unpacked.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class Tool:
    id: str
    name: str
    purpose: str
    kind: str                       # "apt" | "archive" | "file" | "wp_plugin"
    size: str
    source: str
    packages: Tuple[str, ...] = ()  # apt
    binary: Optional[str] = None    # apt: command that proves it is installed
    php_modules: Tuple[str, ...] = ()
    url: Optional[str] = None       # archive / file
    checksum_url: Optional[str] = None
    algo: Optional[str] = None
    slug: Optional[str] = None      # wp_plugin
    hosts: Tuple[str, ...] = ()
    requires: Tuple[str, ...] = ()


TOOLS: Dict[str, Tool] = {t.id: t for t in (
    Tool("php", "PHP", "Runs PHP websites such as WordPress", "apt", "about 30 MB",
         "Ubuntu's signed package repositories",
         packages=("php-cli", "php-sqlite3", "php-mbstring", "php-xml", "php-curl", "php-gd", "php-zip",
                   "php-intl"),
         binary="php", php_modules=("pdo_sqlite", "sqlite3", "mbstring", "xml", "dom")),
    Tool("wordpress", "WordPress", "The WordPress software itself, for WordPress site missions", "archive",
         "about 35 MB", "wordpress.org",
         url="https://wordpress.org/latest.tar.gz", checksum_url="https://wordpress.org/latest.tar.gz.sha1",
         algo="sha1", hosts=("wordpress.org",), requires=("php",)),
    Tool("wp_sqlite", "WordPress SQLite plugin", "Lets WordPress keep its data in a file, so no database "
         "server is needed", "wp_plugin", "about 1 MB", "wordpress.org plugin directory (official plugin)",
         slug="sqlite-database-integration", hosts=("api.wordpress.org", "downloads.wordpress.org"),
         requires=("wordpress",)),
    Tool("wp_cli", "WP-CLI", "Sets up WordPress and adds the pages the agents wrote", "file", "about 7 MB",
         "the WP-CLI project (github.com/wp-cli)",
         url="https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar",
         checksum_url="https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar.sha512",
         algo="sha512", hosts=("raw.githubusercontent.com",), requires=("php",)),
    Tool("sqlite3", "SQLite command line", "Inspect SQLite databases", "apt", "about 2 MB",
         "Ubuntu's signed package repositories", packages=("sqlite3",), binary="sqlite3"),
    Tool("mariadb", "MariaDB (MySQL)", "A MySQL-compatible database server, for projects that need one",
         "apt", "about 200 MB", "Ubuntu's signed package repositories",
         packages=("mariadb-server", "mariadb-client"), binary="mariadb"),
    Tool("nodejs", "Node.js and npm", "Runs JavaScript tools and frameworks", "apt", "about 80 MB",
         "Ubuntu's signed package repositories", packages=("nodejs", "npm"), binary="node"),
    Tool("composer", "Composer", "Installs PHP libraries", "apt", "about 5 MB",
         "Ubuntu's signed package repositories", packages=("composer",), binary="composer", requires=("php",)),
)}

# What each mission kind needs on this computer.
PROFILE_TOOLS: Dict[str, Tuple[str, ...]] = {
    "wordpress_site": ("php", "wordpress", "wp_sqlite", "wp_cli"),
}


def with_dependencies(tool_ids: List[str]) -> List[str]:
    """Tools in install order (dependencies first); unknown ids raise KeyError."""
    order: List[str] = []

    def visit(tid: str, path: Tuple[str, ...] = ()) -> None:
        if tid in path:
            raise ValueError(f"Dependency cycle at {tid}")
        tool = TOOLS[tid]
        for dep in tool.requires:
            visit(dep, path + (tid,))
        if tid not in order:
            order.append(tid)
    for tid in tool_ids:
        visit(tid)
    return order


def apt_packages() -> List[str]:
    return sorted({p for t in TOOLS.values() for p in t.packages})
