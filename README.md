# aula-uploader

Ferramenta local para criar capítulos, organizar vídeos e enviar aulas ao portal.

A pasta de vídeo no Bunny precisa existir antes; o capítulo pode ser criado ou reutilizado.

Precisa de **Python 3.10+**, **ffmpeg** e login de **admin** do portal.

---

## macOS / Linux

```bash
git clone https://github.com/ogomesalef/fc-aula-uploader.git
cd fc-aula-uploader
bash install.sh
source .venv/bin/activate
aula-uploader web
```

Abre http://127.0.0.1:8787/

Atualizar depois:

```bash
cd fc-aula-uploader
git pull
bash install.sh
source .venv/bin/activate
aula-uploader web
```

---

## Windows

No PowerShell:

```powershell
git clone https://github.com/ogomesalef/fc-aula-uploader.git
cd fc-aula-uploader
.\install.ps1
.\.venv\Scripts\Activate.ps1
aula-uploader web
```

Abre http://127.0.0.1:8787/

Na tela de vídeos, cole o caminho da pasta (ex.: `C:\Users\...\aulas`).

Atualizar depois:

```powershell
cd fc-aula-uploader
git pull
.\install.ps1
.\.venv\Scripts\Activate.ps1
aula-uploader web
```

---

MIT
