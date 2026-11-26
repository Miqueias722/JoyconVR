"""
Atualizacao automatica do JoyconVR pelo GitHub Releases.

Como funciona:
  1. Ao abrir, a janela pergunta ao GitHub qual e a ultima versao publicada.
  2. Se for mais nova que VERSION, mostra um aviso com o botao "Atualizar".
  3. Ao clicar: baixa o JoyconVR.zip da versao, fecha o SteamVR (se aberto) e roda um
     .bat que espera esta janela fechar, copia os arquivos novos por cima e abre de novo.

Os arquivos de configuracao da pessoa (config.json, joyconcal.json, joycon2_config.json)
NAO sao substituidos, para nao perder os ajustes dela.
"""

import json
import os
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

VERSION = "1.2.0"
REPO = "Miqueias722/JoyconVR"   # dono/repositorio no GitHub
ASSET_NAME = "JoyconVR.zip"
KEEP = {"joyconvr/config/config.json", "joyconvr/config/joyconcal.json", "joycon2_config.json"}


def _parse(v):
    out = []
    for part in v.strip().lstrip("vV").split("."):
        digits = "".join(c for c in part if c.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out + [0] * (3 - len(out)))


def check_latest(timeout=6):
    """Retorna (versao, url_do_zip, notas) se houver versao mais nova, senao None."""
    if REPO.startswith("OWNER/"):
        return None
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "JoyconVR-updater"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    tag = data.get("tag_name", "")
    if _parse(tag) <= _parse(VERSION):
        return None
    for a in data.get("assets", []):
        if a.get("name") == ASSET_NAME:
            return tag.lstrip("vV"), a["browser_download_url"], (data.get("body") or "")[:600]
    return None


def download(url, progress=None):
    dest = os.path.join(tempfile.gettempdir(), "JoyconVR-update.zip")
    req = urllib.request.Request(url, headers={"User-Agent": "JoyconVR-updater"})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(64 * 1024)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress and total:
                progress(done / total)
    with zipfile.ZipFile(dest) as z:          # confere se o arquivo veio inteiro
        bad = z.testzip()
        if bad:
            raise RuntimeError(f"arquivo baixado com defeito ({bad})")
    return dest


def close_steamvr():
    for name in ("vrmonitor.exe", "vrserver.exe", "vrcompositor.exe", "vrdashboard.exe", "vrwebhelper.exe"):
        subprocess.run(["taskkill", "/IM", name, "/F"], capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def apply_and_restart(zip_path, app_dir, exe_path):
    """Extrai numa pasta temporaria e deixa um .bat copiar por cima depois que fecharmos."""
    stage = os.path.join(tempfile.gettempdir(), "JoyconVR-update")
    if os.path.isdir(stage):
        subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", stage], capture_output=True)
    os.makedirs(stage)
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        root = names[0].split("/")[0] + "/" if names and all(n.startswith(names[0].split("/")[0] + "/") for n in names) else ""
        for n in names:
            rel = n[len(root):]
            if not rel or rel.endswith("/"):
                continue
            if rel.replace("\\", "/") in KEEP and os.path.exists(os.path.join(app_dir, rel)):
                continue
            target = os.path.join(stage, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(n) as src, open(target, "wb") as dst:
                dst.write(src.read())
    bat = os.path.join(tempfile.gettempdir(), "JoyconVR-update.bat")
    pid = os.getpid()
    with open(bat, "w", encoding="ascii", errors="replace") as f:
        f.write(f"""@echo off
title Atualizando o JoyconVR...
echo Atualizando o JoyconVR, so um instante...
:wait
tasklist /FI "PID eq {pid}" | find "{pid}" >nul && (timeout /t 1 >nul & goto wait)
timeout /t 1 >nul
robocopy "{stage}" "{app_dir}" /E /NFL /NDL /NJH /NJS /R:5 /W:1 >nul
start "" "{exe_path}"
del "%~f0"
""")
    subprocess.Popen(["cmd", "/c", bat], creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
