# openkg-webui-cli

CLI-only OPENKG-WebUI distribution. It installs the `openkg-webui` command and the
Python modules required for terminal workflows, RAG, document parsing, and model
provider integrations, but it does not ship the packaged Next.js Web assets or
FastAPI/Uvicorn server dependencies used by `openkg-webui start`.

Install from the repository root when you want a local CLI-only environment:

```bash
python3 -m venv .venv-cli
source .venv-cli/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ./packaging/openkg-webui-cli
```

Keep the checkout in place after installation because editable installs point
the `openkg-webui` command at these source files.
