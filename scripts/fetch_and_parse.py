import requests, re, json, os, csv, sys, io

OUTPUT_DIR = "output"

def get_latest_post():
    resp = requests.get(
        "https://www.cmparana.com.ar/wp-json/wp/v2/posts",
        params={"search": "Estado de Obras Sociales", "per_page": 5, "orderby": "date", "order": "desc"},
        timeout=30,
    )
    resp.raise_for_status()
    posts = resp.json()
    for post in posts:
        html = post.get("content", {}).get("rendered", "")
        m = re.search(r'href="([^"]+\.pdf)"', html)
        if m:
            return {
                "pdf_url": m.group(1),
                "post_title": re.sub("<[^<]+?>", "", post["title"]["rendered"]).strip(),
                "post_link": post["link"],
                "post_date": post["date"],
            }
    return None

def parse_valor_consulta(raw):
    if not raw:
        return {"valor_base": None, "valor_especialista": None, "incluye_iva": False, "raw": raw}
    incluye_iva = bool(re.search(r"iva", raw, re.IGNORECASE))
    numeros = re.findall(r"\d[\d.,]*", raw)
    def to_float(n):
        return float(n.replace(".", "").replace(",", "."))
    numeros = [to_float(n) for n in numeros]
    return {
        "valor_base": numeros[0] if len(numeros) >= 1 else None,
        "valor_especialista": numeros[1] if len(numeros) >= 2 else (numeros[0] if numeros else None),
        "incluye_iva": incluye_iva,
        "raw": raw.strip(),
    }

def _clean(cell):
    if cell is None:
        return ""
    return str(cell).replace("\n", " ").strip()

def parse_pdf(pdf_bytes):
    import pdfplumber
    main_rows = []
    sin_atencion_rows = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        todas_las_filas = []
        for page in pdf.pages:
            for table in page.extract_tables():
                todas_las_filas.extend(table)

        modo_actual = None
        for row in todas_las_filas:
            row = [_clean(c) for c in row]
            primera = row[0].upper() if row else ""
            segunda = row[1].upper() if len(row) > 1 else ""
            if primera.startswith("CÓD"):
                if segunda.startswith("NOMBRE"):
                    modo_actual = "main"
                elif segunda.startswith("OBRA SOCIAL"):
                    modo_actual = "sin_atencion"
                else:
                    modo_actual = None
                continue
            if modo_actual == "main":
                main_rows.append(row)
            elif modo_actual == "sin_atencion":
                sin_atencion_rows.append(row)

        activas = []
        sin_atencion = []
        for row in main_rows:
            row = [_clean(c) for c in row]
            codigo = row[0] if row else ""
            if not codigo or not codigo.isdigit():
                continue
            (codigo, nombre, nombre_completo, cos_consulta, coseguro_practica,
             valor_consulta, observaciones, arancel_comp) = (row + [""] * 8)[:8]
            valor = parse_valor_consulta(valor_consulta)
            activas.append({
                "codigo": codigo, "nombre": nombre, "nombre_completo": nombre_completo,
                "coseguro_consulta": cos_consulta, "coseguro_practica": coseguro_practica,
                "valor_consulta_raw": valor_consulta, "valor_base": valor["valor_base"],
                "valor_especialista": valor["valor_especialista"], "incluye_iva": valor["incluye_iva"],
                "observaciones": observaciones, "arancel_compensatorio": arancel_comp, "estado": "Activa",
            })
        for row in sin_atencion_rows:
            row = [_clean(c) for c in row]
            codigo = row[0] if row else ""
            if not codigo or not codigo.isdigit():
                continue
            codigo, obra_social, motivo = (row + [""] * 3)[:3]
            if motivo.startswith(")"):
                obra_social = (obra_social + ")").strip()
                motivo = motivo[1:].strip()
            sin_atencion.append({"codigo": codigo, "nombre": obra_social, "motivo": motivo, "estado": "Sin_Atencion"})
    return activas, sin_atencion


def write_csv(path, activas, sin_atencion, post_title):
    header = ["Codigo","Nombre","Nombre_Completo","Coseguro_Consulta","Coseguro_Practica",
              "Valor_Consulta_Raw","Valor_Base","Valor_Especialista","Incluye_IVA","Observaciones",
              "Arancel_Compensatorio","Estado","Motivo_Sin_Atencion","Publicacion_Origen"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for a in activas:
            w.writerow([
                a["codigo"], a["nombre"], a["nombre_completo"], a["coseguro_consulta"],
                a["coseguro_practica"], a["valor_consulta_raw"], a["valor_base"],
                a["valor_especialista"], a["incluye_iva"], a["observaciones"],
                a["arancel_compensatorio"], a["estado"], "", post_title,
            ])
        for s in sin_atencion:
            w.writerow([
                s["codigo"], s["nombre"], "", "", "", "", "", "", "", "",
                "", s["estado"], s["motivo"], post_title,
            ])


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    info = get_latest_post()
    if not info:
        print("No se encontró ninguna publicación 'Estado de Obras Sociales'.")
        return

    year_month = info["post_date"][:7]
    filename = f"Aranceles_{year_month}.csv"
    filepath = os.path.join(OUTPUT_DIR, filename)

    if os.path.exists(filepath):
        print(f"{filename} ya existe, no hay novedades.")
        return

    pdf_bytes = requests.get(info["pdf_url"], timeout=60).content
    activas, sin_atencion = parse_pdf(pdf_bytes)

    if len(activas) == 0:
        print(f"ERROR: el parser no reconoció ninguna fila activa. Revisar formato del PDF. Post: {info['post_link']}")
        sys.exit(1)

    write_csv(filepath, activas, sin_atencion, info["post_title"])

    with open(os.path.join(OUTPUT_DIR, "latest.txt"), "w", encoding="utf-8") as f:
        f.write(filename)

    with open(os.path.join(OUTPUT_DIR, "latest_meta.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)

    print(f"OK: generado {filename} con {len(activas)} activas y {len(sin_atencion)} sin atencion. Origen: {info['post_title']}")

if __name__ == "__main__":
    main()
