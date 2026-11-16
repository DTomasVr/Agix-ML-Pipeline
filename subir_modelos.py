"""Sube los modelos del paquete de producción a un repositorio de modelos PRIVADO de
Hugging Face, que es de donde el Space los descarga al arrancar.

    set HF_TOKEN=hf_...            (token de ESCRITURA; Windows)
    python subir_modelos.py <usuario>/agix-modelos [ruta a Agix-produccion/generacion]

Sube el contenido de generacion/ tal cual (src, models, data, estimador): son
~3,3 GB y puede tardar según la conexión. Si se corta, volver a lanzarlo: solo
sube lo que falte.
"""
import os
import sys
from pathlib import Path

from huggingface_hub import HfApi


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    repo = sys.argv[1]
    origen = Path(sys.argv[2] if len(sys.argv) > 2 else r"F:\Agix-produccion\generacion")
    if not (origen / "src" / "19_pipeline.py").exists():
        sys.exit(f"{origen} no parece la carpeta generacion/ del paquete")
    if not os.environ.get("HF_TOKEN"):
        sys.exit("falta la variable de entorno HF_TOKEN (token de escritura)")
    api = HfApi()
    api.create_repo(repo, repo_type="model", private=True, exist_ok=True)
    print(f"subiendo {origen} a {repo} (privado)...")
    api.upload_large_folder(repo_id=repo, repo_type="model", folder_path=str(origen),
                            ignore_patterns=["**/__pycache__/**", "*.pyc"])
    print("hecho")


if __name__ == "__main__":
    main()
