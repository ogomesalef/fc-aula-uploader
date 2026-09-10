# aula-uploader

Ferramenta local para criar capítulos, organizar vídeos e enviar aulas ao portal.

A pasta de vídeo no Bunny precisa existir antes; o capítulo pode ser criado ou reutilizado.

Precisa de **Python 3.10+**, **ffmpeg** e login de **admin** do portal.

---

## macOS / Linux

```bash
# 1. ffmpeg (só se ainda não tiver)
brew install ffmpeg          # macOS
# sudo apt install ffmpeg    # Ubuntu/Debian

# 2. instalar
git clone https://github.com/ogomesalef/fc-aula-uploader.git
cd fc-aula-uploader
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip setuptools wheel
pip install -e .

# 3. abrir
aula-uploader web
```

Abre http://127.0.0.1:8787/

Para atualizar depois:

```bash
cd fc-aula-uploader
git pull
source .venv/bin/activate
pip install -U pip setuptools wheel
pip install -e .
aula-uploader web
```

---

## Windows

No PowerShell:

```powershell
# 1. ffmpeg (só se ainda não tiver)
winget install -e --id Gyan.FFmpeg

# 2. instalar
git clone https://github.com/ogomesalef/fc-aula-uploader.git
cd fc-aula-uploader
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -U pip setuptools wheel
pip install -e .

# 3. abrir
aula-uploader web
```

Abre http://127.0.0.1:8787/

Na tela de vídeos, cole o caminho da pasta (ex.: `C:\Users\...\aulas`).

Para atualizar depois:

```powershell
cd fc-aula-uploader
git pull
.\.venv\Scripts\Activate.ps1
pip install -U pip setuptools wheel
pip install -e .
aula-uploader web
```

---

MIT
