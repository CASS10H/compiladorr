import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ZIP_PATH = ROOT / "input" / "project.zip"
WORK = ROOT / "work" / "project"
OUT = ROOT / "artifacts"
LOGS = OUT / "logs"

OUT.mkdir(parents=True, exist_ok=True)
LOGS.mkdir(parents=True, exist_ok=True)

SKIP_DIRS = {".git", ".gradle", "node_modules", ".pub-cache", "__pycache__"}

def log(*parts):
    print(*parts, flush=True)

def run(cmd, cwd=None):
    cmd = [str(x) for x in cmd]
    log("+", subprocess.list2cmdline(cmd))
    # Windows batch files (.bat/.cmd) must be launched through cmd.exe.
    if os.name == "nt" and cmd and Path(cmd[0]).suffix.lower() in {".bat", ".cmd"}:
        cmd = ["cmd.exe", "/d", "/c", subprocess.list2cmdline(cmd)]
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)

def safe_extract():
    if not ZIP_PATH.is_file():
        raise RuntimeError("Arquivo ausente: coloque o projeto em input/project.zip")

    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(ZIP_PATH) as archive:
        root = WORK.resolve()
        for item in archive.infolist():
            # Reject absolute paths, traversal and Windows drive paths.
            name = item.filename.replace("\\", "/")
            target = (WORK / name).resolve()
            if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
                raise RuntimeError(f"ZIP inseguro: caminho absoluto: {item.filename}")
            if target != root and root not in target.parents:
                raise RuntimeError(f"ZIP inseguro: caminho inválido: {item.filename}")
        archive.extractall(WORK)

    entries = list(WORK.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        nested = entries[0]
        for child in list(nested.iterdir()):
            shutil.move(str(child), str(WORK / child.name))
        nested.rmdir()
    return WORK

def has_gradle(base):
    return any(base.glob("*.gradle")) or any(base.glob("*.gradle.kts")) or \
           (base / "gradlew").exists() or (base / "gradlew.bat").exists()

def npm_install(base):
    if (base / "package-lock.json").exists():
        run(["npm", "ci"], cwd=base)
    elif (base / "yarn.lock").exists():
        run(["corepack", "yarn", "install"], cwd=base)
    elif (base / "pnpm-lock.yaml").exists():
        run(["corepack", "pnpm", "install"], cwd=base)
    else:
        run(["npm", "install"], cwd=base)

def build_flutter(base):
    log("Projeto detectado: Flutter")
    run(["flutter", "doctor", "-v"], cwd=base)
    run(["flutter", "pub", "get"], cwd=base)
    run(["flutter", "build", "apk", "--release"], cwd=base)
    if (base / "web").is_dir():
        run(["flutter", "build", "web", "--release"], cwd=base)
    return "Flutter"

def build_react_native(base):
    log("Projeto detectado: React Native Android")
    npm_install(base)
    android = base / "android"
    wrapper = android / ("gradlew.bat" if os.name == "nt" else "gradlew")
    if not wrapper.is_file():
        raise RuntimeError("Projeto React Native sem android/gradlew(.bat). Gere/adicone a pasta Android.")
    run([wrapper, "assembleRelease", "--no-daemon"], cwd=android)
    return "React Native Android"

def build_gradle(base):
    log("Projeto detectado: Gradle / Android / Java / Kotlin")
    wrapper = base / ("gradlew.bat" if os.name == "nt" else "gradlew")
    if wrapper.is_file():
        if os.name != "nt":
            wrapper.chmod(0o755)
        run([wrapper, "assembleRelease", "--no-daemon"], cwd=base)
        # Also try AAB; APK remains available if this task is not configured.
        try:
            run([wrapper, "bundleRelease", "--no-daemon"], cwd=base)
        except subprocess.CalledProcessError:
            log("Aviso: bundleRelease não está configurado; mantendo os resultados existentes.")
    else:
        run(["gradle", "assembleRelease", "--no-daemon"], cwd=base)
    return "Gradle"

def build_node(base):
    log("Projeto detectado: Node.js / Web")
    pkg = json.loads((base / "package.json").read_text(encoding="utf-8"))
    npm_install(base)
    scripts = pkg.get("scripts", {})
    if "build" in scripts:
        run(["npm", "run", "build"], cwd=base)
    else:
        log("Aviso: package.json não possui script build.")
    return "Node/Web"

def build_python(base):
    log("Projeto detectado: Python")
    run([sys.executable, "-m", "pip", "install", "--upgrade", "build"], cwd=base)
    run([sys.executable, "-m", "build", "--wheel"], cwd=base)
    return "Python"

def build_cmake(base):
    log("Projeto detectado: CMake")
    run(["cmake", "-S", ".", "-B", "build", "-DCMAKE_BUILD_TYPE=Release"], cwd=base)
    run(["cmake", "--build", "build", "--config", "Release", "--parallel"], cwd=base)
    return "CMake"

def build(base):
    if (base / "pubspec.yaml").is_file() and (base / "lib").is_dir():
        return build_flutter(base)

    if (base / "package.json").is_file() and (base / "android").is_dir():
        return build_react_native(base)

    if has_gradle(base):
        return build_gradle(base)

    if (base / "package.json").is_file():
        return build_node(base)

    if (base / "pyproject.toml").is_file() or (base / "setup.py").is_file():
        return build_python(base)

    if (base / "CMakeLists.txt").is_file():
        return build_cmake(base)

    raise RuntimeError(
        "Projeto não reconhecido. Suportados: Flutter, React Native Android, "
        "Gradle/Android, Node/Web, Python e CMake."
    )

def collect_files(base):
    copied = []
    extensions = {".apk", ".aab", ".ipa", ".exe", ".msi", ".deb", ".rpm", ".whl"}
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in extensions:
            dest = OUT / path.name
            if dest.exists():
                dest = OUT / f"{path.parent.name}-{path.name}"
            shutil.copy2(path, dest)
            copied.append(dest.name)

    # Common web/package output directories, copied without dependency folders.
    for dirname in ("dist", "web-build"):
        for src in base.rglob(dirname):
            if not src.is_dir() or any(part in SKIP_DIRS for part in src.parts):
                continue
            dest = OUT / f"{src.parent.name or 'project'}-{dirname}"
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(src, dest, ignore=shutil.ignore_patterns(*SKIP_DIRS))
            copied.append(dest.name)
    return copied

def main():
    try:
        project = safe_extract()
        kind = build(project)
        outputs = collect_files(project)
        summary = [
            f"Tipo detectado: {kind}",
            f"Arquivos/pastas coletados: {len(outputs)}",
            *outputs,
        ]
        (OUT / "build-summary.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")
        log("BUILD FINALIZADO:", kind, "| itens coletados:", len(outputs))
        if not outputs:
            log("AVISO: a compilação terminou, mas nenhum artefato conhecido foi encontrado.")
    except Exception as exc:
        (OUT / "build-error.txt").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        log(f"ERRO DE COMPILAÇÃO: {exc}")
        raise

if __name__ == "__main__":
    main()
