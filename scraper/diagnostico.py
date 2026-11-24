"""Descarga el texto de las convocatorias vigentes a las que les falta algún dato (uso puntual para mejorar el programa)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import actualizar as A  # noqa: E402

os.makedirs("diag", exist_ok=True)
reg = A.carga(A.F_REG, {})
out = A.carga(A.F_OUT, {})
indice = []
for c in out.get("convocatorias", []):
    r = reg.get(c["id"])
    if not r or (r.get("requisitos") and r.get("grupo") and r.get("titulacion")):
        continue
    try:
        if r["fuente"] == "BOE":
            texto, notas = A.boe_texto(r["id"])
            texto = "\n".join(notas) + "\n" + texto
        elif r["fuente"] == "BOJA":
            texto = A.html_a_texto(A.descarga(r["url_bases"]) or "")
        else:
            datos = A.descarga(r["url_pdf"], binario=True) if r.get("url_pdf") else None
            texto = A.pdf_a_texto(datos) if datos else ""
            if A.letras_utiles(texto) < 400 and datos:
                texto = "[OCR]\n" + A.pdf_ocr(datos)
    except Exception as e:  # noqa: BLE001
        texto = f"[ERROR] {e}"
    nombre = c["id"].replace("/", "_")
    with open(f"diag/{nombre}.txt", "w", encoding="utf-8") as f:
        f.write(c["titulo_oficial"] + "\n\n" + texto)
    indice.append({"id": c["id"], "fuente": r["fuente"], "req": bool(r.get("requisitos")), "grupo": r.get("grupo"),
                   "tit": r.get("titulacion"), "titulo": c["titulo_oficial"]})
    print(c["id"], len(texto), flush=True)
with open("diag/indice.json", "w", encoding="utf-8") as f:
    json.dump(indice, f, ensure_ascii=False, indent=1)
