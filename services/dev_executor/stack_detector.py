"""
HOOD Development Executor - Stack Detector
Analyzes projects to discover runtime, framework, dependencies, package managers, and test runners.
Governed by Master System Specification Section 9, 15 & V0.3 Development Executor Spec.
"""

import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field


class ProjectStackInfo(BaseModel):
    project_root: str
    primary_language: str
    framework: Optional[str] = None
    package_manager: Optional[str] = None
    manifest_files: List[str] = Field(default_factory=list)
    has_virtualenv: bool = False
    virtualenv_path: Optional[str] = None
    test_framework: Optional[str] = None
    dev_server_command: Optional[str] = None
    default_port: Optional[int] = None
    entrypoints: List[str] = Field(default_factory=list)
    git_tracked: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


class StackDetector:
    """Discovers project architecture, language, framework, test tools, and service entrypoints."""

    @staticmethod
    def inspect(project_path: Path) -> ProjectStackInfo:
        project_path = project_path.resolve()
        manifest_files: List[str] = []
        entrypoints: List[str] = []
        metadata: Dict[str, Any] = {}

        # 1. Check Git tracking
        git_tracked = (project_path / ".git").is_dir()

        # 2. Check virtualenvs
        has_virtualenv = False
        virtualenv_path = None
        for venv_name in [".venv", "venv", "env"]:
            v_candidate = project_path / venv_name
            if v_candidate.is_dir() and ((v_candidate / "Scripts" / "python.exe").exists() or (v_candidate / "bin" / "python").exists()):
                has_virtualenv = True
                virtualenv_path = str(v_candidate)
                break

        # 3. Detect Python Stack
        is_python = False
        primary_language = "unknown"
        framework = None
        package_manager = None
        test_framework = None
        dev_server_command = None
        default_port = None

        pyproject = project_path / "pyproject.toml"
        requirements = project_path / "requirements.txt"
        setup_py = project_path / "setup.py"
        pipfile = project_path / "Pipfile"

        package_json = project_path / "package.json"

        if pyproject.exists():
            manifest_files.append("pyproject.toml")
            is_python = True
            package_manager = "pip/pyproject"
        if requirements.exists():
            manifest_files.append("requirements.txt")
            is_python = True
            package_manager = package_manager or "pip"
        if setup_py.exists():
            manifest_files.append("setup.py")
            is_python = True
        if pipfile.exists():
            manifest_files.append("Pipfile")
            is_python = True
            package_manager = "pipenv"

        # Check Python files
        py_files = list(project_path.glob("*.py")) + list(project_path.glob("*/*.py"))
        if py_files or is_python:
            is_python = True
            primary_language = "python"
            test_framework = "pytest"

            # Check framework signatures
            req_content = ""
            if requirements.exists():
                try:
                    req_content = requirements.read_text(encoding="utf-8").lower()
                except Exception:
                    pass

            for py_f in py_files[:15]:
                try:
                    text = py_f.read_text(encoding="utf-8", errors="ignore").lower()
                    if "from fastapi import" in text or "import fastapi" in text or "fastapi" in req_content:
                        framework = "FastAPI"
                        dev_server_command = "uvicorn main:app --port 8000"
                        default_port = 8000
                        break
                    elif "from flask import" in text or "import flask" in text or "flask" in req_content:
                        framework = "Flask"
                        dev_server_command = "flask run --port 5000"
                        default_port = 5000
                        break
                    elif "django" in text or "django" in req_content:
                        framework = "Django"
                        dev_server_command = "python manage.py runserver 8000"
                        default_port = 8000
                        break
                    elif "http.server" in text or "simple_server" in text:
                        framework = "http.server"
                        dev_server_command = f"python {py_f.name}"
                        default_port = 8080
                except Exception:
                    pass

        # 4. Detect Node / JavaScript / TypeScript Stack
        if package_json.exists():
            manifest_files.append("package.json")
            if not is_python or len(manifest_files) == 1:
                primary_language = "javascript"
                package_manager = "npm"
                try:
                    pkg_data = json.loads(package_json.read_text(encoding="utf-8"))
                    deps = {**pkg_data.get("dependencies", {}), **pkg_data.get("devDependencies", {})}
                    scripts = pkg_data.get("scripts", {})

                    if "next" in deps:
                        framework = "Next.js"
                        default_port = 3000
                    elif "express" in deps:
                        framework = "Express"
                        default_port = 3000
                    elif "react" in deps:
                        framework = "React"
                        default_port = 3000
                    elif "vue" in deps:
                        framework = "Vue"
                        default_port = 5173
                    elif "vite" in deps:
                        framework = "Vite"
                        default_port = 5173

                    if "jest" in deps or "jest" in scripts.get("test", ""):
                        test_framework = "jest"
                    elif "vitest" in deps or "vitest" in scripts.get("test", ""):
                        test_framework = "vitest"
                    elif "mocha" in deps:
                        test_framework = "mocha"

                    if "dev" in scripts:
                        dev_server_command = "npm run dev"
                    elif "start" in scripts:
                        dev_server_command = "npm start"
                except Exception as e:
                    metadata["package_json_error"] = str(e)

        # Entrypoints search
        common_entrypoints = ["main.py", "app.py", "server.py", "index.js", "server.js", "app.js", "index.html"]
        for ep in common_entrypoints:
            if (project_path / ep).exists():
                entrypoints.append(ep)

        return ProjectStackInfo(
            project_root=str(project_path),
            primary_language=primary_language,
            framework=framework,
            package_manager=package_manager,
            manifest_files=manifest_files,
            has_virtualenv=has_virtualenv,
            virtualenv_path=virtualenv_path,
            test_framework=test_framework,
            dev_server_command=dev_server_command,
            default_port=default_port,
            entrypoints=entrypoints,
            git_tracked=git_tracked,
            metadata=metadata
        )
