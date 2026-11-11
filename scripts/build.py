import os
import sys
import shutil
import zipfile
import subprocess
from pathlib import Path

ROOT = Path.cwd()
ZIP_PATH = ROOT / "input" / "project.zip"
WORK = ROOT / "work" / "project"
OUT = ROOT / "artifacts"
OUT.mkdir(parents=True, exist_ok=True)

def run(cmd, cwd=None):
    print("+", " ".join(map(str, cmd)), flush=True)
    subprocess.run(list(map(str, cmd)), cwd=cwd, check=True)

def extract_project():
    if not ZIP_PATH.exists():
        raise RuntimeError("Não encontrei input/project.zip. Envie o ZIP nesse caminho.")
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    with zipfile.ZipFile(ZIP_PATH) as z:
        for item in z.infolist():
            target = (WORK / item.filename).resolve()
            if WORK.resolve() not in target.parents and target != WORK.resolve():
                raise RuntimeError(f"ZIP inseguro: caminho inválido: {item.filename}")
        z.extractall(WORK)
    # Remove um único diretório externo, caso o ZIP tenha sido criado assim.
    entries = list(WORK.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        nested = entries[0]
        for p in list(nested.iterdir()):
            shutil.move(str(p), str(WORK / p.name))
        nested.rmdir()
    return WORK

def find_files(base, name):
    return list(base.rglob(name))

def copy_outputs(base):
    copied = 0
    patterns = [
        "**/*.apk", "**/*.aab", "**/*.ipa", "**/*.exe", "**/*.msi",
        "**/*.deb", "**/*.rpm", "**/*.whl", "**/*.zip"
    ]
    for pattern in patterns:
        for src in base.glob(pattern):
            if any(part in {".gradle", "node_modules", ".pub-cache"} for part in src.parts):
                continue
            if src.is_file():
                dest = OUT / src.name
                if dest.exists():
                    dest = OUT / f"{src.parent.name}-{src.name}"
                shutil.copy2(src, dest)
                copied += 1
    for dirname in ("dist", "build", "out", "web-build"):
        for src in base.rglob(dirname):
            if src.is_dir() and not any(p in {"node_modules", ".gradle"} for p in src.parts):
                dest = OUT / f"{src.parent.name or 'project'}-{dirname}"
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(src, dest, ignore=shutil.ignore_patterns("node_modules", ".gradle", ".git"))
                copied += 1
    return copied

def build(base):
    # Flutter tem prioridade sobre detecção genérica de Dart.
    if (base / "pubspec.yaml").exists() and (base / "lib").exists():
        print("Projeto detectado: Flutter")
        run(["flutter", "pub", "get"], cwd=base)
        run(["flutter", "build", "apk", "--release"], cwd=base)
        if (base / "web").exists():
            run(["flutter", "build", "web", "--release"], cwd=base)
        return "Flutter"

    # Gradle Android/Java/Kotlin.
    gradle_files = list(base.glob("settings.gradle*")) + list(base.glob("build.gradle*"))
    gradlew = base / ("gradlew.bat" if os.name == "nt" else "gradlew")
    android_dir = base / "android"
    if (base / "package.json").exists() and android_dir.exists() and (android_dir / "gradlew").exists():
        print("Projeto detectado: React Native Android")
        pkg = __import__("json").loads((base / "package.json").read_text(encoding="utf-8"))
        if (base / "package-lock.json").exists():
            run(["npm", "ci"], cwd=base)
        else:
            run(["npm", "install"], cwd=base)
        run(["npm", "run", "build"], cwd=base) if pkg.get("scripts", {}).get("build") else None
        rn_gradlew = android_dir / ("gradlew.bat" if os.name == "nt" else "gradlew")
        if os.name != "nt":
            rn_gradlew.chmod(0o755)
        run([str(rn_gradlew), "assembleRelease", "--no-daemon"], cwd=android_dir)
        return "React Native Android"

    if gradle_files or gradlew.exists():
        print("Projeto detectado: Gradle/Android/Java/Kotlin")
        if gradlew.exists():
            if os.name != "nt":
                gradlew.chmod(0o755)
            run([str(gradlew), "assembleRelease", "--no-daemon"], cwd=base)
        else:
            run(["gradle", "assembleRelease", "--no-daemon"], cwd=base)
        return "Gradle"

    # Node / Web.
    if (base / "package.json").exists():
        print("Projeto detectado: Node.js/Web")
        import json
        pkg = json.loads((base / "package.json").read_text(encoding="utf-8"))
        run(["npm", "ci"] if (base / "package-lock.json").exists() else ["npm", "install"], cwd=base)
        if pkg.get("scripts", {}).get("build"):
            run(["npm", "run", "build"], cwd=base)
        else:
            print("Aviso: package.json não possui script 'build'; dependências instaladas.")
        return "Node/Web"

    # Python.
    if (base / "pyproject.toml").exists() or (base / "setup.py").exists():
        print("Projeto detectado: Python")
        run([sys.executable, "-m", "pip", "install", "--upgrade", "build"], cwd=base)
        run([sys.executable, "-m", "build", "--wheel"], cwd=base)
        return "Python"

    # CMake native project.
    if (base / "CMakeLists.txt").exists():
        print("Projeto detectado: CMake (nativo)")
        run(["cmake", "-S", ".", "-B", "build", "-DCMAKE_BUILD_TYPE=Release"], cwd=base)
        run(["cmake", "--build", "build", "--config", "Release", "--parallel"], cwd=base)
        return "CMake"

    raise RuntimeError(
        "Tipo de projeto não reconhecido. Esperava pubspec.yaml, settings.gradle/build.gradle, "
        "package.json, pyproject.toml/setup.py ou CMakeLists.txt."
    )

if __name__ == "__main__":
    try:
        project = extract_project()
        kind = build(project)
        count = copy_outputs(project)
        (OUT / "build-summary.txt").write_text(
            f"Tipo detectado: {kind}\nArquivos/pastas de saída copiados: {count}\n",
            encoding="utf-8"
        )
        if count == 0:
            print("AVISO: build terminou, mas nenhum artefato conhecido foi encontrado.")
        print(f"BUILD FINALIZADO: {kind}; itens coletados: {count}")
    except Exception as exc:
        (OUT / "build-error.txt").write_text(str(exc), encoding="utf-8")
        print(f"ERRO DE COMPILAÇÃO: {exc}", file=sys.stderr)
        raise
