# Space de Hugging Face (sdk: docker). La imagen solo lleva el código y las
# dependencias; los modelos (3,3 GB) se descargan al arrancar desde un repositorio
# de modelos privado (AGIX_MODEL_REPO, con HF_TOKEN como secreto del Space).
FROM python:3.12-slim

# Hugging Face ejecuta el contenedor con el usuario 1000
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    AGIX_DEVICE=cpu \
    AGIX_CACHE=/home/user/modelos \
    TOKENIZERS_PARALLELISM=false
WORKDIR /home/user/app

# torch se instala aparte, con la rueda de CPU (la de CUDA pesa ~2 GB más y aquí no
# hay GPU)
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --user torch==2.11.0 --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir --user -r requirements.txt

COPY --chown=user app ./app

EXPOSE 7860
# un solo proceso: cada uno carga sus propios modelos (~5 GB de RAM)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860", "--workers", "1"]
